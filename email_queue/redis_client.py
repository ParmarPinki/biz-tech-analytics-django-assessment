from django.conf import settings
from redis import Redis


def get_redis():
    return Redis.from_url(settings.EMAIL_REDIS_URL, decode_responses=True,
                          socket_connect_timeout=3, socket_timeout=3)


def key(name):
    return f"{settings.EMAIL_QUEUE_NAMESPACE}:{name}"
