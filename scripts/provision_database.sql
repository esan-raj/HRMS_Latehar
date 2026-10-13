-- Provision the runtime database role for the HR application.
-- Run this in the Supabase SQL editor as the postgres role, after the
-- application migration has created the hrms schema. Replace the password
-- before running. Do not commit a filled-in copy of this file.
--
-- The browser and the Supabase Data API must not use this role. Point the
-- Flask DATABASE_URL at it. Keep migrations on the postgres direct connection.

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'hrms_app') then
    create role hrms_app login password 'REPLACE_WITH_APP_PASSWORD';
  end if;
end
$$;

grant usage on schema hrms to hrms_app;
revoke create on schema hrms from hrms_app;
grant select, insert, update, delete on all tables in schema hrms to hrms_app;
revoke all on table hrms.alembic_version from hrms_app;

do $$
declare
  tbl text;
begin
  for tbl in
    select tablename from pg_tables where schemaname = 'hrms' and tablename <> 'alembic_version'
  loop
    execute format('alter table hrms.%I enable row level security', tbl);
    if not exists (
      select 1 from pg_policies
      where schemaname = 'hrms' and tablename = tbl and policyname = 'hrms_app_all'
    ) then
      execute format(
        'create policy hrms_app_all on hrms.%I for all to hrms_app using (true) with check (true)',
        tbl
      );
    end if;
  end loop;
end
$$;

revoke all on schema hrms from public;
revoke all on all tables in schema hrms from public;

do $$
declare
  role_name text;
begin
  foreach role_name in array array['anon', 'authenticated', 'authenticator']
  loop
    if exists (select 1 from pg_roles where rolname = role_name) then
      execute format('revoke all on schema hrms from %I', role_name);
      execute format('revoke all on all tables in schema hrms from %I', role_name);
    end if;
  end loop;
end
$$;
