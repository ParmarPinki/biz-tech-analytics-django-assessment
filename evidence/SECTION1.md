# Section 1 profiler evidence

These results were exported from actual django-silk request and SQL records by
`python manage.py profile_section1`. The full export is in
`section1-profile.json` (session key redacted). The requests were made through Django's test client,
which executes the real URL routing, session/auth middleware, view and Silk
middleware against the local database. No artificial delay was added.

Dataset: one customer, 250 orders, 750 item rows, 1500 units.

Environment: Python 3.15.0, Django 5.2.18, django-silk 5.6.0, SQLite.

| Captured measurement | Broken | Fixed |
| --- | ---: | ---: |
| Order/item SELECT queries | 251 | 2 |
| All request queries | 253 | 4 |
| Request time including profiling (ms) | 319.411 | 39.120 |
| Sum of captured SQL times (ms) | 57.868 | 3.646 |

These are single local measurements. The before request runs first, so cache
warm-up and profiler startup may affect timings. Query counts and identical
response data are the main evidence; these timings do not establish a production
latency target or reproduce the supplied 30-second incident.

Both responses:

```json
{
  "order_count": 250,
  "item_count": 1500,
  "total_amount": "4687.50"
}
```

## SQL pattern

Before: one order SELECT, followed by a separate query like this for each order:

```sql
SELECT "orders_orderitem"."id", "orders_orderitem"."order_id",
       "orders_orderitem"."quantity", "orders_orderitem"."unit_price"
FROM "orders_orderitem"
WHERE "orders_orderitem"."order_id" = 1;
```

After: one order SELECT and one item SELECT. The ID list below is abbreviated;
the complete captured SQL is in the JSON export.

```sql
SELECT "orders_orderitem"."id", "orders_orderitem"."order_id",
       "orders_orderitem"."quantity", "orders_orderitem"."unit_price"
FROM "orders_orderitem"
WHERE "orders_orderitem"."order_id" IN (1, 2, 3, ...);
```

## View and regenerate the evidence

1. Run migrations and `python manage.py profile_section1`.
2. Run `python manage.py runserver` and open `http://127.0.0.1:8000/silk/`.
3. Select the broken summary request and inspect its repeated item queries.
4. Select `/api/orders/summary/` and inspect its two order/item queries.

Silk shows two additional queries for the session and authenticated user. Its
own recording work is not part of the captured application query count.
Request IDs in the JSON identify the measured requests in that local database;
run the command again to generate IDs for a fresh database. The database itself
is ignored by Git. Only synthetic demo data is used.

## Validation

All five tests pass against a newly created test database. Django's system check
reports no issues, there are no pending model changes, and `pip check` reports
no broken requirements. The Silk and login pages both return HTTP 200.
