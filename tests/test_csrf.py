import re

from tests.conftest import CsrfConfig, authenticate
from app import create_app
from app.extensions import db


def test_login_and_employee_post_require_csrf():
    application = create_app(CsrfConfig)
    with application.app_context():
        db.create_all()
        client = application.test_client()
        missing = client.post("/login", data={"email": "admin@example.com", "password": "whatever-password"})
        assert missing.status_code == 400
        assert b"could not be submitted" in missing.data

        authenticate(client, application)
        page = client.get("/employees/new")
        assert page.status_code == 200
        token = re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True)).group(1)
        rejected = client.post(
            "/employees/new",
            data={
                "employee_code": "EMP-1",
                "full_name": "No Token",
                "date_of_birth": "1990-01-01",
                "gender": "female",
                "version": "0",
            },
        )
        assert rejected.status_code == 400
        created = client.post(
            "/employees/new",
            data={
                "csrf_token": token,
                "employee_code": "EMP-1",
                "full_name": "With Token",
                "date_of_birth": "1990-01-01",
                "gender": "female",
                "is_active": "y",
                "version": "0",
            },
        )
        assert created.status_code == 302
        db.session.remove()
        db.drop_all()
