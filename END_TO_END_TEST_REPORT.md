# End-to-end test report

Date: 15 October 2026, about 22:16–22:40 IST.

Deployment under test: https://hrms-latehar.vercel.app/

This pass used that deployment as it is running. No deployment was published, and no region, environment variable, or project authentication setting was changed. The local production server was not started and was not used for these timings. A local debug server was already open in another browser tab; that tab was not used.

The connected database is production. The first part of this pass sent unauthenticated GET requests. After a manual sign-in, the browser opened the dashboard, employee register, search, filters, pagination, one employee profile, and Manage Fields. No form was saved. No field was archived, deleted, or reordered. No employee was created or deactivated.

## Revision

Local git `HEAD` is `d73a48c298601e76fb71e1a3b628003e4cd0390c` (“Add performance analysis documentation and optimize database interactions.”, 2026-10-15 16:37:08 +0530). The working tree was clean.

Vercel did not expose a git SHA. The Vercel CLI is not installed here, and the GitHub deployments API returned no records for this repository. There is no `vercel.json` in the repo.

Content check against that commit:

- Deployed `/static/js/app.js` is 3178 bytes and matches the `HEAD` blob (SHA-256 prefix `3112c6de9e9a`). It contains the 12-digit Aadhaar message and does not contain a Verhoeff or check-digit check.
- Deployed `/static/css/app.css` is 3474 bytes and matches the `HEAD` blob (SHA-256 prefix `858c0d1e2c04`).
- The login page shows “Sign in” and “Administrator access only.”, posts to the login route, includes a CSRF field, and loads Bootstrap 5.3.3, `/static/css/app.css`, and `/static/js/app.js`. The rendered page includes a per-request CSRF value, so the HTML was not byte-compared with the template.

The static assets on the deployment match the commit being tested. That is a content match, not a platform-signed deployment SHA.

## Production and isolated tests

| Surface | What was done |
| --- | --- |
| https://hrms-latehar.vercel.app/ | Public checks, then authenticated read-only page checks in the browser |
| Vercel database | Not written. Counts stayed at 1 employee, 1 active, 0 inactive |
| Local production server | Not used |
| Earlier local measurements in `PERFORMANCE_ANALYSIS.md` | In-process timings from this machine to the Seoul pooler. They are not Vercel results |
| SQLite unit tests from earlier work | Isolated from this deployment and not re-run in this pass |

## Region

Observed on every sampled Vercel response, including authenticated fetches of `/`, `/employees/new`, the employee profile, and `/fields/`: `x-vercel-id` region pair `bom1::iad1`. Authenticated responses did not include a `Server-Timing` header.

- `bom1` is the Mumbai edge the request passed through.
- `iad1` is the region where the Flask function executed (Washington, D.C., `us-east-1`). That is Vercel’s documented default when a project does not set a function region. This was read from the response header, not from the browser’s location.
- The trailing part of `x-vercel-id` changed on every login sample. That token identifies the request. It does not show whether a function process was reused.

The database host in the local project configuration is `aws-0-ap-northeast-2.pooler.supabase.com` port 5432 (Seoul). Vercel’s environment was not read, so this analysis does not have a separate copy of the deployment’s `DATABASE_URL`. `/health` returned HTTP 200, so the deployed function reached whatever database it is configured to use. `iad1` and `ap-northeast-2` are different regions.

## Timings

These are client-to-Vercel totals. Flask execution, authentication, database checkout, SQL, and template rendering were not visible. Vercel logs were not available, and no diagnostic endpoint was added. Internal timings are unverified.

Python client, no cookies, four samples per path (one first sample, then three later samples):

| Path | First | Later median | Later range | Status |
| --- | ---: | ---: | --- | --- |
| `/login` | 1.308 s | 1.138 s | 0.990–1.368 s | 200 |
| `/` | 1.575 s | 1.333 s | 0.967–1.337 s | 302 |
| `/health` | 4.496 s | 2.009 s | 1.664–2.073 s | 200 |
| `/employees/` | 1.039 s | 1.034 s | 0.977–1.256 s | 302 |
| `/fields/` | 1.056 s | 1.147 s | 1.085–1.155 s | 302 |
| `/static/css/app.css` | 0.903 s | 2.540 s | 1.718–5.059 s | 200 |
| `/static/js/app.js` | 1.921 s | 0.934 s | 0.691–1.986 s | 200 |

Bootstrap from jsDelivr, measured from this machine and not served by Vercel: first 1.936 s, later median 2.311 s.

Browser navigation of `/login` (one sample, HTTP/2, zero redirects): time to first byte 396 ms, DOMContentLoaded 1057 ms, load 1060 ms. `/static/css/app.css` 477 ms and `/static/js/app.js` 485 ms, both status 200. Bootstrap was already cached in that browser (status 200, duration 0).

`/health` is the only sampled route that runs `SELECT 1`. Its first client total was slower than the next three. That gap is not a measured cold start: function initialization and a new database connection were not observed, and an idle wait was not used as evidence. `X-Vercel-Cache: MISS` and `Age: 0` were present on dynamic and static responses. `/login` sends `Cache-Control: no-store`. The static script sends `Cache-Control: no-cache`. A cache miss is not a cold start.

The CSS later samples were slower than the first sample. That is client-total variation, not evidence of initialization.

## Process reuse

From the code that matches the deployed static assets:

- `wsgi.py` builds the Flask app at import, so one process holds one app object.
- `create_app` touches `db.engine`, which creates the SQLAlchemy engine for that process.
- The pool uses `pool_pre_ping`, size 5, overflow 2, recycle 240 seconds, and checkout timeout 30 seconds.
- Table names are schema-qualified to `hrms`. The connection also starts with `search_path=hrms,public`. Correctness does not depend on the pooler keeping that setting.
- The JWKS client is stored on `app.extensions["hrms_jwks"]` with `cache_keys=True`, at most 8 keys, and a 600-second lifespan.
- The field catalog is cleared at the end of each request. It is not a process-wide cache.

