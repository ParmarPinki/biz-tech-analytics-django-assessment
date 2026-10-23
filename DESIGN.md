# Section 2 Rate-Limited Email Queue Design

## Goal and assumptions

The system must accept a burst of 2,000 email requests, send no more than 200
provider calls in any rolling 60-second window, retry temporary failures, and
recover work after a worker stops. This repository uses a simulated provider by
default so the reviewer can run it without credentials. The HTTP provider
adapter shows where a real provider is called and sends the job UUID as an
idempotency key.

## Architecture choice

I chose **Celery with Redis**, plus an `EmailJob` row in Django's database.
Celery handles background work, delayed retries and worker processes. Redis is
the Celery broker, result backend, distributed lock store and global rate-limit
store. The database row records the durable business state: pending, sent or
dead. Celery Beat runs a recovery scan for pending rows that were not published
or whose last publication became stale.

The request flow is:

1. Save an `EmailJob` as pending in the same database transaction as the
   business action.
2. Use `transaction.on_commit()` to publish its UUID to Celery only after the
   database commit succeeds.
3. A worker locks that UUID, checks the shared Redis limiter, and either retries
   later or calls the provider.
4. Mark success as sent. Retry temporary errors with exponential backoff. Move
   permanent errors, or a fifth temporary failure, to the dead-letter state.

Saving the row before publishing is a small database-outbox pattern. If Redis is
temporarily unavailable, the committed row remains pending and Beat can publish
it later. A plain `task.delay()` after a database commit would leave a gap where
the row exists but the message is lost.

### Options considered

**Celery + Redis** has mature retries, late acknowledgements, scheduling and
worker controls. It also supports several worker processes while all of them use
one Redis limiter. The costs are more moving parts, Redis operations to manage,
and at-least-once delivery, so tasks must be idempotent. It is the best fit here
because the assessment specifically needs rate limiting, retry behaviour and
crash recovery.

**Django Q** has simple Django integration and can use Django's database as a
broker, which can reduce setup for a small application. It has a smaller
ecosystem and I would still need to build the Redis-wide limiter and carefully
verify acknowledgement and crash semantics. It gives little advantage when
Redis is already required.

**A custom queue** could match the exact rules and teach how the parts work, but
I would need to implement message reservation, acknowledgement, visibility
timeouts, retries, scheduling, worker supervision and monitoring. That is too
much reliability-sensitive code for this task. I only implemented the custom
piece the assessment asks for: the Redis rate limiter.

## Sliding-window rate limiter

The limiter uses one Redis sorted set. Each admitted provider attempt is a
unique member, and its score is Redis server time in milliseconds. A Lua script
does four operations as one atomic action:

1. Remove entries at or before the start of the 60-second window with
   `ZREMRANGEBYSCORE`.
2. Read the active count with `ZCARD`.
3. Reject when the count is already 200, returning the wait until the oldest
   entry expires.
4. Otherwise insert the attempt with `ZADD` and set a TTL with `PEXPIRE`.

Lua is used because separate commands could race: two workers could both read
199 and both admit a request. Redis runs the script atomically, so only one is
the 200th permit. Redis `TIME` supplies the clock so workers do not depend on
their local clocks.

The permit is stored one second longer than the requested 60-second window. A
permit is recorded immediately before the network call, and process scheduling
can delay that call by a few milliseconds. Without a small safety margin, calls
from two adjacent permit windows can briefly measure as 201 provider calls in a
rolling minute. The conservative 61-second permit window keeps the provider-side
measurement at or below 200; it sacrifices at most a small amount of throughput.

I chose a sliding window because it directly enforces “no more than 200 in any
60 seconds.” A fixed window is simpler but can allow 200 calls just before a
boundary and 200 just after it. A token bucket is good when controlled bursts
are acceptable, but a full 200-token bucket could also exceed a strict rolling
window after tokens refill. The sorted set uses more memory and does more work
than a counter, but it contains at most 200 active entries for this provider.

If Redis is unavailable, the task **fails closed**: it does not call the email
provider and retries five seconds later. This protects the provider limit but
delays emails during a Redis outage. A fail-open design would improve
availability but could violate the provider contract across multiple workers.

The worker uses Celery retry countdowns; it never calls `time.sleep()`. A
throttled task is returned to Celery for later execution, leaving the worker
free to process another task.

## Failure and retry behaviour

Temporary provider failures such as HTTP 429, HTTP 5xx and network timeouts use
delays of 2, 4, 8 and 16 seconds. A fifth failure moves the row to `dead`.
Permanent 4xx errors move there immediately. `queue_status` lists dead letters,
which can be inspected and requeued only after the data or provider problem is
fixed. Rate-limit waits and infrastructure failures do not spend the provider
failure budget.

Each job UUID is used as the provider idempotency key. The demo provider records
that key atomically and returns success for repeated delivery without sending a
second email. A real provider must support the same idempotency behaviour. This
matters because Celery and Redis provide at-least-once delivery, not exactly
once.

## What happens after SIGKILL

`acks_late=True` makes the worker acknowledge a message after the task finishes.
`task_reject_on_worker_lost=True` requests redelivery when the child process is
killed. `worker_prefetch_multiplier=1` limits the amount of reserved work, and
Redis's 300-second `visibility_timeout` makes an unacknowledged message visible
again if the connection disappears.

There are two important crash points:

- If the process dies before the provider accepts the request, the message is
  redelivered and the email is attempted later.
- If it dies after provider acceptance but before `EmailJob` is marked sent,
  Redis redelivers it. The repeated provider request has the same idempotency
  key, so it completes the database state without sending a duplicate email.

The short Redis job lock prevents two workers from processing the same UUID at
the same time. Lock contention causes a Celery retry, so it does not discard the
recovered message. The outbox scan is a second recovery path if publication
itself fails. This design therefore prefers a possible duplicate task message
over a lost email, while provider idempotency prevents a duplicate side effect.

The live crash verifier uses a five-second visibility timeout and explicitly
invokes Kombu's expired-message restoration after that timeout. Celery workers
run the same restoration operation periodically; calling it directly keeps the
test duration deterministic on Windows. A replacement worker still consumes the
restored Celery message and completes the job. The normal configuration keeps a
300-second visibility timeout.

## Test strategy and limitations

Fast tests mock Redis and the provider to check backoff, dead letters, fail-closed
behaviour, duplicate task messages and outbox recovery. The live verifier uses
a real Redis server and real Celery worker subprocesses. It submits 500 jobs to
two workers, injects one temporary failure, checks all 500 database rows and
provider receipts, and calculates the maximum number of calls in every rolling
window. It also terminates a worker after provider acceptance and checks
redelivery and idempotent completion.

SQLite is used only to keep the assessment runnable in a few minutes. A
production deployment should use PostgreSQL, managed Redis with authentication
and high availability, provider-specific request/response handling, metrics and
alerts for pending age, retries and dead letters. The database-outbox publisher
uses polling rather than database notifications, so recovery can be delayed by
the configured scan interval.

## References

- [Celery task acknowledgement and idempotency](https://docs.celeryq.dev/en/stable/userguide/tasks.html)
- [Celery Redis visibility timeout](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html)
- [Redis Lua atomic execution](https://redis.io/docs/latest/develop/programmability/eval-intro/)
