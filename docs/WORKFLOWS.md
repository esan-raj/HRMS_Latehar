# Workflows

One administrator uses this application. The browser talks only to Flask. It never queries the HR tables through the Supabase Data API. A GET request never changes a record. Every state-changing form carries a CSRF token.

Public routes are `GET /health`, `GET/POST /login`, `POST /logout`, and static files. Every other route requires a verified access token whose subject equals `ADMIN_USER_ID`.

## Sign in

1. The administrator opens `/login`.
2. The form posts email and password. The password field is not refilled if the attempt fails.
3. Eight failures for the same IP or the same email inside 15 minutes are stopped before another password request is sent. The message is generic.
4. A password grant goes to Supabase Auth. The application does not store the password.
5. The access token is verified (signature, expiry, issuer, audience, subject). The request is accepted only when that subject is `ADMIN_USER_ID`.
6. `hrms_access` and `hrms_refresh` are set as `HttpOnly` cookies with `SameSite=Lax`.
7. The browser is sent to the dashboard, or to the safe `next` path that was requested.

A valid token for any other user returns 403. An expired access token is refreshed once from the refresh cookie. If refresh fails, the cookies are cleared and the browser returns to `/login`.

## Sign out

`POST /logout` clears both cookies and asks Supabase to revoke the refresh session. There is no GET logout.

## Dashboard

`GET /` loads counts in SQL, not by loading every employee into Python.

- Total, active, inactive, active without a current posting, and missing a required value.
- A mix bar of active and inactive.
- Bars for the employee's own facility, gender (including "Not recorded"), employment type, current posting by designation, and current posting by facility.
- The eight most recently created employees. Each name links to that employee's view page.

Facility bars use `employees.facility_id`. Designation and posting-facility bars use the open posting (`end_date` is null). The unassigned count is not a link, because the register has no matching filter. The incomplete count links to the register with `incomplete=1`.

## Employee register

`GET /employees/` lists 20 employees per page.

Filters:

- Code or name (`q`, at most 100 characters).
- Current designation and current facility, both taken from the open posting.
- Employment type, taken from the employment-details row.
- Status: all, active, or inactive.
- One custom field and a value. The field id must be a known filterable field. Long text cannot be filtered. The value is bound as a parameter.
- `incomplete=1` keeps employees missing a required value.

The Facility column is the employee's own facility, not the posting facility. Sorting uses an allowlist. Custom columns can be sorted only for short text, integer, decimal, date, and yes/no. Dropdowns can be filtered, not sorted.

Each row offers View, Edit, and Deactivate or Reactivate.

## Add an employee

`GET/POST /employees/new`.

1. The form shows the next code, such as `EMP-0001`. Previewing the form does not consume the code.
2. Name, date of birth, gender, mobile number, and facility are required on this form. Email is optional. Active custom fields are included.
3. On save, the counter row is locked, the next unused `EMP-` code is reserved, and the employee is inserted with an audit row in the same transaction.
4. Employment and the current posting are not collected here. They are saved later on the profile.

`create_employee` still requires a real phone and a real facility. A roster import does not use that function, so a name can exist before those values are known.

## View an employee

`GET /employees/<id>` redirects to `GET /employees/<id>/view`.

The view page is read-only. It shows personal facts, additional fields, employment, and the current posting. Buttons open Edit, Excel, and PDF for that one employee. The page says the files contain this employee only. There is no save button. Delete is a separate confirmation.

## Edit personal details

`GET/POST /employees/<id>/personal`.

- The employee code is shown and cannot be changed.
- Name, date of birth, gender, mobile number, and facility are required to save this form. A blank gender is a real "Select" choice, so an empty stored gender is not presented as Female.
- Custom values are stored in `employees.custom_data`, keyed by the field's stable id.
- The form carries the field-catalog version. If fields changed after the form was opened, the save is refused and the latest fields are shown.
- The record version is checked. A stale save returns 409 and shows the latest stored values. It does not overwrite them.
- An unchanged save flashes "No changes to save."

Mobile rules: exactly 10 digits, first digit 6–9, no country code, not all the same digit, not `1234567890`, and not a strictly ascending or descending sequence. The value is stored as text.

A label that matches "Aadhaar" or "Aadhar" accepts only digits and spaces, must be 12 digits after spaces are removed, and is stored as 12 digits. There is no Verhoeff check. The screen shows the digits with spaces. Other integer fields are capped at ±1,000,000,000.

## Employment

`GET/POST /employees/<id>/employment`.

One employment-details row per employee: department, employment type, service joining date, optional retirement date, and pay level. Retirement cannot be before joining.

Job appointments are edited on the same page. An appointment date cannot be after the joining date. `GET /employees/<id>/appointments` redirects here. Creating, editing, and deleting an appointment writes an audit row and uses the same version check.

