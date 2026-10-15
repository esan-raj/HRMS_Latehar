from sqlalchemy import text


def ping_database() -> bool:
    from app.extensions import db

    try:
        db.session.execute(text("SELECT 1"))
        db.session.rollback()
        return True
    except Exception:
        db.session.rollback()
        return False
