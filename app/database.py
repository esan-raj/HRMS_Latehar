from sqlalchemy import event, text
from sqlalchemy.orm import Session

_registered = False


def register_search_path() -> None:
    """Set the private schema at the start of every Postgres transaction.

    SET inside a transaction is undone by rollback, so it is applied again on
    the next transaction. SQLite tests ignore this.
    """
    global _registered
    if _registered:
        return
    _registered = True

    @event.listens_for(Session, "after_begin")
    def set_search_path(session, transaction, connection):
        if connection.dialect.name != "postgresql":
            return
        connection.exec_driver_sql("SET search_path TO hrms, public")


def ping_database() -> bool:
    from app.extensions import db

    try:
        db.session.execute(text("SELECT 1"))
        db.session.rollback()
        return True
    except Exception:
        db.session.rollback()
        return False
