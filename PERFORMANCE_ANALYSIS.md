# Performance analysis

Measured on 14 October 2026 from this Windows machine against the configured Supabase session pooler (`aws-0-ap-northeast-2`, port 5432). The application process and the database are in different regions.

Sections 1–6 are the baseline, taken before the catalog change. Section 7 is the measurement after loading the field catalog once per request. Section 8 is the measurement after the register no longer loads unused options, incomplete ids, or three separate filter lists. Section 9 is the measurement after table names include the `hrms` schema, so a request no longer sends `SET search_path`.

The live database had **0 employees, 0 facilities, 0 postings, 4 field definitions, and 1 audit row**. Server-side execution of the counted queries was about **0.04 ms**. Page time is network round trips, not row volume. These figures are not a forecast for a large register.

SQLite numbers below are a separate, local query-count check with 50 synthetic employees. They are not Supabase timings.

Authenticated browser sessions were not measured. The administrator password was not used. Successful login, token refresh, and browser paint remain unverified.

Samples are 5 after one discarded warmup unless noted. p95 is omitted because five samples are not enough.

## 1. Measured baseline

### Network building blocks (PostgreSQL and HTTPS)

| Check | Samples | Median | Range | What it includes |
| --- | ---: | ---: | --- | --- |
| New psycopg session, then `SELECT 1` | 2 opens | 3.1 s and 3.7 s | those two opens | TCP, TLS, pooler auth |
| `SELECT 1` on an already open psycopg session | 5 per session, 2 sessions | 276 ms and 312 ms | 251–632 ms | One database round trip |
| SQLAlchemy `engine.connect()` plus `SELECT 1`, first call | 1 | 17.1 s | single observation | Pool startup; not a typical page |
| Later SQLAlchemy `engine.connect()` plus `SELECT 1` | 5 | 1,141 ms | 1,108–1,172 ms | Checkout, pre-ping, and the select |
| `EXPLAIN (ANALYZE)` count of `hrms.employees` | 1 | server execution **0.043 ms** | planning 0.089 ms | Seq scan, 0 rows. The statement still took **242 ms** end to end |
| `EXPLAIN (ANALYZE)` first 20 employees by name | 1 | server execution **0.042 ms** | planning 0.110 ms | Uses `ix_employees_full_name`. End-to-end **287 ms** |
| JWKS `GET` | 5 | 594 ms | 526–1,064 ms | 240 bytes, HTTP 200 |
| Rejected password grant | 5 | 631 ms | 507–1,009 ms | HTTP 400 from Supabase Auth. No application database write |
| Bootstrap 5.3.3 CSS from jsDelivr | 5 | 693 ms | 597–770 ms | 232,803 bytes |
| `GET /login` through the Flask test client | 5 | 0.9 ms | 0.9–7.4 ms | HTML only, 1,769 bytes, no database |
| `GET /` with no cookie | 5 | 0.6 ms | 0.5–0.8 ms | HTTP 302 to login, no database |
| `GET /health` | 5 | 1,292 ms | 1,178–1,555 ms | One database ping |

### Page work on the live database

Views were called in-process with authentication skipped, so this table is **backend work after a request is already allowed**. It is not browser time and not JWT time. “First” is the first sample after the process already held a pool connection. “Warm” is the median of the next 5. Query time is cursor time only.

| Flow | Queries | First wall | Warm wall | Warm query time | Query share of warm wall | HTML bytes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Dashboard | 7 | 2.9 s | 2.9 s (2.8–3.4) | 2.4 s | 81% | 2,989 |
| Employee register | 18 | 8.2 s | 7.6 s (7.0–9.3) | 6.5 s | 85% | 4,679 |
| Register search (`q=a`, sort name) | 18 | 7.0 s | 7.0 s (6.8–7.5) | 6.1 s | 87% | 4,680 |
| Register page 2, sort code | 18 | 9.5 s | 7.2 s (6.7–8.0) | 6.2 s | 86% | 4,679 |
| Incomplete filter | 24 | 9.3 s | 9.5 s (8.6–15.0) | 8.6 s | 90% | 4,728 |
| New-employee form | 15 | 7.4 s | 5.8 s (5.3–6.8) | 4.7 s | 82% | 6,576 |
| Manage fields | 10 | 3.4 s | 4.1 s (3.8–4.2) | 3.4 s | 84% | 10,637 |
| Edit one system field | 7 | 3.1 s | 2.7 s (2.6–3.0) | 2.1 s | 79% | 4,631 |
| Personal edit form | — | — | — | — | — | Not measured. No employee rows |

