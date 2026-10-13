import re
import uuid
from datetime import date

from flask_wtf import FlaskForm
from wtforms import BooleanField, DateField, IntegerField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Email, InputRequired, Length, NumberRange, Optional, ValidationError
from wtforms.widgets import HiddenInput

CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9._/-]{0,31}$")
PHONE_RE = re.compile(r"^[0-9+(). -]{7,20}$")
PIN_RE = re.compile(r"^[0-9A-Za-z -]{4,10}$")
GENDERS = (("female", "Female"), ("male", "Male"), ("other", "Other"))
ALLEGATION_STATUSES = (("open", "Open"), ("closed", "Closed"))
LEAVE_STATUSES = (("recorded", "Recorded"), ("cancelled", "Cancelled"))


def strip(value):
    if value is None:
        return None
    return value.strip()


def strip_upper(value):
    return strip(value).upper() if value is not None else ""


def empty_none(value):
    value = strip(value) if isinstance(value, str) else value
    return value or None


def lower_email(value):
    value = empty_none(value)
    return value.lower() if isinstance(value, str) else None


def valid_code(_form, field):
    if not CODE_RE.fullmatch(field.data or ""):
        raise ValidationError("Use letters, numbers, and the characters . _ / -.")


def valid_phone(_form, field):
    if field.data and not PHONE_RE.fullmatch(field.data):
        raise ValidationError("Use digits and the characters + ( ) . - or spaces.")


def valid_pin(_form, field):
    if not PIN_RE.fullmatch(field.data or ""):
        raise ValidationError("Enter a PIN or postal code of 4 to 10 characters.")


def not_future_birth(_form, field):
    if field.data and (field.data > date.today() or field.data < date(1900, 1, 1)):
        raise ValidationError("Enter a date of birth between 1900 and today.")


class VersionedForm(FlaskForm):
    version = IntegerField(widget=HiddenInput(), default=0, validators=[InputRequired()])


class PersonalForm(VersionedForm):
    employee_code = StringField(
        "Employee code",
        filters=[strip_upper],
        validators=[DataRequired(), Length(max=32), valid_code],
    )
    full_name = StringField("Name", filters=[strip], validators=[DataRequired(), Length(max=200)])
    date_of_birth = DateField(
        "Date of birth",
        format="%Y-%m-%d",
        validators=[DataRequired(), not_future_birth],
        render_kw={"type": "date"},
    )
    gender = SelectField("Gender", choices=GENDERS, validators=[DataRequired()], validate_choice=True)
    phone = StringField(
        "Phone",
        filters=[empty_none],
        validators=[Optional(), Length(max=20), valid_phone],
    )
    email = StringField(
        "Email",
        filters=[lower_email],
        validators=[Optional(), Email(), Length(max=254)],
    )
    is_active = BooleanField("Active", default=True)

    def values(self) -> dict:
        return {
            "employee_code": self.employee_code.data,
            "full_name": self.full_name.data,
            "date_of_birth": self.date_of_birth.data,
            "gender": self.gender.data,
            "phone": self.phone.data,
            "email": self.email.data,
            "is_active": bool(self.is_active.data),
        }


class AddressForm(FlaskForm):
    present_line1 = StringField("Address line 1", filters=[strip], validators=[DataRequired(), Length(max=250)])
    present_line2 = StringField("Address line 2", filters=[empty_none], validators=[Optional(), Length(max=250)])
    present_district = StringField("District", filters=[strip], validators=[DataRequired(), Length(max=120)])
    present_state = StringField("State", filters=[strip], validators=[DataRequired(), Length(max=120)])
    present_pin = StringField("PIN code", filters=[strip], validators=[DataRequired(), valid_pin])
    present_version = IntegerField(widget=HiddenInput(), default=0, validators=[InputRequired()])
    permanent_line1 = StringField("Address line 1", filters=[strip], validators=[DataRequired(), Length(max=250)])
    permanent_line2 = StringField("Address line 2", filters=[empty_none], validators=[Optional(), Length(max=250)])
    permanent_district = StringField("District", filters=[strip], validators=[DataRequired(), Length(max=120)])
    permanent_state = StringField("State", filters=[strip], validators=[DataRequired(), Length(max=120)])
    permanent_pin = StringField("PIN code", filters=[strip], validators=[DataRequired(), valid_pin])
    permanent_version = IntegerField(widget=HiddenInput(), default=0, validators=[InputRequired()])

    def group(self, prefix: str) -> dict:
        return {
            "line1": getattr(self, f"{prefix}_line1").data,
            "line2": getattr(self, f"{prefix}_line2").data,
            "district": getattr(self, f"{prefix}_district").data,
            "state": getattr(self, f"{prefix}_state").data,
            "pin_code": getattr(self, f"{prefix}_pin").data,
        }


