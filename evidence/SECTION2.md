# Section 2 Live Queue Evidence

The live verifier ran against a real Redis server and real Celery worker
subprocesses on October 23, 2026. Celery eager mode was disabled. The detailed
machine-readable output and all 501 simulated provider-call timestamps are in
`section2-live.json`.

## Required 500-job test

| Assertion | Result |
| --- | ---: |
| Jobs submitted | 500 |
| Jobs marked sent | 500 |
| Jobs lost | 0 |
| Distinct provider receipts | 500 |
| Provider calls including one failed attempt | 501 |
| Maximum calls in any rolling 60 seconds | 200 |
| Intentional temporary failure retried | Yes |
| Celery workers | 2 |
| Batch duration | 129.381 seconds |

The first job was configured to fail once. Its second provider attempt occurred
after the two-second exponential-backoff delay. The test counted every simulated
provider call in every rolling 60-second window, rather than checking only fixed
minute boundaries.

Before the batch test, 500 concurrent limiter requests were sent directly to the
same Redis Lua script. Exactly 200 received a permit. This checks that multiple
callers cannot race past the limit.

## Other live checks

- A permanent provider error moved to the `dead` state after one attempt.
- A worker was terminated after the provider accepted an email but before the
  database row was marked sent.
- The expired unacknowledged Redis message was restored through Kombu's
  visibility logic and processed by a replacement Celery worker.
- The final job state was `sent`, while the demo provider recorded exactly one
  delivery for that UUID.

The crash test used a five-second visibility timeout and explicitly triggered
Kombu's expired-message restoration to avoid a platform-dependent periodic scan
delay on Windows. The application default remains 300 seconds. This restoration
does not create a new task or use the database recovery scan; it moves the
original expired broker message back to its queue.

## Validation summary

- Four live integration checks passed in 163.686 seconds.
- Fourteen fast Django tests passed.
- Django's system check reported no issues.
- No model changes are missing from migrations.
- `pip check` reported no broken requirements.

No real email was sent. The demo provider stores synthetic receipts in Redis and
uses the job UUID as its idempotency key.