Search, sorting, and paging did not add queries or meaningful time beyond the register itself. The incomplete filter did: 24 queries and about 9.5 s.

### Same pages on local SQLite, 50 employees

This only shows that the Python and SQL are cheap when the database is local. Wall time was 3–11 ms. Query time was under 1 ms.

| Flow | Queries | Median wall |
| --- | ---: | ---: |
| Dashboard | 6 | 3.5 ms |
| Register | 19 | 10.5 ms |
| Search | 19 | 10.5 ms |
| Personal form, no posting | 14 | 7.0 ms |
| Manage fields | 8 | 4.8 ms |

Postgres register used 18 queries and SQLite 19. The difference is `SET search_path` on Postgres versus one extra field-catalog read on SQLite. The query pattern matches.

## 2. Ranked bottlenecks

### 1. Each database round trip costs about 0.3 s, and pages issue them one after another

- **Where:** `app/config.py` pool options; `app/database.py` `set_search_path`; session pooler in `ap-northeast-2`.
- **Measured:** Server execution 0.04 ms. End-to-end `SELECT 1` on an open session 251–632 ms (median about 280–310 ms). Register cursor time 6.5 s across 18 queries, about 360 ms each. That is 85% of the 7.6 s register.
- **Cause:** The app is far from the pooler. SQLAlchemy holds one connection per request and runs statements serially. `pool_pre_ping` and `SET search_path TO hrms, public` add further round trips on checkout and at transaction start. Opening a session cost 3.1–3.7 s (psycopg) and, once, 17.1 s (first SQLAlchemy connect).
- **Confidence:** Confirmed for this machine and this empty database.

### 2. Field definitions are loaded again and again inside one request

- **Where:** `ensure_system_fields` and `ordered_fields` in `app/fielddefs.py`, called from `employee_directory`, `incomplete_employee_ids`, and `_prepare_employee_form`.
- **Measured:** On the register, **9 of 18** statements were selects from `employee_fields`, plus **2** selects of `field_catalog` id 1. The same 9-field-select pattern appeared on SQLite. `ensure_system_fields` looks up the three system keys one query at a time, and the register calls that path more than once (`employee_directory`, then `incomplete_employee_ids` for the banner count).
- **Cause:** There is no per-request reuse. Each lookup waits on bottleneck 1. Ten repeated catalog statements at the measured ~360 ms each are on the order of 3–4 s of the register. That product is an estimate from the measured statement time and the measured duplicate count. It was not re-tested after a change.
- **Confidence:** Confirmed.

### 3. The incomplete register repeats that work and adds another full id query

- **Where:** `app/employee_routes.py` `index` calls `incomplete_employee_ids()` for the banner, and `employee_directory` calls it again when `incomplete=1`.
- **Measured:** 24 queries, warm median 9.5 s, of which 8.6 s was query time. `employee_fields` was selected **13** times and `field_catalog` **3** times. The incomplete id query ran **twice**.
- **Cause:** Same round-trip cost, more serial statements. The id list is also pulled into Python and then sent back as an `IN (...)` list.
- **Confidence:** Confirmed on the empty table. How large that `IN` list becomes with many incomplete employees is unverified.

### 4. Manage Fields and field edit do one count or JSON scan per check

- **Where:** `missing_value_count` in `app/field_routes.py` `_index`; `stored_value_count` in `_edit` and `delete`.
- **Measured:** Manage Fields ran 10 queries / 4.1 s, including three separate employee counts and one `custom_data ->>` count for the extra required field. Field edit ran `SELECT employees.custom_data FROM employees` even for a system field (7 queries / 2.7 s).
- **Cause:** Required fields are counted one by one. `stored_value_count` and `delete_custom_field` load employee JSON, or every employee row, in Python (`app/fielddefs.py`). At 0 rows this is still a round trip. At a large headcount it becomes a full read. That scale was not measured.
- **Confidence:** Query shape confirmed. Cost at scale unverified.

### 5. Blocking Bootstrap CSS