class EmploymentForm(VersionedForm):
    department_id = SelectField("Department", validators=[DataRequired()], validate_choice=True)
    employment_type_id = SelectField("Employment type", validators=[DataRequired()], validate_choice=True)
    service_joining_date = DateField(
        "Service joining date", format="%Y-%m-%d", validators=[DataRequired()], render_kw={"type": "date"}
    )
    retirement_date = DateField(
        "Retirement date",
        format="%Y-%m-%d",
        validators=[Optional()],
        render_kw={"type": "date"},
    )
    pay_level = StringField("Pay level or grade", filters=[strip], validators=[DataRequired(), Length(max=50)])

    def validate(self, extra_validators=None):
        ok = super().validate(extra_validators)
        if (
            self.retirement_date.data
            and self.service_joining_date.data
            and self.retirement_date.data < self.service_joining_date.data
        ):
            self.retirement_date.errors.append("Retirement date cannot be before the service joining date.")
            ok = False
        return ok

    def values(self) -> dict:
        return {
            "department_id": uuid.UUID(self.department_id.data),
            "employment_type_id": uuid.UUID(self.employment_type_id.data),
            "service_joining_date": self.service_joining_date.data,
            "retirement_date": self.retirement_date.data,
            "pay_level": self.pay_level.data,
        }


class EducationForm(VersionedForm):
    qualification_id = SelectField("Qualification", validators=[DataRequired()], validate_choice=True)
    specialization = StringField("Specialization", filters=[empty_none], validators=[Optional(), Length(max=200)])
    institution = StringField("Institution", filters=[strip], validators=[DataRequired(), Length(max=250)])
    passing_year = IntegerField(
        "Passing year",
        validators=[DataRequired(), NumberRange(min=1950, max=2100)],
    )

    def validate_passing_year(self, field):
        if field.data and field.data > date.today().year + 1:
            raise ValidationError("Passing year cannot be more than one year ahead.")

    def values(self) -> dict:
        return {
            "qualification_id": uuid.UUID(self.qualification_id.data),
            "specialization": self.specialization.data,
            "institution": self.institution.data,
            "passing_year": self.passing_year.data,
        }


class TrainingForm(VersionedForm):
    training_type_id = SelectField("Training type", validators=[Optional()], validate_choice=True)
    title = StringField("Training title", filters=[strip], validators=[DataRequired(), Length(max=250)])
    provider = StringField("Provider", filters=[strip], validators=[DataRequired(), Length(max=250)])
    start_date = DateField("Start date", format="%Y-%m-%d", validators=[DataRequired()], render_kw={"type": "date"})
    end_date = DateField("End date", format="%Y-%m-%d", validators=[DataRequired()], render_kw={"type": "date"})
    remarks = TextAreaField("Remarks", filters=[empty_none], validators=[Optional(), Length(max=2000)])

    def validate(self, extra_validators=None):
        ok = super().validate(extra_validators)
        if self.start_date.data and self.end_date.data and self.end_date.data < self.start_date.data:
            self.end_date.errors.append("End date cannot be before the start date.")
            ok = False
        return ok

    def values(self) -> dict:
        return {
            "training_type_id": uuid.UUID(self.training_type_id.data) if self.training_type_id.data else None,
            "title": self.title.data,
            "provider": self.provider.data,
            "start_date": self.start_date.data,
            "end_date": self.end_date.data,
            "remarks": self.remarks.data,
        }


