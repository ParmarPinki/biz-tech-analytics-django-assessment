import logging
import threading
from datetime import timedelta
from random import uniform

from celery import shared_task
from django.conf import settings
from django.db import OperationalError
from django.db.models import Q
from django.utils import timezone
from redis.exceptions import LockNotOwnedError, RedisError

from .models import EmailJob
from .provider import PermanentProviderError, TemporaryProviderError, send_email
from .rate_limiter import acquire_permit
from .redis_client import get_redis, key

logger = logging.getLogger(__name__)


def retry_delay(failure_count):
    return min(settings.EMAIL_RETRY_BASE_SECONDS * 2 ** (failure_count - 1),
               settings.EMAIL_RETRY_CAP_SECONDS)


def defer(task, job_id, seconds):
    # Persist the due date before asking Celery to publish a replacement message.
    # A later recovery scan can rescue the row if that publication fails.
    EmailJob.objects.filter(pk=job_id, status=EmailJob.Status.PENDING).update(
        next_attempt_at=timezone.now() + timedelta(seconds=seconds),
        last_published_at=timezone.now(),
    )
    raise task.retry(countdown=seconds, max_retries=None)


@shared_task(bind=True, acks_late=True, reject_on_worker_lost=True, max_retries=None)
def deliver_email(self, job_id):
    client = get_redis()
    # Shorter than recovery/visibility timeout, longer than the provider timeout.
    lock = client.lock(
        key(f"job-lock:{job_id}"),
        timeout=settings.EMAIL_JOB_LOCK_SECONDS,
        blocking=False,
    )
    locked = False
    try:
        locked = lock.acquire()
        if not locked:
            # Keep a delivery alive even if the original owner crashed and its
            # lock has not expired yet. Never drop the only recovered message.
            raise self.retry(countdown=1, max_retries=None)
        job = EmailJob.objects.get(pk=job_id)
        if job.status != EmailJob.Status.PENDING:
            return job.status
        remaining = (job.next_attempt_at - timezone.now()).total_seconds()
        if remaining > 0:
            raise self.retry(countdown=remaining, max_retries=None)

        allowed, wait = acquire_permit()
        if not allowed:
            # Retry schedules work for later; it does not block a worker with sleep.
            defer(self, job_id, wait + uniform(0.01, 0.25))

        try:
            send_email(job)
        except PermanentProviderError as error:
            EmailJob.objects.filter(pk=job_id).update(
                status=EmailJob.Status.DEAD, failure_count=job.failure_count + 1,
                last_error=str(error),
            )
            logger.warning("Email %s moved to dead letters: %s", job_id, error)
            return "dead"
        except TemporaryProviderError as error:
            failures = job.failure_count + 1
            EmailJob.objects.filter(pk=job_id).update(failure_count=failures, last_error=str(error))
            if failures >= settings.EMAIL_MAX_FAILURES:
                EmailJob.objects.filter(pk=job_id).update(status=EmailJob.Status.DEAD)
                return "dead"
            defer(self, job_id, retry_delay(failures))

        # Test-only crash point: the provider accepted the email, but our row is
        # still pending. The verifier kills this process then starts a replacement.
        if settings.EMAIL_PROVIDER_MODE == "demo" and settings.EMAIL_DEMO_PAUSE_JOB == job_id:
            client.set(key("demo:crash-ready"), job_id, ex=120)
            threading.Event().wait(120)

        EmailJob.objects.filter(pk=job_id).update(
            status=EmailJob.Status.SENT, sent_at=timezone.now(), last_error="",
        )
        return "sent"
    except (RedisError, OperationalError) as error:
        # No provider call is allowed when Redis cannot grant a permit.
        # Infrastructure failure does not use up the provider failure budget.
        logger.warning("Email %s deferred after %s", job_id, type(error).__name__)
        raise self.retry(countdown=5, max_retries=None)
    finally:
        if locked:
            try:
                lock.release()
            except (LockNotOwnedError, RedisError):
                # The lock can expire after a crash or a long process pause.
                logger.warning("Email lock expired or Redis unavailable: %s", job_id)


@shared_task
def dispatch_pending():
    """Database outbox recovery: republish due jobs whose last publish is stale."""
    from .services import publish_job

    now = timezone.now()
    stale = now - timedelta(seconds=settings.EMAIL_RECOVERY_SECONDS)
    due = EmailJob.objects.filter(status=EmailJob.Status.PENDING, next_attempt_at__lte=now).filter(
        Q(last_published_at__isnull=True) | Q(last_published_at__lt=stale)
    )
    published = 0
    for job_id in due.values_list("pk", flat=True)[:2000]:
        # Stop when the broker is unavailable, rather than repeat 2000 failures.
        if not publish_job(job_id):
            break
        published += 1
    return published