A warm Vercel instance can reuse that engine, pool, and JWKS client. A new instance creates them again. This pass did not observe which requests shared an instance, so reuse on the deployment is unverified.

## Redirects, cookies, and assets

Unauthenticated `GET /`, `GET /employees/`, and `GET /fields/` returned 302 to `/login?next=...`. `GET /login` returned 200 with no redirect. The browser loaded the login page over HTTP/2. `Strict-Transport-Security` was present.

The session cookie on `GET /login` had HttpOnly, Path `/`, and SameSite=Lax. The Secure attribute was absent. Sign-in was completed manually in the browser. Cookie values were not read, so the Secure flag on the access and refresh cookies was not observed directly. In code, the same `COOKIE_SECURE` flag controls the session cookie and those auth cookies. Protected pages loaded after sign-in, over HTTPS.

Resource timing on the login page showed Bootstrap, `app.css`, and `app.js` all at status 200. Later authenticated pages also loaded those assets at status 200. A `data:` SVG used by the select control reports status 0 and is not a network failure. An error listener installed for the authenticated navigations recorded no page errors. No browser sessions were cleared.

## Authenticated read-only checks

Sign-in was done manually on https://hrms-latehar.vercel.app/login. The password was not typed or recorded by this check. The browser then stayed on the deployment. These timings are browser navigation totals. `ttfb` is the first byte. `responseEnd` is the last byte of the document. The gap between them is not a split of Flask, authentication, checkout, SQL, or template time. Those internal timings remain unverified.

| Page | First byte | Last byte | Load | Result |
| --- | ---: | ---: | ---: | --- |
| Dashboard just after login | 5.289 s | — | 7.558 s | 1 redirect, then the dashboard |
| Employee register | 0.147 s | 3.634 s | 4.018 s | 1 row |
| Search `EMP-0001` | 0.144 s | 2.220 s | 2.597 s | 1 row |
| Search `zzznomatch` | 0.310 s | 1.845 s | 2.214 s | “No employees match these filters.” |
| Current-facility filter | 0.139 s | 1.783 s | 2.285 s | 0 rows |
| `?page=2` | 0.125 s | 2.115 s | 2.489 s | Shows page 1 of 1 and the one row |
| Status Active | 0.148 s | 2.278 s | 2.753 s | 1 row, page 1 of 1 |
| Personal details | 0.155 s | 1.834 s | 2.259 s | Profile opened |
| Employment | 0.143 s | 3.308 s | 3.660 s | Employment details and job appointments |
| Manage Fields | 0.162 s | 1.453 s | 1.845 s | Four fields listed |
| Add employee, first GET | 0.394 s | 3.225 s | 3.784 s | Form only, not saved |
| Add employee, second GET | 0.155 s | 1.994 s | 2.369 s | Same preview code |
| Dashboard again | 0.142 s | 1.983 s | 2.363 s | Counts unchanged |

Same-origin fetches from the signed-in page, including the response body, also stayed in `bom1::iad1`: dashboard 1.792 s, add-employee form 1.782 s, job-appointments URL 3.256 s, address URL 0.352 s, active register 2.004 s, qualifications master 0.349 s.

What the pages showed:

- Dashboard: 1 total, 1 active, 0 inactive, 1 active without a current posting. Designation and facility charts both say no active employee has a current posting. A later visit showed the same counts.
- Register: one active row, code `EMP-0001`, facility column Sadar Hospital. Pagination reads “1 employees · page 1 of 1”.
- Search by that code returns the row. A search that cannot match returns the empty state.
- Status Inactive returns the empty state. Status Active returns the row.
- The current-facility filter for Sadar Hospital returns no row. The page says that filter uses the current posting, and this employee has no current posting. The Facility column is the employee’s own facility.
- The custom-field filter has no selectable field. Manage Fields shows Aadhar Card as an integer field hidden from the register. The personal form still renders it as a numeric text input with placeholder `1234 5678 9012`, maxlength 20, and the 12-digit hint. Stored personal values were not copied into this report.
- Requesting page 2 does not add a second page. The register clamps the display to page 1 of 1 and keeps the row. The address bar still shows `page=2`.
- Profile sections are Personal Details, Employment, and Current Posting. Address, Education, Training, Promotions, Allegations, Leave, and DACP are not linked. The address URL returns 404. The qualifications master returns 404. The job-appointments URL redirects to Employment.
- Employment shows “Employment details” and “Job appointments”. Current Posting has no end-date field and no posting-history heading, and the profile says there is no current posting. Neither form was saved.
- Manage Fields lists Employee name, Facility, Mobile number, and Aadhar Card. The add-field form was left blank. Archive, delete, and reorder controls were not used.
- The add-employee form says code `EMP-0002` is assigned on save. A second GET showed the same preview. The form was not submitted, and the dashboard still showed 1 employee.

## Required deployment changes

None of these were applied.

1. Set `COOKIE_SECURE=1` so the session, access, and refresh cookies include Secure. Set `TRUST_PROXY=1` at the same time. `COOKIE_SECURE` also turns on `WTF_CSRF_SSL_STRICT`, and Flask needs `ProxyFix` to see the original HTTPS scheme behind Vercel.
2. To shorten database round trips, set the function region to `icn1` (Seoul), next to the Supabase pooler in `ap-northeast-2`. The repo has no region override today, so the function runs in `iad1`.
3. Server timings need Vercel log access or protected instrumentation that is not a public route. No diagnostic endpoint was added.
