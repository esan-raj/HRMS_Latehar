# Local workflow report

Date: 15 October 2026.

The local application is http://127.0.0.1:5001/

It uses PostgreSQL database `hrms_test` on `127.0.0.1:54329`. That process does not use the production database. The production site https://hrms-latehar.vercel.app/ was not changed. A count check after the browser session still showed 100 employees in `hrms_test` and no `EMP-0001` row.

Authentication is still the existing Supabase project from `.env`. There is no separate test Auth project, so a dedicated test administrator was not created. JWT validation was not disabled.

## Start and stop

PostgreSQL is the Docker container `hrms-test-pg`. The database password is only in `.env.test`, which is gitignored.

```powershell
docker start hrms-test-pg
$env:HRMS_TEST_DATABASE = "1"
$env:DATABASE_URL = (Get-Content .env.test | Where-Object { $_ -like "DATABASE_URL=*" }).Split("=",2)[1]
$env:MIGRATION_DATABASE_URL = $env:DATABASE_URL
flask --app wsgi:application db upgrade
flask --app wsgi:application seed-workflow
flask --app wsgi:application run --port 5001
```

`seed-workflow` loads the dataset once. Running it again prints that the 100 employees are already present. `cleanup-workflow` deletes only `WF-` employees, `WF ` masters, and `WF ` custom fields. It was not run.

Before either command writes, it requires `HRMS_TEST_DATABASE=1`, host `127.0.0.1` or `localhost`, and database name `hrms_test`. A Supabase host is refused.

## Dataset

100 synthetic employees, codes `WF-0001` through `WF-0100`.

| Check | Result |
| --- | ---: |
| Active | 80 |
| Inactive | 20 |
| Missing phone, legacy fixture `WF-0098` | 1 |
| Missing facility, legacy fixture `WF-0099` | 1 |
| Open postings | 78 |
| Open posting facility different from the employee facility | 12 |
| Active without a current posting, dashboard | 18 |

Names include a space, an apostrophe, a hyphen, Hindi characters, three people who share “WF Shared Name” with different codes, and a 200-character name. Optional email is empty on even-numbered rows.

Custom fields cover short text, long text, integer, decimal, date, dropdown, and yes/no. WF Shift is required and visible. WF Biography and WF Review date are hidden from the register. WF Archived motto is archived. WF Empty shelf has no stored values. Integer 0, integer -3, decimal 0, decimal -2.5, and yes/no “no” are stored. Dropdown options include Morning, Evening, retired Night on older rows, and unused “Unused swing”. `WF-0095` omits the required key, `WF-0096` stores JSON null, and `WF-0097` stores a blank string. `WF-0100` has the orphan key `orphan_not_a_field`.

Leading zeros and spaced mobile numbers are rejected by the current form, so they were not inserted.

Masters include used and unused facilities, departments, designations, employment types, qualifications, training types, and leave types, with active and inactive rows. `WF-0011` has address, education, training, appointment, employment, past and current postings, promotion, allegation, leave, and DACP rows. Overlapping postings were not inserted.

## What was executed

### Automated tests, SQLite, mocked JWT

`pytest` completed with 44 passed. An additional test that the initial migration locks down unqualified table names passed. These tests are not the local PostgreSQL browser session.

Covered there, and not repeated as browser proof: unauthenticated redirect, invalid and expired tokens, CSRF, login throttle, stale employee and field saves, audit rows, duplicate-code protection, overlap and a second open posting, required system fields, each custom type, dropdown retirement, archive and restore, permanent field deletion, referenced-master deletion, search, sort, pagination, and 0 / “no” counting as present.

The new phone cases reject `0986712345` and `9867 123450`. The workflow seed test builds all 100 rows on SQLite, checks the legacy fixtures, runs a second time without duplicating them, and checks that cleanup leaves a non-workflow employee in place.

### In-process timings, local PostgreSQL, authentication skipped

These are not browser timings and not Supabase network timings. Warm median is the middle of the three samples after the first.