class AppointmentForm(VersionedForm):
    appointment_reference = StringField(
        "Appointment reference", filters=[strip], validators=[DataRequired(), Length(max=120)]
    )
    appointment_date = DateField(
        "Appointment date", format="%Y-%m-%d", validators=[DataRequired()], render_kw={"type": "date"}
    )
    joining_date = DateField("Joining date", format="%Y-%m-%d", validators=[DataRequired()], render_kw={"type": "date"})
    designation_id = SelectField("Designation", validators=[DataRequired()], validate_choice=True)
    employment_type_id = SelectField("Employment type", validators=[DataRequired()], validate_choice=True)

    def validate(self, extra_validators=None):
        ok = super().validate(extra_validators)
        if (
            self.appointment_date.data
            and self.joining_date.data
            and self.joining_date.data < self.appointment_date.data
        ):
            self.joining_date.errors.append("Joining date cannot be before the appointment date.")
            ok = False
        return ok

    def values(self) -> dict:
        return {
            "appointment_reference": self.appointment_reference.data,
            "appointment_date": self.appointment_date.data,
            "joining_date": self.joining_date.data,
            "designation_id": uuid.UUID(self.designation_id.data),
            "employment_type_id": uuid.UUID(self.employment_type_id.data),
        }


class PostingForm(VersionedForm):
    facility_id = SelectField("Facility", validators=[DataRequired()], validate_choice=True)
    designation_id = SelectField("Designation", validators=[DataRequired()], validate_choice=True)
    start_date = DateField("Start date", format="%Y-%m-%d", validators=[DataRequired()], render_kw={"type": "date"})
    end_date = DateField("End date", format="%Y-%m-%d", validators=[Optional()], render_kw={"type": "date"})
    order_reference = StringField("Order reference", filters=[strip], validators=[DataRequired(), Length(max=120)])
    remarks = TextAreaField("Remarks", filters=[empty_none], validators=[Optional(), Length(max=2000)])

    def validate(self, extra_validators=None):
        ok = super().validate(extra_validators)
        if self.start_date.data and self.end_date.data and self.end_date.data < self.start_date.data:
            self.end_date.errors.append("End date cannot be before the start date.")
            ok = False
        return ok

    def values(self) -> dict:
        return {
            "facility_id": uuid.UUID(self.facility_id.data),
            "designation_id": uuid.UUID(self.designation_id.data),
            "start_date": self.start_date.data,
            "end_date": self.end_date.data,
            "order_reference": self.order_reference.data,
            "remarks": self.remarks.data,
        }


class PromotionForm(VersionedForm):
    previous_designation_id = SelectField("Previous designation", validators=[DataRequired()], validate_choice=True)
    new_designation_id = SelectField("New designation", validators=[DataRequired()], validate_choice=True)
    effective_date = DateField(
        "Effective date", format="%Y-%m-%d", validators=[DataRequired()], render_kw={"type": "date"}
    )
    order_reference = StringField("Order reference", filters=[strip], validators=[DataRequired(), Length(max=120)])
    remarks = TextAreaField("Remarks", filters=[empty_none], validators=[Optional(), Length(max=2000)])

    def validate(self, extra_validators=None):
        ok = super().validate(extra_validators)
        if (
            self.previous_designation_id.data
            and self.previous_designation_id.data == self.new_designation_id.data
        ):
            self.new_designation_id.errors.append("Choose a different new designation.")
            ok = False
        return ok

    def values(self) -> dict:
        return {
            "previous_designation_id": uuid.UUID(self.previous_designation_id.data),
            "new_designation_id": uuid.UUID(self.new_designation_id.data),
            "effective_date": self.effective_date.data,
            "order_reference": self.order_reference.data,
            "remarks": self.remarks.data,
        }


