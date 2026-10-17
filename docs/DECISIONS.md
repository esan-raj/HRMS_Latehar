# Key decisions

Each item is a choice that is still visible in the code, and the reason it was made.

## One administrator, no public registration

The application compares the verified JWT subject with `ADMIN_USER_ID`. An email address, or a claim sent by the browser, is not enough.

This is a single-desk HR register for Latehar. Employee login, self-service, and public sign-up would expose the same records to people who should not change them. Supabase public sign-up is expected to stay disabled in the Supabase project.

## The browser never queries HR tables

Flask is the only client of the `hrms` schema. The Supabase Data API is not given that schema. Tokens are not placed in `localStorage` or in page scripts.

A browser key that can read `hrms` would bypass the administrator check. Keeping the queries on the server means row-level security, the admin check, and CSRF all sit in front of the data.

## HttpOnly cookies and CSRF

Access and refresh tokens are `hrms_access` and `hrms_refresh`, `HttpOnly`, `SameSite=Lax`. Login and every mutating form include a CSRF token. GET does not change records. Logout is POST only.

Cookies keep the token out of JavaScript. `SameSite=Lax` blocks cross-site form posts from other sites. CSRF covers a same-site page that might still be tricked into posting. A GET logout was avoided because a link or a prefetch must not end the session.

## HTTPS cookies and the proxy flag go together

`COOKIE_SECURE=1` marks the cookies `Secure`. `TRUST_PROXY=1` makes Flask trust `X-Forwarded-Proto` from one proxy, which is how a TLS-terminating host such as Vercel tells the app the request was HTTPS.

Setting `Secure` without trusting the proxy makes the app think the request is HTTP and refuse the secure cookie. Setting the proxy flag without `Secure` leaves the session cookie sendable on a plain connection. They are a pair. They are configuration, not a code default, because local `flask run` is HTTP.

## ES256 and RS256 through JWKS, with a narrow HS256 fallback

Current Supabase projects sign access tokens with ES256 or RS256. The app reads the JWT header only to choose a key, loads the matching public key from `{SUPABASE_URL}/auth/v1/.well-known/jwks.json`, and checks signature, expiry, issuer, audience `authenticated`, and subject. Keys are cached for 10 minutes, matching Supabase's edge cache.

`SUPABASE_JWT_SECRET` stays empty for these projects. A shared secret on the server is a larger leak than a public key. Legacy HS256 tokens are checked by calling the Auth user endpoint, and the subject used for authorization comes from that response. A local HS256 check is used only when the secret is deliberately set.

## Private `hrms` schema, row-level security, and a restricted role

Tables live in `hrms`. The initial migration enables row-level security, revokes `PUBLIC`, `anon`, `authenticated`, and `authenticator`, and, when the role exists, grants `hrms_app` only `SELECT`, `INSERT`, `UPDATE`, and `DELETE` plus a policy. `hrms_app` does not receive `hrms.alembic_version`.

The Data API roles must not see HR rows even if a schema setting is wrong later. Migrations use a role that can create the schema, usually `postgres`, through `MIGRATION_DATABASE_URL`. The running app is meant to use `hrms_app`. The table owner bypasses row-level security, so the postgres role used for a migration is not a substitute for the restricted runtime role. The service-role key is not used.

## Port 5432, never 6543

A URL on port 6543 is rejected at startup. Non-local Postgres URLs get `sslmode=require` when it is missing.

Port 6543 is Supabase transaction pooling. Transactions, locks, and session settings do not behave like a session there. The employee-code and facility-code locks need a real transaction. A direct host or the session pooler on 5432 keeps that.

## Do not depend on `search_path`

Every table is named in the `hrms` schema by SQLAlchemy metadata. A connection may still start with `search_path=hrms,public`. Correctness does not depend on that setting surviving the pooler.

Supabase poolers can reset session state between transactions. Schema-qualified names stay correct when that happens. An earlier per-request `SET search_path` was removed for the same reason.

## Pool size

`pool_pre_ping` is on. The default pool is 5, with overflow 2, recycle 240 seconds, and a checkout timeout of 30 seconds.

A small pool matches one admin and a serverless or single-process host. Pre-ping drops dead connections. Recycle avoids using a connection the pooler has already closed.

## Windows cannot use the IPv6-only direct host

