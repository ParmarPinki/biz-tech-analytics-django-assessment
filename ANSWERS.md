# Section 1 Diagnose a broken system

## Assumptions and scope

The assessment describes an incident but does not provide the application,
deployment diff, logs or database. I therefore built a small reproduction. My
chosen root cause is an N+1 query introduced by removing `prefetch_related()`
from a queryset helper during deployment. This is a plausible explanation, not
a confirmed diagnosis of the original incident.

The endpoint returns the logged-in customer's order count, total quantity and
total amount. Each order can have several items. I use SQLite so the reviewer
can run it quickly. The local example demonstrates the query problem; it does
not reproduce the reported 30-second production timeout.

## Investigation log

This is the order I would follow for the reported incident. The measurements in
steps 3 and 4 are from the reproduction I actually ran.

1. **Check the scope and timing.** I would compare deployment time with response
   times and errors, then compare customers with few orders against customers
   with more than 200 orders. I would also check database connection usage, slow
   queries and lock waits. This helps separate a data-size problem from a
   general database outage. I would not assume a 30-second timeout proves N+1.

2. **Check changes outside the view.** An unchanged view can still behave
   differently if its queryset helper, model manager, serializer or middleware
   changes. I would inspect the deployment diff and migrations. For this example,
   I reconstructed a helper change from
   `Order.objects.filter(user=user).prefetch_related("items")` to
   `Order.objects.filter(user=user)`. The summary loop itself stays the same.

3. **Profile the broken request.** I enabled django-silk and sent an authenticated
   request with 250 orders, each containing three item rows. Silk recorded one
   query for the orders and 250 separate item queries. The item queries have the
   same structure, with a different `order_id` each time. That is direct evidence
   of N+1 in this reproduction, rather than just a guess based on response time.

4. **Check the explanation against other causes.** The foreign keys already have
   indexes created by Django. An index can speed up each item lookup, but cannot
   remove 250 separate database calls. This example has no serializer or cache,
   so serializer overhead and cache invalidation cannot explain its regression.
   I also compared 1 and 250 orders in tests: the broken order-query count grows
   from 2 to 251. This supports the N+1 diagnosis. In the actual application I
   would still inspect query plans and any locks before ruling other causes out.

5. **Apply the fix and verify the response.** I restored
   `prefetch_related("items")` in the helper. On the same 250 orders, Silk recorded
   two order queries and the response totals stayed identical. Tests also cover
   no orders, an order without items, and another customer's orders. In a real
   incident I would monitor latency, query counts and memory after rollout.

## Broken code and fix

The relevant code is in `orders/selectors.py` and `orders/views.py`. The broken
behaviour is equivalent to:

```python
orders = Order.objects.filter(user=request.user)
for order in orders:
    for item in order.items.all():
        total_amount += item.unit_price * item.quantity
```

The fix is:

```python
orders = Order.objects.filter(user=request.user).prefetch_related("items")
```

Both versions use the same `build_summary()` function. The fixed endpoint is
`/api/orders/summary/`. A separate broken route is kept only to reproduce and
compare the problem.

## Why the fix works

Django querysets are lazy: building the queryset does not fetch the orders.
Iterating over it runs the order query. Without prefetching, each
`order.items.all()` then makes its own query because the items are a reverse
foreign-key relationship and have not been loaded. For N orders the summary
therefore makes 1 + N queries, even when each lookup uses an index.

With `prefetch_related("items")`, Django fetches the orders first, then fetches
their items with one query using `WHERE order_id IN (...)`. Django groups the
items by order in Python and stores them in each order's prefetch cache. The
existing `order.items.all()` calls use that cached result. With at least one
order, this example uses two queries regardless of the tested order count.
With zero orders, only the order query is needed.

I used `prefetch_related()` because one order has many items.
`select_related()` uses SQL joins for foreign-key/one-to-one relationships such
as an order's single customer; it cannot load the reverse collection here.
Also, changing the loop to `order.items.filter(...)` would issue new queries
instead of using the cached `.all()` result. That would need a matching
`Prefetch` queryset or a different query design.

This removes repeated database round trips and query processing. It does not
make the total amount of work constant: all items still need to be read and
processed, and prefetching holds them in memory. For a much larger history,
database aggregation with `Sum()` and `Count()` would be worth considering,
especially because this response only needs totals. I kept the existing summary
logic to make the cause and fix easy to verify. I would also check consistency
requirements if orders could change between the two SELECTs; prefetch does not
create a single database snapshot by itself.

## Measured evidence

For 250 orders and 750 item rows:

| Measurement | Before | After |
| --- | ---: | ---: |
| Order and item queries | 251 | 2 |
| Total request queries in Silk | 253 | 4 |
| Order count returned | 250 | 250 |
| Quantity returned | 1500 | 1500 |
| Total amount returned | 4687.50 | 4687.50 |

The two extra queries load the session and authenticated user. See
`evidence/SECTION1.md` and the exported Silk records in
`evidence/section1-profile.json`. The export contains actual captured queries,
not manually entered profiler counts. Local timings include profiler overhead
and are not production benchmarks. The 200-order symptom is not a Django limit;
it is where growing work could start hitting a timeout in that environment.

## Tests and reproduction

Run `python manage.py test orders` inside the virtual environment. The tests
check query growth at 1 and 250 orders, unchanged totals, empty results, orders
without items, customer isolation, and authentication. Query-count unit tests
measure the view separately from session and profiler overhead. The
`profile_section1` command exercises the full middleware path and exports Silk
evidence. Setup commands are in `README.md`.

## References

