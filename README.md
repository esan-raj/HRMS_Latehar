# HR Records

A small administrative HR system for one administrator. It keeps employee service history, related records, and master lists. The browser talks only to this Flask application. It never queries HR tables through the Supabase Data API.

This project did not have the Pratibimba HR source or schema available. The screens follow the requested section list and a practical field baseline. They are not an exact copy of Pratibimba.

## What it does

- Sign-in with email and password through Supabase Auth.
- Access only when the verified JWT subject equals `ADMIN_USER_ID`.
- Dashboard counts from SQL aggregates.
- Employee register with search, filters, pagination, and allowlisted sorting.
- Employee profile sections, each saved on its own.
- Master-data lists for departments, designations, facilities, employment types, qualifications, training types, and leave types.
- Deactivate employees and master records instead of deleting history.
- Audit rows written in the same transaction as the change.

There is no public registration, employee login, payroll, attendance, transfer allocation, notifications, or approval workflow. Allegations, leave, and DACP are recordkeeping only.

## Stack

- Python 3.12, Flask application factory, feature blueprints.
- Jinja templates, Bootstrap 5.3.3 from the CDN, and a small script that copies a present address.
- PostgreSQL through SQLAlchemy and Alembic (Flask-Migrate).
- Waitress as the production WSGI server.

## Assumptions

- Designation and facility filters, and the dashboard breakdowns, use the employee's open posting (`end_date` is null). Employment-type filters use the employment-details row.
- At most one open posting is allowed. Posting dates are inclusive: a new period must start after the previous period ends.
- The current posting is the open posting. It is not stored as a separate flag.
- Phone numbers and PIN codes are text. PIN codes are 4–10 characters.
- DACP stores a stage or level as text. The application does not calculate eligibility.
- Inactive master records stay visible on old rows and are omitted from dropdowns for new selections.
- Employee removal is deactivation. There is no permanent delete.
- Optimistic locking uses a hidden `version` field. A stale save returns the latest stored values and does not overwrite them.

## Local setup

From the project directory:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env`. Every value in `.env.example` is a placeholder. Do not commit `.env`.

Generate a secret of at least 32 characters:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Apply the schema, then start the development server. The development server is for local use only.

```powershell
flask --app wsgi:application db upgrade
flask --app wsgi:application run --debug
```

Open `http://127.0.0.1:5000/login`.

Optional synthetic records. The command refuses to run when any employee already exists.

```powershell
flask --app wsgi:application seed-demo
```

Tests use an in-memory SQLite database and do not need Supabase:

```powershell
pip install -r requirements-dev.txt
pytest
```

## Administrator

Create exactly one user in the Supabase dashboard under Authentication. Disable public sign-ups for the project. Copy that user's UUID into `ADMIN_USER_ID`.

The application compares the verified token subject with that UUID. An email address or a claim sent by the browser is not enough. A valid token for any other user is rejected.

Login errors are generic. Eight failed attempts for the same IP or the same email, within 15 minutes, are throttled before another password request is sent.

## Authentication and cookies

Current Supabase projects sign access tokens with ES256 or RS256. This application:

1. Reads the JWT header only to choose a key.
2. Loads the matching public key from `{SUPABASE_URL}/auth/v1/.well-known/jwks.json`.
3. Verifies the signature, expiry, issuer (`{SUPABASE_URL}/auth/v1`), audience (`authenticated`), and subject.
4. Allows the request only when that subject equals `ADMIN_USER_ID`.

The JWKS client caches keys for 10 minutes, which matches Supabase's edge cache. Restart the process to drop the cache sooner. Leave `SUPABASE_JWT_SECRET` empty for these projects.

Legacy HS256 tokens are checked by calling `GET {SUPABASE_URL}/auth/v1/user`. The subject and audience used for authorization come from that Auth server response, not from an unverified payload. If you intentionally keep a legacy shared secret, set `SUPABASE_JWT_SECRET` and the application verifies HS256 locally instead. Keep that secret on the server only.

Access and refresh tokens are `HttpOnly` cookies (`hrms_access`, `hrms_refresh`) with `SameSite=Lax`. Set `COOKIE_SECURE=1` whenever the site is served over HTTPS. Tokens are not written to `localStorage` or to page scripts.

Login and every state-changing form include a CSRF token. GET requests do not change records. Logout clears both cookies and asks Supabase to revoke the refresh session.

## Database

Use a direct Postgres connection on port 5432 for a long-running Flask process:

`postgresql://hrms_app:PASSWORD@db.PROJECT_REF.supabase.co:5432/postgres?sslmode=require`

Hosts that cannot reach the direct IPv6 address can use the shared session pooler, still on port 5432. The username is then `hrms_app.PROJECT_REF`. Do not use port 6543. The transaction pooler does not support the prepared statements and `search_path` setting this application uses. A URL on port 6543 is rejected at startup.

Non-local Postgres URLs get `sslmode=require` when it is missing. The pool uses `pool_pre_ping`, a default size of 5, overflow of 2, and a recycle time of 240 seconds. Each transaction sets `search_path` to `hrms, public`.

### Private schema

Tables live in the schema `hrms`. Do not add `hrms` to the schemas exposed by the Supabase Data API. The migration also:

- enables row-level security on the HR tables;
- creates a policy that allows only the `hrms_app` role;
- revokes access from `PUBLIC`, `anon`, `authenticated`, and `authenticator`.

The table owner bypasses row-level security. In production, `DATABASE_URL` must be the `hrms_app` role, not `postgres`. The application does not use the Supabase service-role key.

Migrations use `MIGRATION_DATABASE_URL` when the command is `flask db ...`. Point that URL at the direct connection for a role that can create the schema, usually `postgres`.

After `flask db upgrade`, open the Supabase SQL editor as `postgres`, replace the password in `scripts/provision_database.sql`, and run it. That creates `hrms_app` if needed and grants only `SELECT`, `INSERT`, `UPDATE`, and `DELETE` on the HR tables. It does not grant access to `hrms.alembic_version`. Do not commit the file after you put a real password in it.

A live connection to Supabase was not available while this project was built. Follow the steps above before treating the database setup as complete.

## Production

Do not use `flask run` in production. Behind a reverse proxy that terminates TLS:

```powershell
waitress-serve --listen=127.0.0.1:8000 wsgi:application
```

Set `COOKIE_SECURE=1` and `TRUST_PROXY=1`. `GET /health` returns `{"status":"ok"}` or `503` with `{"status":"unavailable"}`. It does not return credentials, SQL errors, or HR data.

## Project layout

- `wsgi.py` — production application object.
- `app/` — factory, models, auth, employees, masters, dashboard, health.
- `migrations/` — Alembic environment and the initial `hrms` revision.
- `scripts/provision_database.sql` — runtime role, grants, and row-level security.
- `tests/` — authentication, CSRF, employees, postings, masters, audit, and directory.
