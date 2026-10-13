"""Flask-Migrate environment.

The HR tables live in the private ``hrms`` schema. This file creates that
schema before Alembic writes its version table there.
"""
from logging.config import fileConfig

from alembic import context
from flask import current_app
from sqlalchemy import text

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_db = current_app.extensions["migrate"].db


def get_metadata():
    if hasattr(target_db, "metadatas"):
        return target_db.metadatas[None]
    return target_db.metadata


def run_migrations_offline():
    url = current_app.config.get("SQLALCHEMY_DATABASE_URI")
    context.configure(
        url=url,
        target_metadata=get_metadata(),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema="hrms",
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = target_db.engine
    with connectable.connect() as connection:
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS hrms"))
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=get_metadata(),
            version_table_schema="hrms",
            include_schemas=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