- **Where:** `app/templates/layout.html` loads Bootstrap from `cdn.jsdelivr.net` in `<head>` before the page can paint. App CSS and `app.js` are local. `app.js` is `defer`. No icon or font CDN.
- **Measured:** CSS download median 693 ms, 233 KB. Login HTML itself was under 1 ms and 1.8 KB. Register HTML was about 4.7 KB. Browser paint and first contentful paint were not measured.
- **Cause:** The stylesheet is render-blocking and comes from a third host.
- **Confidence:** Download time confirmed. Effect on paint unverified.

### 6. Authentication is not on the page-view path, except the first JWKS fetch

- **Where:** `app/auth/guards.py`, `app/auth/jwt_tokens.py`.
- **Measured:** A request with no cookie redirects in 0.6 ms and does not touch the database. A rejected Auth password grant takes about 630 ms. JWKS takes about 590 ms.
- **Cause:** After a valid ES256 or RS256 access token is checked, verification is local. The JWKS client is cached on the process for 10 minutes. HS256 without `SUPABASE_JWT_SECRET` would call `GET /auth/v1/user` on every check. `SUPABASE_JWT_SECRET` is unset, and current Supabase projects sign with asymmetric keys, so that legacy path should not run. Refresh runs only after the access token fails, and it is one Auth HTTP call plus another verify. None of the success, refresh, or JWKS-on-first-real-login paths were timed with a real session.
- **Confidence:** Redirect and rejected grant confirmed. Successful login and refresh unverified. Code inspection says ordinary pages do not call Auth.

### Not the current bottleneck

- Dashboard SQL is six aggregates, not a load of all history. Execution was 0.04 ms. The 2.9 s page is six round trips plus `SET search_path`.
- The register does not load every employee. It counts, then `LIMIT 20`, then postings and employment for that page only. With 0 rows the posting and employment queries were skipped.
- JSONB cast for the one required custom field was one indexed-looking count statement and was not slower than the other counts. No index recommendation: the planner already index-scans `full_name`, and the seq scan of an empty `employees` table executed in 0.04 ms.
- Template rendering of these pages was about 4–10 ms on SQLite. Postgres “time outside cursors” was 0.5–1.0 s and likely includes pool checkout, not Jinja.
- HTML tables are a few kilobytes at this data size.
- Waitress, the Flask debugger, and the reloader were not part of these timings. The views ran in-process. Debug-reloader overhead is unverified.

## 3. Recommended fixes, after review

Items 1 and 2 are now in the application. The repeat measurement is in section 7. Item 3's grouped counts are in section 8. Field deletion and employee-code allocation are in section 9. `pool_pre_ping` is still enabled. Item 5 was not implemented.

| Order | Change | Why | Effort | Correctness |
| --- | --- | --- | --- | --- |
| 1 | Load field catalog and field rows once per request and reuse them | Done. Register `employee_fields` selects went from 9 to 1 | Small | Each request loads the current catalog. The cache is dropped when the request ends or when that request commits a field or employee change |
| 2 | Compute the incomplete-id set once per register request | Done. The incomplete filter’s id query went from 2 to 1 | Small | Same rules, one evaluation. The register banner still needs that one evaluation |
| 3 | Replace per-field `COUNT` queries on Manage Fields with one grouped query, and count stored JSON in SQL instead of `SELECT custom_data` | Done for the list, the stored-value count, and field deletion. See sections 8 and 9. This does not change the register | Small | Counts must stay exact. Deletion removes one JSON key in SQL and bumps the catalog version |
| 4 | Stop calling `pool_pre_ping` on every checkout, or move the app closer to `ap-northeast-2` | This is now the largest remaining cost. Each of the 10 remaining statements still waits on the network | Small for the ping; large for a region move | Pre-ping hides dead pooler connections. If it is removed, failed requests must still reconnect cleanly |
| 5 | Host Bootstrap with the app, or keep using the CDN with a long cache | Saves about 0.7 s of CSS download on a cold cache | Small | CSP already allows `cdn.jsdelivr.net` for styles. A local file needs no CSP change |

Do not add a JSONB index until a non-empty `EXPLAIN` shows a sequential scan that is actually slow. Today the database time is 0.04 ms.

## 4. Tradeoffs

- Collapsing field reads inside one request does not make stale forms succeed. The employee form still compares `fields_version` with `field_catalog.version` on save.
- A process-wide field cache would be wrong unless it is dropped when that version changes. Two app processes would each need to see the new version.
- Skipping incomplete counts would make the register banner lie.
- Removing pre-ping saves a round trip and can surface a dead connection as a failed page instead of a hidden reconnect.
- Login must keep verifying the JWT. Fewer database queries must not skip that check. JWKS caching for 10 minutes is already the intended behavior.