class AllegationForm(VersionedForm):
    reference_no = StringField("Reference", filters=[strip], validators=[DataRequired(), Length(max=120)])
    description = TextAreaField("Description", filters=[strip], validators=[DataRequired(), Length(max=4000)])
    recorded_date = DateField(
        "Recorded date", format="%Y-%m-%d", validators=[DataRequired()], render_kw={"type": "date"}
    )
    status = SelectField("Status", choices=ALLEGATION_STATUSES, validators=[DataRequired()], validate_choice=True)
    resolution_date = DateField(
        "Resolution date", format="%Y-%m-%d", validators=[Optional()], render_kw={"type": "date"}
    )
    remarks = TextAreaField("Remarks", filters=[empty_none], validators=[Optional(), Length(max=2000)])

    def validate(self, extra_validators=None):
        ok = super().validate(extra_validators)
        if self.status.data == "closed" and not self.resolution_date.data:
            self.resolution_date.errors.append("Enter a resolution date when the status is closed.")
            ok = False
        if self.status.data == "open" and self.resolution_date.data:
            self.resolution_date.errors.append("Clear the resolution date while the status is open.")
            ok = False
        if (
            self.recorded_date.data
            and self.resolution_date.data
            and self.resolution_date.data < self.recorded_date.data
        ):
            self.resolution_date.errors.append("Resolution date cannot be before the recorded date.")
            ok = False
        return ok

    def values(self) -> dict:
        return {
            "reference_no": self.reference_no.data,
            "description": self.description.data,
            "recorded_date": self.recorded_date.data,
            "status": self.status.data,
            "resolution_date": self.resolution_date.data,
            "remarks": self.remarks.data,
        }


class LeaveForm(VersionedForm):
    leave_type_id = SelectField("Leave type", validators=[DataRequired()], validate_choice=True)
    start_date = DateField("Start date", format="%Y-%m-%d", validators=[DataRequired()], render_kw={"type": "date"})
    end_date = DateField("End date", format="%Y-%m-%d", validators=[DataRequired()], render_kw={"type": "date"})
    status = SelectField("Status", choices=LEAVE_STATUSES, validators=[DataRequired()], validate_choice=True)
    remarks = TextAreaField("Remarks", filters=[empty_none], validators=[Optional(), Length(max=2000)])

    def validate(self, extra_validators=None):
        ok = super().validate(extra_validators)
        if self.start_date.data and self.end_date.data and self.end_date.data < self.start_date.data:
            self.end_date.errors.append("End date cannot be before the start date.")
            ok = False
        return ok

    def values(self) -> dict:
        return {
            "leave_type_id": uuid.UUID(self.leave_type_id.data),
            "start_date": self.start_date.data,
            "end_date": self.end_date.data,
            "status": self.status.data,
            "remarks": self.remarks.data,
        }


class DacpForm(VersionedForm):
    stage = StringField("Stage or level", filters=[strip], validators=[DataRequired(), Length(max=80)])
    effective_date = DateField(
        "Effective date", format="%Y-%m-%d", validators=[DataRequired()], render_kw={"type": "date"}
    )
    order_reference = StringField("Order reference", filters=[strip], validators=[DataRequired(), Length(max=120)])
    remarks = TextAreaField("Remarks", filters=[empty_none], validators=[Optional(), Length(max=2000)])

    def values(self) -> dict:
        return {
            "stage": self.stage.data,
            "effective_date": self.effective_date.data,
            "order_reference": self.order_reference.data,
            "remarks": self.remarks.data,
        }


class MasterNameForm(VersionedForm):
    name = StringField("Name", filters=[strip], validators=[DataRequired(), Length(max=120)])

    def values(self) -> dict:
        return {"name": self.name.data}


class FacilityForm(VersionedForm):
    name = StringField("Name", filters=[strip], validators=[DataRequired(), Length(max=200)])
    code = StringField("Code", filters=[strip_upper], validators=[DataRequired(), Length(max=32), valid_code])
    facility_type = StringField("Type", filters=[strip], validators=[DataRequired(), Length(max=80)])
    district = StringField("District", filters=[strip], validators=[DataRequired(), Length(max=120)])
    block = StringField("Block", filters=[empty_none], validators=[Optional(), Length(max=120)])

    def values(self) -> dict:
        return {
            "name": self.name.data,
            "code": self.code.data,
            "facility_type": self.facility_type.data,
            "district": self.district.data,
            "block": self.block.data,
        }
