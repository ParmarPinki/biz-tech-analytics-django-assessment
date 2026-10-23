"""Local assessment settings. Silk is enabled to inspect the example queries."""
import os
from pathlib import Path

from django.core.management.utils import get_random_secret_key

BASE_DIR = Path(__file__).resolve().parent.parent

# Keep the local session key out of version control and stable between restarts.
if os.environ.get("DJANGO_SECRET_KEY"):
    SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
else:
    key_file = BASE_DIR / ".local-secret-key"
    try:
        with key_file.open("x", encoding="utf-8") as handle:
            handle.write(get_random_secret_key())
    except FileExistsError:
        pass
    SECRET_KEY = key_file.read_text(encoding="utf-8")

DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "silk",
    "orders",
    "email_queue",
]
MIDDLEWARE = [
    "silk.middleware.SilkyMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
    ]},
}]
WSGI_APPLICATION = "config.wsgi.application"
DATABASES = {"default": {
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": os.environ.get("ASSESSMENT_DB_PATH", str(BASE_DIR / "db.sqlite3")),
    "OPTIONS": {"timeout": 30},
}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/api/orders/summary/"

# SQL profiling is sufficient; Python function profiling adds unnecessary noise.
SILKY_PYTHON_PROFILER = False
SILKY_INTERCEPT_PERCENT = 100
SILKY_META = False

# Section 2. One namespace is shared by every worker using this email provider.
EMAIL_REDIS_URL = os.environ.get("EMAIL_REDIS_URL", "redis://127.0.0.1:6379/2")
EMAIL_QUEUE_NAMESPACE = os.environ.get("EMAIL_QUEUE_NAMESPACE", "assessment-emails")
EMAIL_RATE_LIMIT = 200
EMAIL_RATE_WINDOW_SECONDS = float(os.environ.get("EMAIL_RATE_WINDOW_SECONDS", "60"))
# A permit is recorded just before the network call. Keep permits slightly
# longer so scheduling/network delay cannot create 201 actual calls in 60s.
EMAIL_RATE_SAFETY_MILLISECONDS = int(os.environ.get("EMAIL_RATE_SAFETY_MILLISECONDS", "1000"))
EMAIL_MAX_FAILURES = 5
EMAIL_RETRY_BASE_SECONDS = 2
EMAIL_RETRY_CAP_SECONDS = 30
EMAIL_RECOVERY_SECONDS = 90
EMAIL_JOB_LOCK_SECONDS = int(os.environ.get("EMAIL_JOB_LOCK_SECONDS", "30"))
EMAIL_PROVIDER_TIMEOUT_SECONDS = 10
EMAIL_PROVIDER_URL = os.environ.get("EMAIL_PROVIDER_URL", "")
EMAIL_PROVIDER_API_KEY = os.environ.get("EMAIL_PROVIDER_API_KEY", "")
EMAIL_PROVIDER_MODE = os.environ.get("EMAIL_PROVIDER_MODE", "demo")
# Used only by the crash test, in its isolated namespace.
EMAIL_DEMO_PAUSE_JOB = os.environ.get("EMAIL_DEMO_PAUSE_JOB", "")

CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://127.0.0.1:6379/1")
CELERY_TASK_DEFAULT_QUEUE = EMAIL_QUEUE_NAMESPACE
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_WORKER_CANCEL_LONG_RUNNING_TASKS_ON_CONNECTION_LOSS = True
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_VISIBILITY_TIMEOUT = int(os.environ.get("CELERY_VISIBILITY_TIMEOUT", "300"))
CELERY_BROKER_TRANSPORT_OPTIONS = {
    "visibility_timeout": CELERY_VISIBILITY_TIMEOUT,
    "global_keyprefix": EMAIL_QUEUE_NAMESPACE + ":",
    "socket_connect_timeout": 3,
    "socket_timeout": 3,
}
CELERY_RESULT_BACKEND_TRANSPORT_OPTIONS = {
    "visibility_timeout": CELERY_VISIBILITY_TIMEOUT,
    "global_keyprefix": EMAIL_QUEUE_NAMESPACE + ":",
}
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_RESULT_EXPIRES = 3600
CELERY_TASK_PUBLISH_RETRY_POLICY = {
    "max_retries": 2, "interval_start": 0, "interval_step": 0.2, "interval_max": 0.5,
}
CELERY_BEAT_SCHEDULE = {
    "recover-pending-emails": {
        "task": "email_queue.tasks.dispatch_pending",
        "schedule": 10.0,
    },
}