## Current posting

`GET/POST /employees/<id>/postings`.

The current posting is the single open row (`end_date` is null). It is not a separate flag. Saving this form always stores `end_date` as null. At most one open posting is allowed. A new period must start after the previous period ends. Dates are inclusive. Overlap is rejected.

Older posting rows stay in the table. The profile does not offer a screen to edit them. The posting's facility is independent of the facility stored on the employee.

## Deactivate and reactivate an employee

`GET` shows a confirmation page. `POST` changes `is_active` and writes an audit row. History rows remain. If the employee is already in the requested state, the flash says so and nothing is written. Deactivation is not deletion.

## Delete one employee

`GET /employees/<id>/delete` shows the name, code, whether the employee is active or inactive, and states that related HR records will be deleted and shared masters will be kept. `POST` performs the delete. `GET` does not.

Active and inactive employees can both be deleted. The related rows listed under employee deletion in the decision record are removed, then the employee, then one audit row, in one commit. A stale version or a database failure leaves the employee and the history in place.

## Delete the employees selected on this page

The register has a checkbox on each row and a checkbox that selects every row on the current page. The page says that select-all applies only to this page. Changing the filter, sort, or page loads a new page, so the ticks are cleared. The Delete selected button stays disabled until at least one row is ticked.

The first `POST /employees/delete-selected` only shows the count and the code, name, and status of each selected employee. A second post with `confirm=yes` deletes them. The return keeps the current register filters. The server checks every id and version. An empty selection, a duplicate, a value that is not an id, a missing employee, a stale version, or more than one page of ids deletes nobody. The batch and its audit rows commit together or not at all. There is no control that deletes every employee matching the filter.

## Download employees

- `GET /employees/export/excel`
- `GET /employees/export/pdf`

Both use the same filters as the register. `employee_id` limits the file to one employee. At most 2,000 rows are written. A longer match is marked truncated.

Columns are employee code, name, date of birth, gender, mobile number, email, facility, designation, employment type, status, then every active custom field, including fields hidden from the register. A hidden Aadhaar column is included because the administrator asked to download the data. Empty custom display "—" is written as a blank cell.

Excel keeps Unicode. A cell that starts with `=`, `+`, `-`, or `@` is forced to text. The PDF is one table with those same headers, in that order, and the same data rows. It does not rearrange the columns. The header row is repeated when the table continues onto another page. A wider table uses a landscape page. Its note says whether it contains one employee or all filtered records. A filtered result longer than 2,000 rows is marked as the first 2,000. The Excel download itself is unchanged.

## Bulk add employees

`GET /employees/bulk` explains the file. `GET /employees/bulk/template` downloads a UTF-8 CSV with a byte-order mark.

Fixed columns: `full_name`, `date_of_birth`, `gender`, `phone`, `facility`, `email`, `status`, then one column per active custom field. If two labels collide, the section name is added.

Rules:

- Only CSV is accepted. An `.xlsx` upload is rejected.
- The file must be at most 1 MB and at most 200 data rows.
- Unknown or missing columns fail the file.
- The sample row "Dr. Sample Name" / mobile `9867000001` is ignored before validation.
- Dates may be `YYYY-MM-DD`, `DD/MM/YYYY`, or `DD-MM-YYYY`.
- A phone ending in `.0` from a spreadsheet is stripped.
- Facility must match exactly one active facility, compared with an escaped case-insensitive pattern.
- Dropdown labels map to option ids. Retired options are not accepted for new rows.
- Yes/no accepts yes/y/true and no/n/false.
- A blank status means active.
- If any data row is invalid, nothing is inserted.
- Codes are assigned at insert time. One commit writes every new employee.

## Master lists

The navigation offers four lists:

| Slug | Screen |
| --- | --- |
| `departments` | Departments |
| `designations` | Designations |
| `facilities` | Facilities |
| `employment-types` | Employment types |

Any other slug returns 404. Qualifications, training types, and leave types still have tables. They have no screen.

For departments, designations, and employment types:

- The name is unique, ignoring case.
- Add, edit, deactivate, activate, and delete.
- Delete is refused while another row still references the record. The message names those places.
- Inactive rows stay on old employee history and disappear from new dropdowns.
- A blank status on bulk upload means active.

For facilities:

- The add form does not ask for a code. It shows the next `FAC-` code. The code is assigned when the row is saved and cannot be edited later.
- Existing codes that are not `FAC-` numbers, such as `CHC-BALUMATH`, are left as they are.
- Names may repeat. Codes may not.
- Type, district, and optional block are stored with the name.

Each of the four lists has:

- `GET /masters/<slug>/export/excel`
- `GET /masters/<slug>/export/pdf`
- `GET /masters/<slug>/bulk/template`
- `GET/POST /masters/<slug>/bulk`

