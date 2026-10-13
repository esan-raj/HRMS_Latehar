"""Create the private hrms schema and application tables.

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-13
"""
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    import app.models  # noqa: F401
    from app.extensions import db

    op.execute("CREATE SCHEMA IF NOT EXISTS hrms")
    op.execute("SET search_path TO hrms, public")
    bind = op.get_bind()
    db.metadata.create_all(bind=bind.execution_options(schema_translate_map={None: "hrms"}))
    names = sorted(db.metadata.tables)
    for name in names:
        if not name.replace("_", "").isalnum():
            raise RuntimeError("Refusing to lock down an unexpected table name.")
        op.execute(f"ALTER TABLE hrms.{name} ENABLE ROW LEVEL SECURITY")
    quoted = ", ".join(f"'{name}'" for name in names)
    op.execute("REVOKE ALL ON SCHEMA hrms FROM PUBLIC")
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA hrms FROM PUBLIC")
    op.execute(
        f"""
        DO $$
        DECLARE
            role_name text;
            tbl text;
        BEGIN
            FOREACH role_name IN ARRAY ARRAY['anon', 'authenticated', 'authenticator']
            LOOP
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
                    EXECUTE format('REVOKE ALL ON SCHEMA hrms FROM %I', role_name);
                    EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA hrms FROM %I', role_name);
                END IF;
            END LOOP;

            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hrms_app') THEN
                EXECUTE 'GRANT USAGE ON SCHEMA hrms TO hrms_app';
                EXECUTE 'REVOKE CREATE ON SCHEMA hrms FROM hrms_app';
                FOREACH tbl IN ARRAY ARRAY[{quoted}]
                LOOP
                    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON hrms.%I TO hrms_app', tbl);
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_policies
                        WHERE schemaname = 'hrms' AND tablename = tbl AND policyname = 'hrms_app_all'
                    ) THEN
                        EXECUTE format(
                            'CREATE POLICY hrms_app_all ON hrms.%I FOR ALL TO hrms_app USING (true) WITH CHECK (true)',
                            tbl
                        );
                    END IF;
                END LOOP;
                IF to_regclass('hrms.alembic_version') IS NOT NULL THEN
                    EXECUTE 'REVOKE ALL ON TABLE hrms.alembic_version FROM hrms_app';
                END IF;
            END IF;
        END $$;
        """
    )
    op.execute("COMMENT ON SCHEMA hrms IS 'Private HR schema. Do not add it to exposed Supabase Data API schemas.'")


def downgrade():
    import app.models  # noqa: F401
    from app.extensions import db

    bind = op.get_bind()
    db.metadata.drop_all(bind=bind.execution_options(schema_translate_map={None: "hrms"}))
