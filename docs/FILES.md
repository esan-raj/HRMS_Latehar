# File guide

This list covers the project files. It skips `.venv`, caches, `.env`, `.env.test`, and the gitignored roster workbook. Those stay on the machine that uses them.

## Root

| File | Contents |
| --- | --- |
| `README.md` | Setup, authentication, database connection, and the original assumptions. |
| `docs/README.md` | Index of these design notes. |
| `docs/WORKFLOWS.md` | Every administrator and command workflow. |
| `docs/DECISIONS.md` | Why the main choices were made. |
| `docs/DATABASE.md` | Schema diagram and column catalog. |
| `docs/FILES.md` | This file. |
| `wsgi.py` | Exposes `application = create_app()` for Waitress and for `flask --app wsgi:application`. |
| `requirements.txt` | Runtime pins: Flask 3.1.3, SQLAlchemy 2.1.4, Alembic 1.20.0, Flask-Migrate, Flask-WTF, WTForms, PyJWT, cryptography, psycopg, python-dotenv, waitress, openpyxl, email-validator. |
| `requirements-dev.txt` | Installs the runtime pins plus pytest 9.1.1. |
| `pytest.ini` | Runs `tests/` with the project root on `pythonpath`. |
| `.gitignore` | Ignores `.env`, `.env.test`, virtualenvs, caches, `instance/`, SQLite files, and the OPD workbook. |
| `.env.example` | Placeholder names for the secret, database URLs, Supabase URL, publishable key, and `ADMIN_USER_ID`. No real credentials. |
| `END_TO_END_TEST_REPORT.md` | Notes from the deployed read-only check: timings, cookie flags, and what was not submitted. |
| `LOCAL_WORKFLOW_REPORT.md` | Notes from the isolated PostgreSQL workflow dataset. The password is not written here. |
| `PERFORMANCE_ANALYSIS.md` | Notes on query shape and where time was spent. |
| `OPD Reporting September.xlsx` | The Latehar duty roster used by `import-opd-roster`. Gitignored. Not a source file. |

## `scripts/`

| File | Contents |
| --- | --- |
| `scripts/provision_database.sql` | Creates `hrms_app` if needed and grants only table DML on `hrms`. The password placeholder is replaced locally and the filled file is not committed. |

## `migrations/`

| File | Contents |
| --- | --- |
| `migrations/alembic.ini` | Alembic script location and log levels. |
| `migrations/env.py` | Creates schema `hrms`, sets statement, lock, and idle timeouts, and stores `alembic_version` in `hrms`. |
| `migrations/versions/0001_initial.py` | Creates every model table, enables row-level security, and revokes the Supabase API roles. Table names are checked before they are interpolated. |
| `migrations/versions/0002_employee_fields.py` | Adds `facility_id` and `custom_data` if missing, and backfills facility from an open posting only when the employee has none. |
| `migrations/versions/0003_employee_code_counters.py` | Creates the employee-code counter if absent and sets `last_value` from the highest `EMP-` code. |
| `migrations/versions/0004_optional_birth_and_gender.py` | Drops `NOT NULL` on date of birth and gender. |
| `migrations/versions/0005_facility_code_counters.py` | Creates the facility-code counter if absent, seeds the highest `FAC-` number, and enables row-level security. |

## `app/` Python