The direct Supabase host for this project publishes only an IPv6 address. The working connection from this Windows machine is the IPv4 session pooler on port 5432. The roster import and migrations print the host and the database name, not the URL or the password.

Printing the URL would put the password in the terminal log.

## `load_dotenv()` does not override an existing variable

A shell that already exported `DATABASE_URL` toward the local test database would keep that value if `.env` were loaded without override. Production commands that must hit Supabase load `.env` with override inside the process, or the operator unsets the variables first. The roster command also refuses localhost and `hrms_test`.

## Employee codes are a locked counter

New employees receive `EMP-0001`, `EMP-0002`, and so on. The number comes from `employee_code_counters` row `id = 1`, locked with `FOR UPDATE`. The preview on the form does not increment the counter. Existing codes are not rewritten. When `last_value` is 0, the highest `EMP-` number already stored is used as the start.

Typed codes race and collide. A locked row makes two saves take successive numbers. Leaving old codes alone means a roster import does not rename anyone already in the register.

## Facility codes are a locked counter

New facilities receive `FAC-0001`, `FAC-0002`, and so on, from `facility_code_counters`, with the same lock. The form shows the next code and does not accept a typed code. Bulk upload has no code column. Existing slug codes are not rewritten.

The register already had codes such as `CHC-BALUMATH`. Rewriting them would break anything that quoted those codes. Generating only new codes keeps history stable. Facility names are allowed to repeat because the code is the unique key. Department, designation, and employment-type names stay unique, ignoring case, because those lists have no separate code.

## Date of birth and gender may be empty in the table

Migration `0004` drops `NOT NULL` on `employees.date_of_birth` and `employees.gender`. The personal form and the add-employee form still require both.

The OPD roster has names and facilities, not birth dates or gender. Inventing those values would put false identity data on real people. The database allows the gap. The form closes it when a person later saves the profile. The gender control includes a blank "Select" so a null value is not displayed as Female. Saving that form without an explicit gender is rejected rather than silently writing Female.

## Mobile number, PIN, and name rules

Mobile is exactly 10 digits, starts with 6–9, and rejects all-same digits, `1234567890`, and strict ascending or descending sequences. It is stored as text. PIN codes are text, 6 digits on the address form, with a table check of length 4 to 10. Names are 2 to 200 characters and must include a letter.

Text keeps a leading zero that an integer column would drop. The sequence checks block placeholder numbers that would otherwise look like 10 valid digits. The address screen is not in the navigation, but the column rule remains for rows the seed commands insert.

## Aadhaar is a custom integer-like field, not a check-digit algorithm

A label matching "Aadhaar" or "Aadhar" accepts digits and spaces only, requires 12 digits, and stores the 12 digits. Display inserts spaces. There is no Verhoeff check.

The production field was created as an integer custom field and is hidden on the register. A check digit would reject numbers the office already holds. Storing digits, not the spaced display, keeps one canonical value. Exports include it because the download is for the administrator who asked for the data. The application does not log the value.

## Custom fields live in JSON, keyed by id

Definitions are rows. Values are `employees.custom_data`, a JSON object keyed by the field id, not the label. Adding a field does not change the employee table. Renaming a label or a dropdown choice keeps stored values. A required field added later does not backfill a default.

Altering the employee table for every office-specific column would need a migration per field and would still lose values on rename. JSON keeps the register flexible. The catalog version on the form stops a save from using a definition that changed while the page was open. Sort and filter accept only a known field id and bind it as a parameter, so a query string cannot choose an arbitrary JSON key.

## The employee's facility and the posting's facility are different facts

`employees.facility_id` is the person's own facility. `postings.facility_id` is where that posting was. The register Facility column and the dashboard "Employees by facility" chart use the employee column. Designation filters and "current posting by facility" use the open posting.

The imported doctors have a facility and no posting. Using only the posting would hide them from the facility chart. Linking those bars to the posting filter would show the wrong people, so the bars are not links.

## One open posting is the current posting

A partial unique index allows only one `postings` row per employee with `end_date` null. The profile edits that row. Historical rows are kept and are not edited from the screen.

A boolean "current" flag can drift out of date. The null end date is the current posting. Inclusive dates and the overlap check stop two periods from covering the same day.

## Deactivate employees; delete masters only when nothing points at them