## 5. How this was measured

From `D:\HRMS System`, using `.venv` and the existing `.env` pooler URL:

- psycopg `connect_timeout=15`, then five `SELECT 1` calls on that connection. Repeated for a second connection.
- SQLAlchemy engine from `create_app()`, cursor events around the real view functions, 1 warmup + 5 samples, session rolled back. No inserts.
- `EXPLAIN (ANALYZE, BUFFERS)` only for two read-only selects.
- `urllib` for JWKS, a rejected password grant (`perf-check@example.com`, not a real account), and the Bootstrap CSS. The Auth call did not go through Flask, so it did not write `login_throttle`.
- Flask test client for `/login`, `/`, and `/health`.
- A second process used in-memory SQLite, 50 synthetic employees, then dropped that database.

Temporary probe scripts under `instance/` were removed after the run. They were not part of the application.

## 6. Missing evidence

- No employee rows, so profile, create, and update **writes** were not timed. Create is one transaction (insert, audit, commit). Update is a locked read plus update plus audit. Delete-field loads every employee in Python. Those shapes are from code, not from a clock.
- No posting row, so lazy loads of `current_posting.designation` and `current_posting.facility` (`app/web.py` `current_posting` has no `joinedload`) were not counted. A profile with a posting likely adds two queries. Confidence: likely.
- Successful login, refresh, and the first in-app JWKS fetch during login were not timed.
- Browser navigation, time to first byte in a browser, and paint were not measured.
- Waitress and `flask run --debug` were not measured.
- A register of hundreds or thousands of employees was not measured on the live database. After section 8, the register issues 6 statements and still uses `LIMIT 20`. PostgreSQL completeness was checked with 4 synthetic employees in a transaction that was rolled back. Query **time on the server** may still grow for `next_employee_code` and for deleting a custom field, which loads employee rows in Python.

## 7. Request-scoped field catalog

The field catalog and the incomplete-employee id list are stored on Flask’s `g` for the current request. Active, required, visible, and register subsets are filtered from that list in memory. The same list is used by validation, the register, completeness checks, and templates. Sort and filter look up a custom field from that list instead of selecting it again.

The cache is cleared when the request ends, and also when the request commits or rolls back a change. A later request therefore reads the database again. There is no process-wide or cross-request cache. The register still does not load every employee: it counts, selects one page of 20, and, when the banner or the incomplete filter needs it, selects the incomplete ids once.

Required yes/no values accept `False` (stored as `no`). Required integers accept `0`. Empty and missing values are still incomplete.

### Repeat measurement

Same machine, same session pooler, same empty database: **0 employees, 0 facilities, 0 postings, 4 field definitions**. Views were called in-process with authentication skipped, then the session was rolled back. No rows were inserted. Warm figures are the median of 5 samples after one discarded warmup on a pool that already held a connection. p95 is omitted.

Cold connection, measured separately from the page samples:

| Check | Samples | Result |
| --- | ---: | --- |
| New psycopg session | 2 opens | 2.1 s and 1.6 s |
| `SELECT 1` on those open sessions | 5 per session | medians 305 ms and 260 ms; range 247–646 ms |
| SQLAlchemy `engine.connect()` plus `SELECT 1`, first call after `dispose()` | 1 | 6.9 s |
| Later SQLAlchemy `engine.connect()` plus `SELECT 1` | 5 | median 1.1 s (1.0–1.3 s) |

The register’s first request after `engine.dispose()` took 5.5 s wall and 3.4 s in cursors. That sample includes opening a pool connection. The warm medians below do not. Search, page 2, and the incomplete filter were measured on that already open pool.

| Flow | Before queries | After queries | `employee_fields` selects | `field_catalog` selects | Incomplete id queries | Before warm wall | After warm wall | After warm query time |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Employee register | 18 | 10 | 9 → 1 | 2 → 1 | 1 → 1 | 7.6 s (7.0–9.3) | 3.8 s (3.6–4.1) | 3.2 s (3.0–3.5) |
| Search (`q=a`, sort name) | 18 | 10 | 9 → 1 | 2 → 1 | 1 → 1 | 7.0 s (6.8–7.5) | 4.3 s (3.7–4.4) | 3.6 s (3.2–3.8) |
| Page 2, sort code | 18 | 10 | 9 → 1 | 2 → 1 | 1 → 1 | 7.2 s (6.7–8.0) | 3.7 s (3.4–7.1) | 3.2 s (2.9–6.5) |
| Incomplete filter | 24 | 10 | 13 → 1 | 3 → 1 | 2 → 1 | 9.5 s (8.6–15.0) | 3.5 s (3.4–4.1) | 3.0 s (2.9–3.6) |