Facility CSV columns are `name`, `facility_type`, `district`, `block`, `status`. There is no code column. The sample name "Sample facility" is ignored. Named-master CSV columns are `name` and `status`. The sample name "Sample name" is ignored. Duplicate names in the file or already in the database fail the whole file. Facility codes are allocated only for rows that are inserted.

## Manage fields

`GET/POST /fields/`.

Name, facility, and mobile number are system fields. They can be relabelled, given help text, reordered, and shown or hidden on the register. They cannot be archived, deleted, or made optional.

A custom field does not add a database column. The definition is a row in `employee_fields`. Values live in `employees.custom_data`. Renaming the label keeps the values.

Supported types: short text (200 characters), long text (4,000), integer, decimal, date, dropdown, and yes/no.

Dropdown choices have their own ids. Renaming a choice keeps selections. A choice that is already stored can be retired, not deleted. A choice that is not stored can be deleted. Retired choices remain visible on records that use them and are hidden from new selections.

Hiding a register column leaves the values in place. Archiving removes the field from the form and the register and keeps the values. Restore brings it back.

Permanent deletion asks for the current label, removes that key from every `custom_data` object, deletes the definition, and writes an audit row with the number of affected employees. Up and down change `sort_order`.

Adding a required field without a default does not invent values. Employees who lack it stay in the register and are counted as incomplete.

## Confirmation and errors

Destructive actions use `confirm.html`: the title, the message, a hidden version, and Cancel. Missing version on that post returns 400.

- 400: the form could not be submitted. The visitor is told to refresh.
- 403: the token is valid but the subject is not the administrator.
- 404: the record, slug, or route does not exist.
- 500: the request could not be completed. The page does not show SQL or credentials.
- CSRF failure clears nothing secret and renders the 400 page.

## Health

`GET /health` is public and exempt from CSRF. It runs `SELECT 1`. Success is `{"status":"ok"}`. Failure is 503 `{"status":"unavailable"}`. The body does not include a password, a host, or a SQL error.

## Database migrations

`flask --app wsgi:application db upgrade` uses `MIGRATION_DATABASE_URL` when that variable is set, otherwise `DATABASE_URL`. The Alembic version table is `hrms.alembic_version`.

| Revision | Effect |
| --- | --- |
| `0001_initial` | Creates schema `hrms`, all current model tables, enables row-level security, and revokes the Supabase API roles. |
| `0002_employee_fields` | Adds `facility_id` and `custom_data` when they are missing, and copies an open posting's facility onto an employee who has none. |
| `0003_employee_code_counters` | Creates the employee-code counter if needed and sets it to the highest `EMP-` number. |
| `0004_optional_birth_and_gender` | Makes `date_of_birth` and `gender` nullable. |
| `0005_facility_code_counters` | Creates the facility-code counter if needed, seeds the highest `FAC-` number, and enables row-level security on that table. |

`0001` and `0005` run `ENABLE ROW LEVEL SECURITY`. The `hrms_app` policy is created only when that role already exists. The table owner bypasses row-level security, so a `postgres` connection used for migrations is not stopped by the missing policy.

## Commands that are not screens

### `seed-demo`

Inserts a small synthetic set only when the database has no employees. It refuses to run otherwise. The people and facilities are fictional.

### `seed-workflow`

Loads 100 synthetic employees, `WF-0001` through `WF-0100`, plus masters, history, and custom fields. It runs only when `HRMS_TEST_DATABASE=1`, the host is `127.0.0.1` or `localhost`, and the database name is `hrms_test`. A Supabase or pooler host is refused.

### `cleanup-workflow`

Deletes only that synthetic dataset, and only from the same isolated database. It does not run as part of normal use.

### `workflow-timing`

Measures in-process page work against the isolated database. It does not sign in and it is not a browser timing.

### `import-opd-roster`

Reads `OPD Reporting September.xlsx`, or a path passed on the command line. It loads `.env` with override, drops `HRMS_TEST_DATABASE`, and refuses localhost, `hrms_test`, and any host that is not Supabase. It prints the host and the database name, not the password.

For each doctor row it matches a facility by lower-case name. "Sadar Hospital, Latehar" matches a facility whose name starts with "sadar hospital". A missing facility is created with the next `FAC-` code. Designation and employment type are created by name when missing. The employee is inserted only when the same lower-case name and facility are not already present. Phone, date of birth, gender, and Aadhaar are left empty. No posting and no employment-details row are created. The workbook stays out of git.

## What is stored but has no screen

These tables and forms remain so history and the demo seed can use them. Their collection routes are not registered, and they are not in the profile navigation:

- Addresses
- Education records and qualifications
- Training records and training types
- Promotions
- Allegations
- Leave records and leave types
- DACP details

Opening those paths returns 404. Job appointments are the exception: they are edited inside Employment.
