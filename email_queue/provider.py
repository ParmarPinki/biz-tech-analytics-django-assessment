"""Demo provider plus a small HTTP adapter; no real emails are sent by default."""
import json
import urllib.error
import urllib.request

from django.conf import settings

from .redis_client import get_redis, key


class TemporaryProviderError(Exception):
    pass


class PermanentProviderError(Exception):
    pass


DEMO_SCRIPT = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
-- A stable job ID acts as the provider's idempotency key.
if redis.call('HEXISTS', KEYS[1], ARGV[1]) == 1 then
    return 'already_sent'
end
local attempt = redis.call('HINCRBY', KEYS[2], ARGV[1], 1)
redis.call('RPUSH', KEYS[3], cjson.encode({job_id=ARGV[1], time_ms=now, attempt=attempt}))
if ARGV[3] == '1' then return 'permanent_failure' end
if attempt <= tonumber(ARGV[2]) then return 'temporary_failure' end
redis.call('HSET', KEYS[1], ARGV[1], now)
return 'sent'
"""


def send_email(job):
    if settings.EMAIL_PROVIDER_MODE == "demo":
        result = get_redis().eval(
            DEMO_SCRIPT, 3, key("demo:receipts"), key("demo:attempts"), key("demo:calls"),
            str(job.pk), job.demo_failures, int(job.demo_permanent_failure),
        )
        if result == "temporary_failure":
            raise TemporaryProviderError("Demo provider: temporary failure")
        if result == "permanent_failure":
            raise PermanentProviderError("Demo provider: invalid recipient")
        return

    if settings.EMAIL_PROVIDER_MODE != "http" or not settings.EMAIL_PROVIDER_URL:
        raise PermanentProviderError("Configure EMAIL_PROVIDER_MODE and EMAIL_PROVIDER_URL")
    payload = json.dumps({"to": job.recipient, "subject": job.subject, "body": job.body}).encode()
    request = urllib.request.Request(settings.EMAIL_PROVIDER_URL, data=payload, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.EMAIL_PROVIDER_API_KEY}",
        "Idempotency-Key": str(job.pk),
    })
    try:
        with urllib.request.urlopen(request, timeout=settings.EMAIL_PROVIDER_TIMEOUT_SECONDS) as response:
            response.read()
    except urllib.error.HTTPError as error:
        # Do not persist response bodies, which can contain credentials or user data.
        if error.code in (408, 429) or error.code >= 500:
            raise TemporaryProviderError(f"Provider HTTP {error.code}") from error
        raise PermanentProviderError(f"Provider HTTP {error.code}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise TemporaryProviderError("Provider network error or timeout") from error