HTML sizes were unchanged: register 4,679 bytes, search 4,680, page 2 4,679, incomplete 4,728. Each of the 10 statements is `SET search_path`, one `field_catalog` read, one `employee_fields` read, one `employee_field_options` read (options for the fields just loaded), the incomplete id query, the employee count, the page select, and the three filter lists (designations, facilities, employment types).

Warm cursor time is still about 85% of wall time. Ten serial statements at the measured ~260–300 ms round trip account for the remaining ~3 s. That split is the measured cursor time divided by the measured statement count.

### Tests

`tests/test_field_cache.py` checks that the register, search, page 2, incomplete filter, new-employee form, and Manage Fields each select `employee_fields` and `field_catalog` once. The incomplete id query runs once on the register paths and does not run on the dashboard, the new-employee form, or Manage Fields. A later request sees a help-text edit and a newly added field. A field create and an employee phone update in the same request drop the cache. Required integer `0` and yes/no `False` stay complete; clearing those values makes the employee incomplete again.

The full suite was 39 passed. Section 8 changes that incomplete-id assertion: the register no longer selects those ids.

### Next bottleneck

The remaining register time at this point was still the network round trip. The next measured change is in section 8. `pool_pre_ping` was left on.

## 8. Fewer register statements

### Statement inventory before this change

Traced on the same empty database after the catalog cache, one request each. Durations are that single sample, not a median. `pool_pre_ping` did not appear inside the view: the pool already held a connection. Checkout was measured separately.

| # | Statement | Caller | This sample | Role |
| --- | --- | --- | ---: | --- |
| 1 | `SET search_path TO hrms, public` | `app/database.py` at transaction start | 0.95 s on the register trace, 0.56 s on the incomplete trace | Transaction handling. A `SET` is undone by rollback, so it is applied again. Not an application read |
| 2 | `field_catalog` by id | `ensure_system_fields` | 0.43 s | Catalog version for this request |
| 3 | `employee_fields` ordered | `ordered_fields` | 0.34 s | Field definitions |
| 4 | `employee_field_options` | `selectinload` on every catalog load | 0.28 s | Dropdown choices. The live catalog has no dropdown. Register columns are name, facility, and phone. This read was unused |
| 5 | `COUNT(*)` from `employees` | `employee_directory` | 0.27 s | Page total for pagination |
| 6 | `employees` `LIMIT 20` | `employee_directory` | 0.29 s | The page, with the employee’s facility joined |
| 7 | `SELECT employees.id` where a required value is missing | `incomplete_employee_ids`, then `len()` in the register view | 0.26 s | Banner count. The id list was loaded only to count it. The incomplete filter also put those ids into an `IN (...)` list |
| 8 | `designations` ordered by name | `all_choices` | 0.28 s | Designation filter |
| 9 | `facilities` ordered by name | `all_choices` | 0.27 s | Facility filter |
| 10 | `employment_types` ordered by name | `all_choices` | 0.30 s | Employment-type filter |

Checkout, measured apart from those 10: after `engine.dispose()`, `connect()` took 1.6 s. `SELECT 1` on that open connection took 0.53 s. Five later `connect()` plus `SELECT 1` calls, which include `pool_pre_ping`, had median 1.3 s (1.1–1.6 s). The gap between a later checkout and a statement on an already open connection includes both pool checkout and the pre-ping; those two were not split further. `pool_pre_ping` stays on so a dead pooler connection is not handed to a page. That setting was not changed.

### Changes

- The register banner uses a SQL count. The incomplete filter uses the same missing-value predicate in the page query. Employee ids are not loaded for either.
- The page total and the banner count are one `SELECT` with two sums.
- Designations, facilities, and employment types are one `UNION ALL`.
- Dropdown options load only when a dropdown is rendered. The register does not render one.
- Manage Fields counts every required field in one aggregate. A stored-value check is a SQL count: numeric 0, JSON `false`, and `"no"` count as present; blank and missing do not. This page is not part of the register timings below.
- No cross-request cache. `pool_pre_ping` and `SET search_path` are unchanged. Authentication is unchanged.