| Work | Statements | Warm median | Range |
| --- | ---: | ---: | --- |
| Dashboard render | 6 | 0.033 s | 0.028–0.034 s |
| Register render | 9 | 0.081 s | 0.080–0.096 s |
| Search render | 9 | 0.056 s | 0.051–0.068 s |
| Status-filter render | 9 | 0.063 s | 0.057–0.069 s |
| Manage Fields render | 3 | 0.020 s | 0.019–0.022 s |
| Register query only | 8 | 0.041 s | 0.039–0.045 s |
| Profile queries | 3 | 0.009 s | 0.009–0.010 s |
| One create | — | 0.021 s once | — |
| One save | — | 0.043 s once | — |

The timing employee was removed. The dataset count returned to 100.

### Real browser on http://127.0.0.1:5001/

The signed-in session was the existing administrator. The dashboard showed 100 total, 80 active, 20 inactive, and 18 active without a current posting. That matches the test database.

| Check | Status | Evidence |
| --- | --- | --- |
| Dashboard counts | Pass | Counts above |
| Register, 20 rows, page 1 of 5 | Pass | “100 employees · page 1 of 5” |
| Page 2 | Pass | Response contained “page 2 of 5” |
| Search `O'Brien` | Pass | One row; the apostrophe is escaped in HTML |
| Search with no match | Pass | “No employees match these filters.” |
| Inactive filter | Pass | 20 Reactivate actions on that page |
| Incomplete link | Pass | “5 employees are missing a required value” |
| Custom columns and long dropdowns | Pass | WF fields and WF masters are in the filters |
| Personal, Employment, Current Posting | Pass | Those three links are present |
| Address route | Pass as current behavior | Authenticated GET returned 404 |
| Qualifications master | Pass as current behavior | Authenticated GET returned 404 |
| Job appointments URL | Pass | Redirects to Employment, which contains “Job appointments” |
| Add-employee form | Pass | Preview `EMP-0001`; retired Night is absent; inactive facilities are absent |
| Leading-zero mobile in the browser | Pass | The field kept `0986712345` and the browser blocked submit: “Please match the requested format.” |
| Departments, designations, facilities, employment types | Pass | Each returned HTTP 200 |
| Manage Fields | Pass | HTTP 200 in 108 ms |
| Unauthenticated protected routes | Pass | `/`, `/employees/`, `/fields/`, and `/employees/new` return 302 to login without a cookie |
| Login page and `/health` | Pass | HTTP 200 |

Register navigation in the browser: first byte 391 ms, last byte 392 ms, load 773 ms. No page error was recorded on that navigation. Later same-origin reads of the register were about 46–81 ms. Desktop width was the width of the browser tab. A narrow-screen pass was not done.

## Not completed in the real browser

These were not claimed as passed.

- Creating, editing, deactivating, and reactivating an employee.
- Saving 0 and “no” through the form. The form was filled, then the wrong form was submitted.
- Master create, edit, deactivate, reactivate, and deletion.
- Custom-field create, rename, reorder, required toggle, column visibility, archive, restore, and deletion.
- Dropdown rename and option retirement from the screen.
- Stale-tab edits, duplicate submits, and overlap rejection from the screen.
- Audit rows for a browser mutation.
- Console and layout checks on a narrow viewport.
- Invalid login. It was not submitted, because a failed password would hit the existing Supabase project and can throttle the account. The mocked tests cover it.

While trying to save the new employee, the script submitted the logout form instead of the employee form. That cleared the localhost session used by both port 5000 and port 5001. The Vercel dashboard was still signed in afterward and still showed 1 employee. No test employee was created. Sign in again at http://127.0.0.1:5001/login to continue. Do not use that login for the production database; port 5001 is the test database.

## Fixes

A fresh local database could not finish `flask db upgrade`. Revision `0001` treated schema-qualified metadata keys such as `hrms.employees` as table names and stopped. It now uses the unqualified table name. Revision `0003` now creates `employee_code_counters` only when that table is not already present, because `0001` creates the current model set. Both changes are for new databases. The already-applied production migration was not rerun.

No application behavior bug was fixed from the browser session. The address and qualifications 404s match the current screens: the profile shows personal details, employment, and the current posting, and the sidebar masters are departments, designations, facilities, and employment types. The history tables are still seeded.

## Limitations

- Local PostgreSQL timings are not comparable to the Vercel-to-Seoul timings.
- Browser work used the existing Supabase administrator, not a separate test user.
- Cleanup is available and was not run, so the dataset remains for review.
- The development server on port 5001 is still running and is currently signed out.
