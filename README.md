# Full stack developer python Django assessment

All three required sections are implemented. Section 1 reproduces and fixes a
slow order summary. Section 2 adds a rate-limited Celery and Redis email queue.
Section 3 answers the Django Admin Performance and Pagination Trade-offs
questions.
The original assessment document has not been edited.

## Submission overview

| Task | What is included | Main review file |
| --- | --- | --- |
| 1 | N+1 reproduction, `prefetch_related()` fix, Silk profiling and regression tests | `ANSWERS.md` |
| 2 | Database-backed Celery queue, global Redis rate limiter, retries, dead-letter handling and crash recovery | `DESIGN.md` |
| 3 | Short answers for Django Admin Performance and Pagination Trade-offs | `ANSWERS.md` |

The default email provider is a simulation, so reviewing the project does not
require an email account or API key. Local secrets, databases, virtual
environments and runtime output are excluded through `.gitignore`. A safe list
of optional environment variables is provided in `.env.example`.

## Run locally

Use Python 3.12 or newer. These commands are for Windows PowerShell, starting in
the project folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py profile_section1
.\.venv\Scripts\python.exe manage.py test orders email_queue
.\.venv\Scripts\python.exe manage.py runserver
```

Setup should take under five minutes with Python installed and an internet
connection. You can skip the first two commands if this environment is already
set up. On macOS/Linux, use `python3` to create the environment and
`.venv/bin/python` for the remaining commands.

Before submitting a change, run the reproducible checks:

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py test orders email_queue
```

The profiling command creates a dedicated demo user with 250 orders and 750 item
rows, makes authenticated requests through Django and Silk, and writes actual
SQL records to `evidence/section1-profile.json` (with the session key redacted).
It can be run again without
adding duplicate orders. It does not delete existing users, orders, or profiles.

Open [Silk](http://127.0.0.1:8000/silk/) after starting the server. Inspect:

- `/demo/orders/summary/broken/`: 251 order queries.
- `/api/orders/summary/`: 2 order queries.

Silk also records two session/authentication queries per request: its total counts
are 253 and 4 respectively. Request IDs in a newly generated JSON report refer to
the matching entries in your local Silk database. IDs from the committed report
belong to the original measurement; regenerate it for your own database.

To try the API in a browser, give the generated demo user a password:

```powershell
.\.venv\Scripts\python.exe manage.py changepassword assessment-profile-demo
```

Then open [the summary](http://127.0.0.1:8000/api/orders/summary/) and log in with
that username and your chosen password. The demo account starts with no usable
password; the profiling command authenticates it internally through Django's
test client. The same session can access the broken demo route.

## Files to review

- `ANSWERS.md`: investigation order, chosen root cause, explanation and limitations.
- `orders/selectors.py`: broken queryset and the one-line prefetch fix.
- `orders/views.py`: shared response logic and both endpoints.
- `orders/tests.py`: correct totals, customer isolation and query-count regression tests.
- `evidence/SECTION1.md`: readable measurements and sample SQL.
- `evidence/section1-profile.json`: raw SQL evidence exported from django-silk.

All monetary totals use `Decimal` and are returned as strings. `item_count` means
the total quantity purchased, rather than the number of item rows.

The settings are for a local assessment demonstration. Silk records SQL and is
exposed locally for review. The generated session key, database, passwords and
virtual environment are excluded from version control. No real customer data is
used. A fresh database is created by `migrate`; migrations are included.

## Section 2 email queue

Task 2 is in the `email_queue` Django app. Read `DESIGN.md` for the architecture,
trade-offs, rate limiter and worker-crash explanation. The default provider is a
Redis-backed simulation: it records deliveries but sends no real email.

Start Redis on `127.0.0.1:6379`, then open three PowerShell terminals:

```powershell
# Terminal 1: worker
.\.venv\Scripts\python.exe -m celery -A config worker --pool=solo --loglevel=INFO

# Terminal 2: recovery scheduler
.\.venv\Scripts\python.exe -m celery -A config beat --loglevel=INFO

# Terminal 3: submit work and inspect its state
.\.venv\Scripts\python.exe manage.py enqueue_emails --count 100 --retry-first
.\.venv\Scripts\python.exe manage.py queue_status
```

`--pool=solo` is convenient on Windows. Linux and container users can use
Celery's default prefork pool. `dispatch_emails` manually runs the same pending
job recovery scan that Beat schedules every ten seconds.

The included Compose file starts Redis, Django, one worker and Beat. Set a
temporary secret, build, initialize the database, then start the services:

```powershell
$env:DJANGO_SECRET_KEY = 'replace-this-with-a-long-random-development-value'
docker compose build
docker compose run --rm app python manage.py migrate
docker compose up
```

In a separate terminal, submit demo jobs:

```powershell
$env:DJANGO_SECRET_KEY = 'the-same-value-used-above'
docker compose exec app python manage.py enqueue_emails --count 100 --retry-first
docker compose exec app python manage.py queue_status
```

For the required live integration test, start a real Redis server locally and
run:

```powershell
.\.venv\Scripts\python.exe scripts\verify_queue.py
```

The default test uses two real Celery workers, submits 500 jobs, enforces 200
provider attempts in every rolling 60 seconds, checks the intentional retry and
terminates a worker during one task. It normally takes a little over two minutes
because the limiter is real. `--window-seconds 2` is available as a quick smoke
test but is not evidence of the required per-minute behaviour. Results are
written to `evidence/section2-live.json`; temporary worker logs and test data go
under the ignored `.runtime` folder.

The generic HTTP adapter is enabled with `EMAIL_PROVIDER_MODE=http`,
`EMAIL_PROVIDER_URL` and `EMAIL_PROVIDER_API_KEY`. Use a provider that supports
the `Idempotency-Key` header before treating the HTTP mode as production-ready.

## Task 2 files to review

- `DESIGN.md`: architecture choices, alternatives, limiter and SIGKILL answer.
- `email_queue/tasks.py`: Celery delivery, retry and dead-letter logic.
- `email_queue/sliding_window.lua`: atomic global Redis limiter.
- `email_queue/services.py`: database row creation and safe publication.
- `email_queue/tests.py`: fast failure and recovery tests.
- `scripts/verify_queue.py`: live 500-job and crash integration test.
- `evidence/SECTION2.md`: readable results from the verified live run.
- `evidence/section2-live.json`: raw timestamps and machine-readable assertions.

## Task 3 answers

Task 3 requires two short written answers and no additional code. The answers
to Question A (Django Admin Performance) and Question B (Pagination Trade-offs)
are in `ANSWERS.md`. Each stays within the required 200–350 word range and
names the Django or Django REST Framework mechanisms I would use.