### Register timings

Same machine, same pooler, **0 employees** left in the database. Views ran in-process with authentication skipped. Warm figures are the median of 5 samples after one discarded warmup. The register cold sample is the first request after `engine.dispose()`. Search, page 2, and the incomplete filter were then measured on that open pool; their first samples are not cold connects. p95 is omitted. Authentication and browser paint were not measured.

| Flow | Section 7 queries | Now | Section 7 warm wall | Warm wall now | Warm query time now |
| --- | ---: | ---: | ---: | ---: | ---: |
| Employee register | 10 | 6 | 3.8 s (3.6–4.1) | 2.6 s (2.5–2.9) | 2.0 s (1.9–2.3) |
| Search (`q=a`, sort name) | 10 | 6 | 4.3 s (3.7–4.4) | 2.7 s (2.4–3.0) | 2.1 s (1.9–2.5) |
| Page 2, sort code | 10 | 6 | 3.7 s (3.4–7.1) | 2.6 s (2.4–2.9) | 2.0 s (1.8–2.3) |
| Incomplete filter | 10 | 6 | 3.5 s (3.4–4.1) | 2.7 s (2.6–3.7) | 2.1 s (2.0–2.9) |

HTML sizes were unchanged: 4,679, 4,680, 4,679, and 4,728 bytes. The register’s first request after `dispose()` took 4.4 s wall and 2.2 s in cursors. One later warm register sample was `SET search_path` 0.54 s, catalog 0.28 s, fields 0.28 s, the combined count 0.26 s, the page 0.27 s, and the filter-list union 0.25 s.

### Manage Fields, measured separately

This did not produce the register numbers above. The original baseline for this page was 10 queries and 4.1 s warm (section 1), which also included repeated catalog reads later removed in section 7. It was not measured on its own between section 7 and this change. It now runs 4 statements: `SET search_path`, `field_catalog`, `employee_fields`, and one aggregate. Warm median 2.0 s (1.8–2.3), query time 1.3 s (1.3–1.6), HTML 10,637 bytes, the same size as the baseline.

### Tests and dataset

SQLite suite: **40 passed**. The new register test checks that incomplete ids are not selected, that the three filter lists are one statement, and that the register does not read `employee_field_options`. A required integer `0` and yes/no `False` stay off the incomplete page; a blank phone stays on it. Manage Fields reads `employees` once.

PostgreSQL check, not left in the database: 4 synthetic employees, one temporary facility, one temporary required yes/no field, and one archived required field. Integer `0`, JSON `false`, and `"no"` were complete. A missing custom value, a blank phone, and only the non-archived required fields counted. The archived field did not. The transaction was rolled back. Counts after rollback: **0 employees, 4 field definitions**, the same as before the check.

### Remaining bottlenecks

The register’s six statements are still serial round trips of about 0.25–0.30 s, plus `SET search_path` and pool checkout. `pool_pre_ping` was not disabled. Deleting a custom field still loads employee rows in Python to remove one JSON key. `next_employee_code` still reads every employee code when creating an employee. Authentication, token refresh, and browser paint remain unverified. Do not add a JSONB index until a non-empty `EXPLAIN` shows a slow scan. Section 9 is the later measurement of the search-path, deletion, and code-allocation items.

## 9. Schema-qualified SQL, JSON deletion, and code allocation

Measured on 14 October 2026, same machine and same Seoul session pooler, after the changes in this section. Views ran in-process with authentication skipped. Warm figures are the median of 5 samples after one discarded warmup. The register and Manage Fields cold samples are the first request after `engine.dispose()`. Search, page 2, and the incomplete filter stayed on the open pool, so their first samples are not cold connects. p95 is omitted. The database was left at **0 employees, 4 field definitions, 1 audit row, and an employee-code counter of 0**.

### Authenticated browser checks

These checks are pending. No administrator password was available, and the access tokens are signed with keys this application does not hold, so a session was not created. Credentials and tokens were not logged. Browser tooling was not used for a login that cannot be completed.

| Check | Status |
| --- | --- |
| Login | Pending |
| Register with a valid access token | Pending |
| Register when token refresh is required | Pending |
| Employee profile | Pending |
| Employee save | Pending |

Authentication, checkout, SQL, rendering, network, and browser time were therefore not separated for a signed-in session. The page figures below remain backend time after a request is already allowed.