- [Django queryset documentation](https://docs.djangoproject.com/en/5.2/ref/models/querysets/#prefetch-related): relationship loading, caching and prefetch limitations.
- [Django foreign-key documentation](https://docs.djangoproject.com/en/5.2/ref/models/fields/#foreignkey): automatic foreign-key indexes.
- [django-silk documentation](https://github.com/jazzband/django-silk): installation, middleware and SQL inspection.

# Section 2 Design a rate-limited async job queue

The implementation and full trade-offs are in `DESIGN.md`. In simple terms, I
save each requested email as a pending database row and send its ID to Celery.
A Celery worker asks an atomic Redis sliding-window limiter for permission before
calling the provider. The whole system shares one limit, so adding more workers
does not multiply the allowed rate.

Temporary provider errors are retried after 2, 4, 8 and 16 seconds. On the fifth
failure the job moves to the dead-letter state. Permanent errors move there
immediately. If Redis itself is unavailable, the worker fails closed and does
not call the provider because it cannot prove that a rate-limit slot is safe.

## SIGKILL answer

The task uses `acks_late=True`, so Celery acknowledges it only after it returns.
It also uses `task_reject_on_worker_lost=True`. If the worker process is killed
while the task is running, its Redis message remains unacknowledged and becomes
available to another worker after the broker's `visibility_timeout`. The
project sets that timeout to 300 seconds and uses `worker_prefetch_multiplier=1`
so each worker reserves little extra work.

Redelivery means the same task can run more than once. Every email job has a
UUID, and that UUID is sent to the provider as an idempotency key. If a process
is killed after the provider accepts the email but before Django marks it sent,
the replacement worker sends the same key. A supporting provider returns the
previous success rather than sending a duplicate. This is at-least-once task
delivery with an idempotent side effect; claiming exactly-once delivery would be
incorrect.

The database row also protects the publish step. If the Django transaction
rolls back, `transaction.on_commit()` does not enqueue anything. If the broker
is unavailable after a successful commit, the row remains pending and the
scheduled `dispatch_pending` scan republishes it later.

# Section 3 Short answers

## Question A: Django Admin Performance

A primary-key index only helps queries that look up or order by that key. I
would investigate the slow admin page with Django Debug Toolbar locally and the
database slow-query log in production, because several other problems are
common on a table with more than 500,000 rows.

First, fields or methods in `list_display` may access related objects once per
row. For example, displaying `obj.customer.company.name` can create an N+1
query problem. For foreign-key and one-to-one relations, I would set
`list_select_related = ("customer", "customer__company")`. If the page needs a
many-to-many or reverse relation, I would override `get_queryset()` and add an
appropriate `prefetch_related()`. I would also remove any `list_display`
callable that performs a query for every object.

Second, Django Admin can run expensive count queries. The normal changelist may
count the filtered result, and `show_full_result_count = True` asks for another
full-table count. I would set `show_full_result_count = False`. On current
Django versions I would also set `show_facets = admin.ShowFacets.NEVER` when
facet counts are not needed, because facets add count queries for the filters.
A custom estimated-count paginator is another option, but I would only use it
if the team accepts that the displayed total is approximate.

Third, searching, filtering, or sorting can force a large scan or sort. I would
inspect `EXPLAIN` for the actual changelist query, add database indexes that
match the common filter and ordering columns, and use a stable indexed ordering
such as `ordering = ("-created_at", "-pk")`. I would restrict `sortable_by` so
users cannot sort by expensive computed or unindexed fields. I would also avoid
broad `icontains` searches where an exact or prefix search is acceptable; for
example, `search_fields = ("=reference", "^customer__email")`. For a huge
foreign-key selector on an edit form, `autocomplete_fields` prevents Django
from rendering all related rows in one HTML select.

## Question B: Pagination Trade-offs

Offset pagination uses a query similar to `LIMIT 20 OFFSET 100000`. In Django
REST Framework, `LimitOffsetPagination` provides this style. It is easy for a
client to understand and allows direct movement to a known position or page.
The drawback is that the database normally has to find and discard the earlier
rows before returning a deep page, so response time can grow with the offset.
It also behaves poorly while data changes: an insertion or deletion before the
current offset can make the client receive a duplicate item or skip one.

Cursor pagination gives the client an opaque next or previous cursor. In DRF I
would subclass `CursorPagination`, set `page_size`, and use a stable ordering
such as `ordering = ("-created_at", "-id")`. The ordering fields should be
non-null, effectively immutable, and indexed; the unique ID provides a
tie-breaker when multiple records share the same timestamp. The database can
continue from the last position instead of scanning every earlier result, so a
deep page has much more consistent performance. Newer records inserted at the
front also do not shift the position already reached by the user.

For a mobile infinite-scroll feed, I would choose cursor pagination. The UI
only needs next-page navigation, records may be inserted frequently, and users
can scroll far enough for large offsets to become expensive. I would keep the
same filters and ordering for the lifetime of a cursor and avoid ordering by a
field that users can edit. A cursor does not create a database snapshot: an
item can still disappear if it is deleted, and changing an ordering value can
move it.

I would choose offset pagination for a small or mostly static admin/reporting
list where users need page numbers, total counts, or direct jumps to page 50.
Cursor pagination trades those features for safer traversal during inserts and
predictable large-dataset performance. Before choosing either, I would verify
the query plan and create an index that matches both the filters and ordering.

## Section 3 references

- [Django Admin documentation](https://docs.djangoproject.com/en/5.2/ref/contrib/admin/): `list_select_related`, `show_full_result_count`, `show_facets`, `sortable_by`, and `autocomplete_fields`.
- [Django REST Framework pagination documentation](https://www.django-rest-framework.org/api-guide/pagination/): limit/offset and cursor pagination behaviour and requirements.