| File | Contents |
| --- | --- |
| `app/__init__.py` | Application factory. Loads config, ProxyFix when `TRUST_PROXY` is set, SQLAlchemy, CSRF, blueprints, SQLite schema translation and foreign keys, error pages, the `show_date` filter, and the field-rule context processor. Imports `record_routes` inside the factory so those routes attach to the employee blueprint. |
| `app/config.py` | Reads `.env`, rejects a short secret, a missing admin UUID, a non-HTTPS Supabase URL, and port 6543. Adds `sslmode=require` for non-local Postgres. Switches to `MIGRATION_DATABASE_URL` when the command is `flask db`. Sets the pool and cookie flags. |
| `app/extensions.py` | `db` with metadata schema `hrms` and a naming convention, plus `migrate` and `csrf`. |
| `app/database.py` | `ping_database` runs `SELECT 1` and rolls back. Used only by health. |
| `app/models.py` | All ORM tables: masters, employees, history, custom fields, audit, both code counters, and login throttle. Also the case-insensitive name indexes and the one-open-posting index. |
| `app/domain.py` | `FieldError`, `StaleFormError`, and `StaleEditError`, each with an HTTP status. |
| `app/persistence.py` | New ids, locked reloads, versioned updates, and commit that turns an integrity error into a field error. |
| `app/audit.py` | `utcnow`, a column snapshot, and `record_audit`. The snapshot is the before/after map stored on the audit row. |
| `app/hr.py` | Employee and facility code allocation, create and update employee, activation, addresses, employment, posting overlap rules, generic child create/update/delete, and the "name already used" and "delete blocked" checks. |
| `app/forms.py` | WTForms for personal details, employment, history sections, masters, and field definitions. Phone, name, PIN, and birth-date validators live here. `FacilityForm` has no code field. |
| `app/choices.py` | Active dropdown choices, with the current inactive selection still shown on an old row. Facility labels include the code. |
| `app/fielddefs.py` | System fields, custom-field CRUD, catalog version, Aadhaar and typed parsers, incomplete-employee predicate, JSON extraction for sort and filter, option retire/delete, and the request-scoped field cache. |
| `app/directory.py` | Register query, filters, allowlisted sort, pagination of 20, export selection, and dashboard aggregates. Facility bars use the employee facility. Posting bars use the open posting. |
| `app/transfers.py` | Employee view facts, Excel and PDF bytes, the employee CSV template, and all-or-nothing employee import. Limits are 2,000 export rows and 200 import rows. |
| `app/master_transfer.py` | The same download and CSV import for the four master lists. Facility codes are allocated at insert time. |
| `app/employee_routes.py` | Register, create, view, personal edit, employment, activation, and the employee export and bulk routes. Profile `/employees/<id>` redirects to the view page. |
| `app/record_routes.py` | Job appointments inside Employment, and the current-posting save. Collection specs for education, training, promotions, allegations, leave, and DACP are defined here. Only appointments are registered as routes. |
| `app/master_routes.py` | The four master screens, their export and bulk routes, and activate, deactivate, and delete. Unknown slugs return 404. |
| `app/field_routes.py` | Field list, create, edit, reorder, archive, restore, delete, and dropdown option routes. |
| `app/web.py` | Loads an employee or returns 404, finds the open posting, and renders the profile shell. `SECTIONS` is View, Personal Details, Employment, and Current Posting. |
| `app/dashboard.py` | `GET /` renders the dashboard from `dashboard_counts`. |
| `app/health.py` | Public `GET /health`. CSRF exempt. Returns ok or 503. |
| `app/seed.py` | Registers `seed-demo`, which inserts fictional people only when the employee table is empty. Also registers the workflow and roster commands. |
| `app/workflow_data.py` | Guard for the isolated test database, `seed-workflow`, `cleanup-workflow`, and `workflow-timing`. Builds 100 `WF-` employees and their history. |
| `app/roster_import.py` | Reads the OPD workbook from XML, refuses a non-Supabase database, and inserts missing doctors without inventing phone, birth date, or gender. |

## `app/auth/`

| File | Contents |
| --- | --- |
| `app/auth/routes.py` | Login form, generic failure messages, throttle check, password grant, cookie issue, and POST logout with refresh revocation. |
| `app/auth/guards.py` | Before-request admin check. Public endpoints are health, login, logout, and static. After-request cookie refresh and security headers, including the content security policy. |
| `app/auth/jwt_tokens.py` | Selects a JWKS key for ES256/RS256, verifies the claims, and uses the Auth user endpoint or a configured HS256 secret for legacy tokens. |
| `app/auth/supabase_api.py` | Password grant, refresh grant, and revoke calls to Supabase Auth. |
| `app/auth/cookies.py` | Names and attributes of `hrms_access` and `hrms_refresh`, including `HttpOnly`, `SameSite=Lax`, and the secure flag. |
| `app/auth/throttle.py` | Eight failures per IP or email in 15 minutes, stored in `login_throttle`. |

## `app/templates/`