### Changes

- ORM tables use the `hrms` schema. SQLite tests translate that schema away. The per-transaction `SET search_path` listener was removed. `pool_pre_ping` stays on. A connection may still start with `search_path=hrms,public`; the queries do not depend on that setting surviving the pooler.
- Deleting a custom field counts non-empty stored values in SQL, then removes only that key. PostgreSQL uses `jsonb_exists` and the JSONB `-` operator. SQLite uses `json_remove`. The field row, catalog version, audit event, and transaction boundaries are unchanged. A save that still posts the old field is rejected when its `fields_version` is stale, and a later save that has the new catalog version does not copy the deleted key back.
- New employee codes are reserved by locking one counter row in the same transaction as the insert. The create form only previews the next code. A counter that is still zero and already has employees catches up with one `MAX` of `EMP-` numbers; later allocations do not read every code. A manually supplied code is still checked with one equality query, and the unique constraint remains. Existing codes are not renumbered. The migration set the counter to the highest existing `EMP-` number, which is 0 on this empty database.

### Verification

With `search_path` set to `public` inside a rolled-back transaction, the employee count compiled as `hrms.employees` and returned 0.

A second rolled-back transaction inserted one synthetic employee. SQL removed one populated key and one blank key, left a different key in place, and ignored a posted copy of the deleted key. The preview stayed `EMP-0043` without moving the counter. Two allocations under the row lock returned `EMP-0043` and `EMP-0044`. The second allocation did not read every employee code. After rollback the counts matched the start: 0 employees, 4 fields, 1 audit row, counter 0.

SQLite suite: **41 passed**. The added tests cover a stale personal form after field deletion, a later save that still posts the deleted input, and a second code allocation that does not select every employee code.

### Register timings

| Flow | Section 8 statements | Now | Section 8 warm wall | Warm wall now | Warm query time now |
| --- | ---: | ---: | ---: | ---: | ---: |
| Employee register | 6 | 5 | 2.6 s (2.5–2.9) | 2.1 s (2.0–2.2) | 1.6 s (1.5–1.7) |
| Search (`q=a`, sort name) | 6 | 5 | 2.7 s (2.4–3.0) | 2.1 s (2.1–2.5) | 1.6 s (1.5–2.0) |
| Page 2, sort code | 6 | 5 | 2.6 s (2.4–2.9) | 2.1 s (2.0–2.1) | 1.5 s (1.5–1.6) |
| Incomplete filter | 6 | 5 | 2.7 s (2.6–3.7) | 2.1 s (2.1–2.3) | 1.6 s (1.5–1.6) |

HTML sizes were unchanged: 4,679, 4,680, 4,679, and 4,728 bytes. The register’s first request after `dispose()` took 3.8 s wall and 1.8 s in cursors, compared with 4.4 s and 2.2 s in section 8. One later warm register sample was catalog 0.49 s, fields 0.25 s, the combined count 0.24 s, the page 0.24 s, and the filter-list union 0.28 s. None of the samples contained `SET search_path`.

The field-delete and code-allocation changes do not run on these register requests. The register difference is the removed `SET search_path` round trip, about 0.4–0.5 s on this run.

### Manage Fields, measured separately

Cold after `dispose()`: 3.3 s wall, 1.2 s in cursors. Warm median 1.7 s (1.7–1.8), query time 1.2 s (1.1–1.2), 3 statements, HTML 10,637 bytes. Section 8 was 4 statements and 2.0 s warm. The removed statement is `SET search_path`. The grouped employee aggregate was already in place and is not a new register improvement.

### Checkout

`pool_pre_ping` remained enabled. After `dispose()`, `connect()` took 1.6 s and `SELECT 1` on that connection took 0.60 s. Five later checkout-plus-`SELECT 1` calls, which include the pre-ping, had median 0.89 s (0.76–0.96 s). Section 8’s later median was 1.3 s. That gap is a different sample of the same network path, not a change made in this section.

### Remaining limitations

The register still pays five serial round trips to Seoul, plus pool checkout. `pool_pre_ping` was not disabled. The database was not moved. Login, token refresh, employee profile, employee save, and browser paint remain unmeasured. A counter that is still zero catches up by scanning `EMP-` codes once; that path does not run after the counter has moved. Do not add a JSONB index until a non-empty `EXPLAIN` shows a slow scan.