Employees are deactivated. There is no employee delete. Master rows can be deleted when no foreign key still references them. Inactive masters remain visible on old rows and are omitted from new dropdowns.

Deleting an employee would either fail on history or destroy service history. Deactivation keeps the audit trail. A master that is still cited cannot disappear without leaving a broken designation or facility name.

## Optimistic locking

Business rows carry `version`. A save sends the version it loaded. If the stored version differs, the save returns 409 and shows the latest values.

Two open tabs would otherwise overwrite each other. The version check makes the second save visible instead of silent.

## Audit in the same transaction

Creates, updates, deactivations, deletes, and field changes call `record_audit` before commit. The actor is the administrator UUID from the token. Passwords and tokens are not copied into `changes`.

A separate audit write can succeed when the business write fails, or the reverse. One transaction keeps them together.

## Bulk import is all or nothing

Employee and master CSV imports validate every data row, then insert in one commit. A sample row is ignored. Limits are 200 rows and 1 MB, matching `MAX_CONTENT_LENGTH`. Export stops at 2,000 rows.

A partial import is hard to retry safely because codes would already have been consumed and some names would exist. Failing the file leaves the database unchanged. The sample row lets the template be uploaded unchanged without creating "Dr. Sample Name".

## CSV for upload, Excel and PDF for download

Uploads accept only the prescribed CSV. Excel is produced with openpyxl. PDF is a small pure-Python PDF 1.4 writer using Helvetica.

An Excel upload would accept a second, harder-to-check shape. A PDF library that needed Pillow failed to install when the package index timed out, so PDF does not depend on it. Helvetica cannot encode every Unicode character, so those characters become `?` in the PDF while Excel keeps them. Forcing formula-leading cells to text stops a downloaded name from being executed as a formula when the file is reopened.

## The OPD roster is imported, not typed, and identity fields are left blank

`import-opd-roster` reads the workbook with the Office Open XML parts directly. It matches on lower-case name plus facility, creates missing facilities, designations, and employment types, and does not create postings or employment-detail rows. Phone, date of birth, gender, and Aadhaar stay null.

The sheet is a duty roster, not a full personnel file. Inventing a mobile number or a gender would be false data. Idempotent matching lets the command be repeated without duplicating a doctor. The workbook is gitignored because it contains real staff names.

## Synthetic data stays in an isolated database

`seed-workflow` and `cleanup-workflow` require `HRMS_TEST_DATABASE=1`, host `127.0.0.1` or `localhost`, and database name `hrms_test`. `seed-demo` runs only when no employee exists. Tests use in-memory SQLite with foreign keys on. `WTF_CSRF_ENABLED` is false in tests except the CSRF tests.

The production register now holds real doctors. A workflow seed pointed at Supabase would mix fictional people into that register. The guard exists so a copied `DATABASE_URL` cannot do that by accident.

## Screens that were removed stay out of the navigation

The profile shows View, Personal Details, Employment, and Current Posting. Address, education, training, promotions, allegations, leave, and DACP routes are not registered. Qualifications, training types, and leave types are not in the master navigation. The tables and models remain.

Those sections were taken off the product so the desk sees the records it actually keeps. Dropping the tables would destroy rows already stored and would break the demo seed. Leaving the models in place means a later screen can be added without a new migration.

## This is not a copy of another HR product

The section list started from a requested outline. The field set is a practical baseline. Where a source system was not available, the screen was not invented as a replica of it.

Copying an unseen schema would mislabel Latehar's records. The custom-field catalog is how local labels, including Aadhaar, were added without pretending they were universal columns.

## The interface is a small records desk, not a marketing site

The layout is a dark navigation column, a teal accent, white cards, and compact tables. Bootstrap 5.3.3 supplies the grid and form controls. Source Sans 3 is requested from Google Fonts, with Segoe UI as the fallback. The content security policy allows styles from this app and from `cdn.jsdelivr.net` only, so the Google Fonts stylesheet does not load and the fallback font is what the browser uses.

A records desk needs a stable register more than a decorative page. The policy was written to block unexpected scripts and styles. The font request is newer than that policy, and the policy was left strict.

## Tests do not sign in to production

Pytest builds the schema with `db.create_all()` on SQLite. Authenticated tests set the access cookie directly. Browser checks that need the real password are not automated.

A failed password against Supabase Auth counts toward the eight-attempt throttle and can lock the administrator out.
