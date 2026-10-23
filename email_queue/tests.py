from datetime import timedelta
from unittest.mock import Mock, patch

from celery.exceptions import Retry
from django.db import transaction
from django.test import TestCase
from django.utils import timezone
from kombu.exceptions import OperationalError
from redis.exceptions import ConnectionError

from .models import EmailJob
from .provider import PermanentProviderError, TemporaryProviderError
from .services import enqueue_email
from .tasks import deliver_email, dispatch_pending


class EmailTaskTests(TestCase):
    def setUp(self):
        self.job = EmailJob.objects.create(recipient="customer@example.test", subject="Order", body="Thanks")
        self.client = Mock()
        self.client.lock.return_value.acquire.return_value = True
        self.redis_patch = patch("email_queue.tasks.get_redis", return_value=self.client)
        self.rate_patch = patch("email_queue.tasks.acquire_permit", return_value=(True, 0))
        self.provider_patch = patch("email_queue.tasks.send_email")
        self.retry_patch = patch.object(deliver_email, "retry", side_effect=Retry())
        self.redis_patch.start()
        self.rate = self.rate_patch.start()
        self.provider = self.provider_patch.start()
        self.retry = self.retry_patch.start()
        for item in (self.redis_patch, self.rate_patch, self.provider_patch, self.retry_patch):
            self.addCleanup(item.stop)

    def test_success_and_duplicate_message(self):
        self.assertEqual(deliver_email.run(str(self.job.pk)), "sent")
        self.assertEqual(deliver_email.run(str(self.job.pk)), "sent")
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, EmailJob.Status.SENT)
        self.assertIsNotNone(self.job.sent_at)
        self.assertEqual(self.provider.call_count, 1)

    def test_throttled_job_is_rescheduled_without_spending_failure_budget(self):
        self.rate.return_value = (False, 3.5)
        with self.assertRaises(Retry):
            deliver_email.run(str(self.job.pk))
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, EmailJob.Status.PENDING)
        self.assertEqual(self.job.failure_count, 0)
        self.assertGreater(self.job.next_attempt_at, timezone.now())
        self.assertGreaterEqual(self.retry.call_args.kwargs["countdown"], 3.5)
        self.provider.assert_not_called()

    def test_exponential_backoff_then_dead_letter_after_five_failures(self):
        self.provider.side_effect = TemporaryProviderError("Temporary outage")
        for delay in (2, 4, 8, 16):
            with self.assertRaises(Retry):
                deliver_email.run(str(self.job.pk))
            self.assertEqual(self.retry.call_args.kwargs["countdown"], delay)
            # Move only this test fixture's due date forward to its next attempt.
            EmailJob.objects.filter(pk=self.job.pk).update(next_attempt_at=timezone.now())
        self.assertEqual(deliver_email.run(str(self.job.pk)), "dead")
        self.job.refresh_from_db()
        self.assertEqual(self.job.failure_count, 5)
        self.assertEqual(self.job.status, EmailJob.Status.DEAD)
        self.assertEqual(self.job.last_error, "Temporary outage")
        self.assertEqual(self.retry.call_count, 4)

    def test_permanent_error_is_dead_lettered_immediately(self):
        self.provider.side_effect = PermanentProviderError("Invalid recipient")
        self.assertEqual(deliver_email.run(str(self.job.pk)), "dead")
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, EmailJob.Status.DEAD)
        self.retry.assert_not_called()

    def test_redis_outage_fails_closed(self):
        self.rate.side_effect = ConnectionError("Redis unavailable")
        with self.assertRaises(Retry):
            deliver_email.run(str(self.job.pk))
        self.provider.assert_not_called()
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, EmailJob.Status.PENDING)
        self.assertEqual(self.job.failure_count, 0)
        self.assertEqual(self.retry.call_args.kwargs["countdown"], 5)

    def test_lock_contention_retries_instead_of_losing_the_message(self):
        self.client.lock.return_value.acquire.return_value = False
        with self.assertRaises(Retry):
            deliver_email.run(str(self.job.pk))
        self.provider.assert_not_called()
        self.assertEqual(self.retry.call_args.kwargs["countdown"], 1)


class OutboxTests(TestCase):
    @patch("email_queue.tasks.deliver_email.apply_async", side_effect=OperationalError("Broker unavailable"))
    def test_broker_publish_failure_keeps_a_pending_database_job(self, publish):
        with self.captureOnCommitCallbacks(execute=True):
            job = enqueue_email("customer@example.test", "Order", "Thanks")
        job.refresh_from_db()
        self.assertEqual(job.status, EmailJob.Status.PENDING)
        self.assertIsNone(job.last_published_at)
        publish.assert_called_once()

    @patch("email_queue.services.publish_job")
    def test_database_rollback_does_not_publish_a_job(self, publish):
        with self.captureOnCommitCallbacks(execute=True):
            with self.assertRaises(ValueError):
                with transaction.atomic():
                    enqueue_email("customer@example.test", "Order", "Thanks")
                    raise ValueError("Roll back the business transaction")
        self.assertEqual(EmailJob.objects.count(), 0)
        publish.assert_not_called()

    @patch("email_queue.services.publish_job", return_value=True)
    def test_recovery_only_publishes_due_stale_pending_jobs(self, publish):
        now = timezone.now()
        common = {"recipient": "customer@example.test", "subject": "Order", "body": "Thanks"}
        due = EmailJob.objects.create(**common)
        stale = EmailJob.objects.create(**common, last_published_at=now - timedelta(seconds=100))
        EmailJob.objects.create(**common, last_published_at=now)
        EmailJob.objects.create(**common, next_attempt_at=now + timedelta(minutes=5))
        EmailJob.objects.create(**common, status=EmailJob.Status.DEAD)
        self.assertEqual(dispatch_pending.run(), 2)
        self.assertEqual({call.args[0] for call in publish.call_args_list}, {due.pk, stale.pk})
