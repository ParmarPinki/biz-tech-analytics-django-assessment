"""A global rolling-window limit, implemented with Redis commands in Lua."""
from pathlib import Path
from uuid import uuid4

from django.conf import settings

from .redis_client import get_redis, key

SCRIPT = (Path(__file__).parent / "sliding_window.lua").read_text(encoding="utf-8")


def acquire_permit():
    """Return (allowed, retry_after_seconds). Redis failure propagates: fail closed."""
    effective_window_ms = (
        int(settings.EMAIL_RATE_WINDOW_SECONDS * 1000)
        + settings.EMAIL_RATE_SAFETY_MILLISECONDS
    )
    result = get_redis().eval(
        SCRIPT, 1, key("rate-window"), settings.EMAIL_RATE_LIMIT,
        effective_window_ms, str(uuid4()),
    )
    return bool(result[0]), max(int(result[1]) / 1000, 0.001)
