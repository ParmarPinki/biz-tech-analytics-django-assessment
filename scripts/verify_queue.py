"""Live integration test: real Redis, real Celery subprocesses, no eager mode.

Run: python scripts/verify_queue.py
Default: 500 jobs, 200 attempts per rolling 60 seconds (about 3 minutes).
Optional --window-seconds 2 is an accelerated smoke test, not a minute-limit proof.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--window-seconds", type=float, default=60)
parser.add_argument("--output", default="evidence/section2-live.json")
OPTIONS = parser.parse_args()
if OPTIONS.window_seconds <= 0:
    parser.error("window-seconds must be positive")
(ROOT / ".runtime").mkdir(exist_ok=True)
RUNTIME = Path(tempfile.mkdtemp(prefix="queue-test-", dir=ROOT / ".runtime"))
NAMESPACE = "assessment-test-" + uuid.uuid4().hex
os.environ.update({
    "DJANGO_SETTINGS_MODULE": "config.settings",
    "ASSESSMENT_DB_PATH": str(RUNTIME / "test.sqlite3"),
    "EMAIL_QUEUE_NAMESPACE": NAMESPACE,
    "EMAIL_RATE_WINDOW_SECONDS": str(OPTIONS.window_seconds),
    "EMAIL_PROVIDER_MODE": "demo",
    "EMAIL_DEMO_PAUSE_JOB": "",
})

import django
django.setup()

from config.celery import app
from django.conf import settings
from django.core.management import call_command
from email_queue.models import EmailJob
from email_queue.rate_limiter import acquire_permit
from email_queue.redis_client import get_redis, key
from email_queue.services import enqueue_email, publish_job
from redis import Redis

REPORT = {"namespace": NAMESPACE, "window_seconds": OPTIONS.window_seconds,
          "limit": settings.EMAIL_RATE_LIMIT, "eager_mode": False, "checks": {}}


def wait_until(predicate, seconds, description):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        threading.Event().wait(0.1)
    raise AssertionError(f"Timed out waiting for {description}. Worker logs: {RUNTIME}")


def maximum_in_window(timestamps, window_ms):
    values = sorted(timestamps)
    start = peak = 0
    for end, timestamp in enumerate(values):
        while values[start] <= timestamp - window_ms:
            start += 1
        peak = max(peak, end - start + 1)
    return peak


class LiveQueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = get_redis()
        cls.client.ping()  # Fail clearly if real Redis is not running.
        call_command("migrate", verbosity=0)
        cls.workers = []
        cls.handles = []

    @classmethod
    def start_worker(cls, name, **environment):
        handle = (RUNTIME / f"{name}.log").open("w", encoding="utf-8")
        process = subprocess.Popen([
            sys.executable, "-m", "celery", "-A", "config", "worker", "--pool=solo",
            "--concurrency=1", "--without-gossip", "--without-mingle", "--without-heartbeat",
            "--loglevel=WARNING", f"--hostname={name}@assessment",
        ], cwd=ROOT, env={**os.environ, **environment}, stdout=handle, stderr=subprocess.STDOUT)
        cls.workers.append((process, name))
        cls.handles.append(handle)
        wait_until(lambda: app.control.ping(destination=[f"{name}@assessment"], timeout=0.5),
                   40, f"worker {name} to start")
        return process

    @classmethod
    def stop_workers(cls):
        for process, name in cls.workers:
            if process.poll() is None:
                app.control.broadcast("shutdown", destination=[f"{name}@assessment"])
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
        cls.workers.clear()

    @classmethod
    def tearDownClass(cls):
        cls.stop_workers()
        for handle in cls.handles:
            handle.close()
        # Delete only keys in this randomly generated test namespace, never FLUSHDB.
        for url in (settings.EMAIL_REDIS_URL, settings.CELERY_BROKER_URL, settings.CELERY_RESULT_BACKEND):
            client = Redis.from_url(url, decode_responses=True)
            for redis_key in client.scan_iter(match=NAMESPACE + ":*"):
                client.delete(redis_key)

    def test_01_atomic_limit_under_concurrent_callers(self):
        with ThreadPoolExecutor(max_workers=24) as pool:
            permits = list(pool.map(lambda _: acquire_permit()[0], range(500)))
        self.assertEqual(sum(permits), 200)
        REPORT["checks"]["atomic_callers"] = {"requests": 500, "admitted": sum(permits)}
        # These were test-only permits; no provider has been called yet.
        self.client.delete(key("rate-window"))

    def test_02_500_jobs_are_delivered_with_retry_and_global_limit(self):
        self.start_worker("batch-one")
        self.start_worker("batch-two")
        started = time.monotonic()
        jobs = [enqueue_email(f"customer{i}@example.test", "Order confirmation", "Thanks",
                              demo_failures=1 if i == 0 else 0) for i in range(500)]
        ids = [job.pk for job in jobs]
        wait_until(lambda: EmailJob.objects.filter(pk__in=ids, status="sent").count() == 500,
                   max(OPTIONS.window_seconds * 4 + 40, 90), "all 500 emails to be sent")
        first = EmailJob.objects.get(pk=ids[0])
        self.assertEqual(first.failure_count, 1)
        self.assertEqual(EmailJob.objects.filter(pk__in=ids).count(), 500)
        self.assertEqual(self.client.hlen(key("demo:receipts")), 500)
        calls = [json.loads(value) for value in self.client.lrange(key("demo:calls"), 0, -1)]
        self.assertEqual(len(calls), 501)  # 500 successful calls plus one intentional failure.
        peak = maximum_in_window([call["time_ms"] for call in calls], OPTIONS.window_seconds * 1000)
        self.assertLessEqual(peak, 200)
        retried = [call for call in calls if call["job_id"] == str(ids[0])]
        self.assertEqual(len(retried), 2)
        self.assertGreaterEqual(retried[1]["time_ms"] - retried[0]["time_ms"], 2000)
        REPORT["checks"]["batch"] = {
            "submitted": 500, "sent": 500, "lost": 0, "provider_calls": len(calls),
            "distinct_receipts": 500, "maximum_calls_in_rolling_window": peak,
            "intentional_failure_retried": True, "workers": 2,
            "duration_seconds": round(time.monotonic() - started, 3),
            "provider_calls_timeline": calls,
        }
        self.stop_workers()

    def test_03_permanent_failure_reaches_dead_letters(self):
        self.start_worker("dead-letter")
        job = enqueue_email("invalid@example.test", "Order", "Thanks", demo_permanent_failure=True)
        wait_until(lambda: EmailJob.objects.filter(pk=job.pk, status="dead").exists(),
                   max(OPTIONS.window_seconds + 20, 30), "permanent failure to be dead-lettered")
        job.refresh_from_db()
        self.assertEqual(job.failure_count, 1)
        self.assertFalse(self.client.hexists(key("demo:receipts"), str(job.pk)))
        REPORT["checks"]["dead_letter"] = {"status": job.status, "failures": job.failure_count}
        self.stop_workers()

    def test_04_killed_worker_redelivers_without_duplicate_email(self):
        job = EmailJob.objects.create(recipient="crash@example.test", subject="Crash recovery", body="Thanks")
        # Shorten visibility only for this isolated crash test. Production stays 300s.
        doomed = self.start_worker("crash-before-ack", EMAIL_DEMO_PAUSE_JOB=str(job.pk),
                                   CELERY_VISIBILITY_TIMEOUT="5", EMAIL_JOB_LOCK_SECONDS="5")
        publish_job(job.pk)
        wait_until(lambda: self.client.get(key("demo:crash-ready")) == str(job.pk),
                   max(OPTIONS.window_seconds + 20, 30), "provider acceptance before acknowledgment")
        job.refresh_from_db()
        self.assertEqual(job.status, "pending")
        self.assertTrue(self.client.hexists(key("demo:receipts"), str(job.pk)))
        broker = Redis.from_url(settings.CELERY_BROKER_URL)
        self.assertGreater(broker.hlen(NAMESPACE + ":unacked"), 0)
        doomed.kill()  # SIGKILL on Linux; TerminateProcess on Windows.
        doomed.wait(timeout=10)
        # Wait until both the broker visibility period and the dead process's
        # short test lock expire. Then invoke Kombu's Redis visibility restore
        # directly. Celery workers run this same operation periodically; doing
        # it here avoids a platform-dependent scan delay in the verifier.
        threading.Event().wait(6)
        transport_options = dict(settings.CELERY_BROKER_TRANSPORT_OPTIONS)
        transport_options["visibility_timeout"] = 5
        with app.connection_for_read(transport_options=transport_options) as connection:
            channel = connection.channel()
            try:
                channel.qos.restore_visible(interval=1)
            finally:
                channel.close()
        self.start_worker("crash-replacement", CELERY_VISIBILITY_TIMEOUT="5",
                          EMAIL_JOB_LOCK_SECONDS="5")
        wait_until(lambda: EmailJob.objects.filter(pk=job.pk, status="sent").exists(),
                   90, "Redis visibility redelivery and idempotent completion")
        self.assertEqual(int(self.client.hget(key("demo:attempts"), str(job.pk))), 1)
        REPORT["checks"]["crash"] = {
            "termination": "SIGKILL" if os.name != "nt" else "TerminateProcess",
            "crash_point": "provider accepted; job pending; message unacknowledged",
            "recovery": "Expired Redis message restored through Kombu visibility logic; no task republish or outbox scan",
            "visibility_restore": "explicitly triggered to avoid platform-dependent periodic scan delay",
            "test_visibility_timeout_seconds": 5, "default_visibility_timeout_seconds": 300,
            "final_status": "sent", "provider_deliveries": 1,
        }


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(LiveQueueTests))
    REPORT["passed"] = result.wasSuccessful()
    REPORT["tests_run"] = result.testsRun
    REPORT["runtime_logs"] = str(RUNTIME)
    output = Path(OPTIONS.output)
    if not output.is_absolute():
        output = ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(REPORT, indent=2) + "\n", encoding="utf-8")
    print(f"Integration evidence: {output}", flush=True)
    sys.exit(0 if result.wasSuccessful() else 1)
