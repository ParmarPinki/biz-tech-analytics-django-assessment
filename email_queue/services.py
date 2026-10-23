import logging

from django.db import transaction
from django.utils import timezone
from kombu.exceptions import OperationalError
from redis.exceptions import RedisError

from .models import EmailJob

logger = logging.getLogger(__name__)


def publish_job(job_id):
    """The database row remains pending even if publishing to Redis fails."""
    from .tasks import deliver_email

    try:
        deliver_email.apply_async(args=[str(job_id)])
    except (OperationalError, RedisError, OSError):
        logger.warning("Broker unavailable; email %s remains pending for recovery", job_id)
        return False
    EmailJob.objects.filter(pk=job_id, status=EmailJob.Status.PENDING).update(
        last_published_at=timezone.now()
    )
    return True


def enqueue_email(recipient, subject, body, *, demo_failures=0, demo_permanent_failure=False):
    job = EmailJob(recipient=recipient, subject=subject, body=body,
                   demo_failures=demo_failures, demo_permanent_failure=demo_permanent_failure)
    job.full_clean()
    with transaction.atomic():
        job.save()
        transaction.on_commit(lambda: publish_job(job.pk))
    return job
