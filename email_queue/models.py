import uuid

from django.db import models
from django.utils import timezone


class EmailJob(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        DEAD = "dead", "Dead letter"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipient = models.EmailField()
    subject = models.CharField(max_length=200)
    body = models.TextField()
    status = models.CharField(max_length=10, choices=Status, default=Status.PENDING)
    failure_count = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    last_published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    # These two fields inject predictable failures into the local demo provider.
    demo_failures = models.PositiveIntegerField(default=0)
    demo_permanent_failure = models.BooleanField(default=False)

    class Meta:
        indexes = [models.Index(fields=["status", "next_attempt_at"], name="email_pending_due_idx")]