| File | Contents |
| --- | --- |
| `layout.html` | Document shell, favicon, Source Sans 3 link, Bootstrap 5.3.3, `app.css`, and `app.js`. |
| `base.html` | Signed-in frame: skip link, mobile menu, Latehar brand, primary navigation, master links, logout form, and the page body. |
| `simple.html` | Centered frame for login and error pages. |
| `macros.html` | Renders a WTForms field with its error, and a CSRF hidden input. |
| `flashes.html` | One Bootstrap alert per flashed message. |
| `confirm.html` | Title, message, hidden version, confirm button, and cancel link. |
| `auth/login.html` | Email, password, and the Latehar mark. The password is not echoed back. |
| `dashboard/index.html` | Count cards, mix bar, five bar charts, and the recent-employee table. |
| `employees/list.html` | Register header, filters, table, and pagination. Export links keep the current filters. |
| `employees/form.html` | Add-employee form. Shows the next code. |
| `employees/profile_base.html` | Name, code, status, current-posting line, deactivate or reactivate, and the section tabs. |
| `employees/view.html` | Read-only personal, custom, employment, and posting facts, plus edit and download buttons. |
| `employees/personal.html` | Personal form, system fields, and custom fields. |
| `employees/employment.html` | Employment-details form and the job-appointment list and form. |
| `employees/postings.html` | Current-posting form. The end date is not shown. |
| `employees/bulk.html` | Sample CSV link, column hints, facility names, and the upload form. |
| `employees/_custom_fields.html` | One control per custom field, including Aadhaar spacing rules. |
| `employees/collection.html` | Generic history table. Used if a collection route is registered. Appointments render inside employment instead. |
| `employees/address.html` | Present and permanent address form. No route renders it. |
| `masters/list.html` | Master table, downloads, and the add form. Facilities show the next code. |
| `masters/form.html` | Edit form. A facility code is shown and is not an input. |
| `masters/bulk.html` | Master sample CSV and upload. |
| `fields/list.html` | Field table with edit, reorder, archive, restore, and delete, plus the add form. |
| `fields/form.html` | Edit label, help, placeholder, section, type limits, and dropdown options. |
| `fields/delete.html` | Asks the administrator to type the label before a field is removed. |
| `errors/400.html` | Form could not be submitted. |
| `errors/403.html` | Signed in as someone other than the administrator. |
| `errors/404.html` | Page not found, with a link to the dashboard. |
| `errors/500.html` | Request could not be completed. No error detail. |

## `app/static/`

| File | Contents |
| --- | --- |
| `app/static/css/app.css` | The records-desk layout: sidebar, cards, tables, filters, bars, badges, and the narrow-screen drawer. |
| `app/static/js/app.js` | Sets the maximum birth date to today, and checks mobile numbers, Aadhaar length, digit fields, and names that must contain a letter before submit. Also copies a present address into the permanent address fields. |
| `app/static/favicon.svg` | Teal square with the letters HR. |

## `tests/`

| File | Contents |
| --- | --- |
| `tests/conftest.py` | In-memory SQLite, foreign keys on, schema created from the models, CSRF off except where a test turns it on, and a helper that sets the admin cookie. |
| `tests/test_auth.py` | Login redirect, expired and invalid tokens, health without secrets, and logout clearing cookies. |
| `tests/test_csrf.py` | A mutating post without the token is rejected. |
| `tests/test_employees.py` | Create and edit rules, mobile and name validation, profile sections, posting overlap, dashboard aggregates, and incomplete records. |
| `tests/test_fields.py` | Custom field types, archive and delete, Aadhaar length, catalog version, and audit contents. |
| `tests/test_field_cache.py` | A field changed in another request is not served from the stale request cache. |
| `tests/test_transfers.py` | Read-only view, Excel and PDF contents, filtered export, CSV template, sample-row rejection, and a failed row inserting nobody. |
| `tests/test_master_transfer.py` | Bulk and download links for the four masters, facility codes assigned in order, and a posted code ignored on edit. |
| `tests/test_workflow_seed.py` | The isolated-database guard, the OPD workbook shape, and the migration lockdown using plain table names. |
