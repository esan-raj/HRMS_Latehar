import re

from app import create_app
from app.extensions import db
from app.models import Facility
from app.persistence import new_id
from tests.conftest import CsrfConfig, authenticate


def test_login_and_employee_post_require_csrf():
    application = create_app(CsrfConfig)
    with application.app_context():
        db.create_all()
        client = application.test_client()
        missing = client.post("/login", data={"email": "admin@example.com", "password": "whatever-password"})
        assert missing.status_code == 400
        assert b"could not be submitted" in missing.data

        authenticate(client, application)
        facility = Facility(
            id=new_id(),
            name="Northview PHC",
            code="PHC-CSRF",
            facility_type="PHC",
            district="Northview",
            is_active=True,
            version=1,
        )
        db.session.add(facility)
        db.session.commit()
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
                "phone": "9867543210",
                "facility_id": str(facility.id),
                "fields_version": "1",
                "is_active": "y",
                "version": "0",
            },
        )
        assert created.status_code == 302
        db.session.remove()
        db.drop_all()
