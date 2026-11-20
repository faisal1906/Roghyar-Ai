"""Current SQLAlchemy metadata for the private application schema."""

from sqlalchemy import (
    ARRAY, Boolean, CheckConstraint, Column, DateTime, ForeignKey, ForeignKeyConstraint,
    Index, Integer, LargeBinary, MetaData, Numeric, String, Table, Text, Time,
    UniqueConstraint, Uuid, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import registry
from sqlalchemy.types import UserDefinedType

SCHEMA = "roghyar"
metadata = MetaData(schema=SCHEMA)
mapper = registry(metadata=metadata)


class GeographyPoint(UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **kw):
        return "geography(Point,4326)"


def table(name, *fields):
    return Table(name, metadata, Column("id", Uuid, primary_key=True,
                 server_default=text("gen_random_uuid()")), *fields)


def created(name="created_at"):
    return Column(name, DateTime(timezone=True), nullable=False, server_default=text("now()"))


def fk(name, target, *, nullable=False):
    return Column(name, Uuid, ForeignKey(f"{SCHEMA}.{target}.id"), nullable=nullable)


def verified():
    return (
        Column("verified", Boolean, nullable=False, server_default=text("false")),
        Column("published", Boolean, nullable=False, server_default=text("false")),
        Column("verified_on", DateTime(timezone=True)),
        Column("verified_by", Uuid),
        Column("source", Text),
        CheckConstraint("NOT published OR verified"),
        CheckConstraint("NOT verified OR (verified_on IS NOT NULL AND verified_by IS NOT NULL "
                        "AND source IS NOT NULL AND length(trim(source)) > 0)"),
    )


hospitals = table(
    "hospitals", Column("name", Text, nullable=False), Column("aliases", ARRAY(Text)),
    Column("address", Text, nullable=False), Column("city", Text, nullable=False),
    Column("district", Text, nullable=False), Column("province", Text, nullable=False),
    Column("location", GeographyPoint(), nullable=False), Column("phone", Text),
    Column("is_emergency_24_7", Boolean, nullable=False, server_default=text("false")),
    Column("departments", ARRAY(Text)), created(), *verified(),
)
Index("ix_hospitals_location", hospitals.c.location, postgresql_using="gist")
Index("ix_hospitals_city_name", hospitals.c.city, text("lower(name)"))

doctors = table(
    "doctors", fk("hospital_id", "hospitals"), Column("full_name", Text, nullable=False),
    Column("specialty", Text, nullable=False), Column("qualifications", ARRAY(Text)),
    Column("experience_years", Integer), Column("bio", Text),
    Column("pmdc_reg_no", String(50), nullable=False), Column("fee", Numeric(10, 2)),
    Column("languages", ARRAY(Text)), Column("photo_url", Text), created(), *verified(),
    UniqueConstraint("id", "hospital_id"), CheckConstraint("experience_years >= 0"),
    CheckConstraint("fee >= 0"), CheckConstraint("length(trim(pmdc_reg_no)) > 0"),
)
Index("ux_doctors_pmdc", text("lower(trim(pmdc_reg_no))"), unique=True, _table=doctors)
Index("ix_doctors_hospital_specialty", doctors.c.hospital_id, doctors.c.specialty)

doctor_schedules = table(
    "doctor_schedules", fk("doctor_id", "doctors"), Column("weekday", Integer, nullable=False),
    Column("start_time", Time, nullable=False), Column("end_time", Time, nullable=False),
    Column("slot_minutes", Integer, nullable=False), Column("location_note", Text), created(),
    CheckConstraint("weekday BETWEEN 0 AND 6"),
    CheckConstraint("end_time > start_time AND slot_minutes BETWEEN 1 AND 720 "
                    "AND extract(epoch FROM end_time - start_time) >= slot_minutes * 60"),
    UniqueConstraint("doctor_id", "weekday", "start_time"),
)

patients = table(
    "patients", Column("auth_subject", Uuid, nullable=False, unique=True),
    Column("full_name_ciphertext", LargeBinary, nullable=False),
    Column("phone_ciphertext", LargeBinary, nullable=False),
    Column("phone_lookup", LargeBinary, nullable=False, unique=True),
    Column("phone_verified_at", DateTime(timezone=True)),
    Column("guardian_name_ciphertext", LargeBinary), Column("age", Integer, nullable=False),
    Column("gender", Text), Column("consent_at", DateTime(timezone=True), nullable=False), created(),
    CheckConstraint("age BETWEEN 0 AND 120"), CheckConstraint("octet_length(phone_lookup) = 32"),
)

assessments = table(
    "assessments", Column("session_id", Uuid), fk("patient_id", "patients", nullable=True),
    Column("text_ciphertext", LargeBinary), Column("language", String(8)),
    Column("severity", Text), Column("duration_hours", Integer),
    Column("more_than_one_year", Boolean, nullable=False, server_default=text("false")),
    Column("age", Integer), Column("answers_ciphertext", LargeBinary),
    Column("safety_details_ciphertext", LargeBinary), Column("level", Text),
    Column("condition_code", Text), Column("specialty", Text), Column("red_flags", ARRAY(Text)),
    Column("anonymized_at", DateTime(timezone=True)), created(),
    CheckConstraint("age BETWEEN 0 AND 120"),
    CheckConstraint("severity IN ('mild', 'moderate', 'severe')"),
    CheckConstraint("""(
       anonymized_at IS NULL AND (session_id IS NOT NULL OR patient_id IS NOT NULL)
       AND text_ciphertext IS NOT NULL AND age IS NOT NULL AND severity IS NOT NULL
       AND ((duration_hours IS NULL AND more_than_one_year)
            OR (duration_hours BETWEEN 1 AND 8760 AND duration_hours IS NOT NULL
                AND NOT more_than_one_year))
    ) OR (
       anonymized_at IS NOT NULL AND session_id IS NULL AND patient_id IS NULL
       AND text_ciphertext IS NULL AND age IS NULL AND severity IS NULL AND duration_hours IS NULL
       AND NOT more_than_one_year AND language IS NULL AND answers_ciphertext IS NULL
       AND safety_details_ciphertext IS NULL AND level IS NULL AND condition_code IS NULL
       AND specialty IS NULL AND red_flags IS NULL
    ) OR (
       anonymized_at IS NULL AND num_nonnulls(session_id, patient_id) = 1
       AND text_ciphertext IS NOT NULL AND answers_ciphertext IS NOT NULL
       AND safety_details_ciphertext IS NOT NULL AND age IS NULL AND severity IS NULL
       AND duration_hours IS NULL AND NOT more_than_one_year AND language IS NULL
    )""", name="ck_assessments_live_or_anonymized"),
)
Index("ix_assessments_session", assessments.c.session_id)
Index("ix_assessments_patient", assessments.c.patient_id)
Index("ix_assessments_retention", assessments.c.created_at,
      postgresql_where=assessments.c.anonymized_at.is_(None))

appointments = table(
    "appointments", Column("booking_code", String(32), nullable=False, unique=True),
    Column("doctor_id", Uuid, nullable=False), Column("hospital_id", Uuid, nullable=False),
    fk("patient_id", "patients"), fk("assessment_id", "assessments", nullable=True),
    Column("slot_start", DateTime(timezone=True), nullable=False),
    Column("slot_end", DateTime(timezone=True), nullable=False),
    Column("reason_ciphertext", LargeBinary), Column("status", Text, nullable=False),
    Column("status_changed_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("consent_at", DateTime(timezone=True), nullable=False), created(),
    ForeignKeyConstraint(["doctor_id", "hospital_id"], ["roghyar.doctors.id", "roghyar.doctors.hospital_id"]),
    CheckConstraint("slot_end > slot_start"),
    CheckConstraint("status IN ('pending','confirmed','cancelled','rescheduled','completed','no_show')"),
)
Index("ux_appointments_active_slot", appointments.c.doctor_id, appointments.c.slot_start, unique=True,
      postgresql_where=appointments.c.status.in_(("pending", "confirmed")))
Index("ix_appointments_patient_start", appointments.c.patient_id, appointments.c.slot_start)
Index("ix_appointments_hospital_start", appointments.c.hospital_id, appointments.c.slot_start)
Index("ix_appointments_assessment", appointments.c.assessment_id)

chat_messages = table(
    "chat_messages", Column("session_id", Uuid, nullable=False),
    fk("patient_id", "patients", nullable=True), Column("role", Text, nullable=False),
    Column("text_ciphertext", LargeBinary, nullable=False), Column("language", String(8)),
    Column("relevance_category", Text), Column("confidence", Numeric(5, 4)), created(),
    CheckConstraint("role IN ('patient', 'assistant')"), CheckConstraint("confidence BETWEEN 0 AND 1"),
)
Index("ix_chat_session_created", chat_messages.c.session_id, chat_messages.c.created_at)
Index("ix_chat_patient", chat_messages.c.patient_id)


def review_fields():
    return (
        Column("version", Integer, nullable=False), Column("review_status", Text, nullable=False,
               server_default=text("'draft'")),
        Column("reviewed_by", Uuid), Column("reviewed_at", DateTime(timezone=True)),
        Column("active", Boolean, nullable=False, server_default=text("false")), created(),
        CheckConstraint("version > 0"), CheckConstraint("review_status IN ('draft','approved','rejected')"),
        CheckConstraint("review_status = 'draft' OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)"),
        CheckConstraint("NOT active OR review_status = 'approved'"),
    )


formulary = table(
    "formulary", Column("condition_code", Text, nullable=False), Column("medicine", Text, nullable=False),
    Column("adult_dose", Text), Column("frequency", Text), Column("max_daily", Text),
    Column("min_age", Integer), Column("max_age", Integer), Column("child_dose_table", JSONB),
    Column("contraindications", ARRAY(Text)), Column("home_steps", ARRAY(Text)),
    Column("see_doctor_if", ARRAY(Text)), *review_fields(),
    CheckConstraint("min_age BETWEEN 0 AND 120 AND max_age BETWEEN 0 AND 120 AND min_age <= max_age"),
    UniqueConstraint("condition_code", "medicine", "version"),
)
Index("ux_formulary_active_version", formulary.c.condition_code, formulary.c.medicine, unique=True,
      postgresql_where=formulary.c.active)

red_flag_rules = table(
    "red_flag_rules", Column("rule_code", Text, nullable=False), Column("category", Text, nullable=False),
    Column("pattern", JSONB, nullable=False), Column("level", Text, nullable=False), *review_fields(),
    UniqueConstraint("rule_code", "version"),
    CheckConstraint("""NOT active OR (
        pattern->'schema_version' = '1'::jsonb
        AND jsonb_typeof(pattern->'attestation') = 'object'
        AND jsonb_typeof(pattern->'attestation'->'key_id') = 'string'
        AND jsonb_typeof(pattern->'attestation'->'signature') = 'string'
        AND pattern->'attestation'->>'signature' ~ '^[0-9a-f]{128}$'
    ) IS TRUE""", name="ck_red_flag_active_attestation"),
)
Index("ux_red_flag_active_version", red_flag_rules.c.rule_code, unique=True,
      postgresql_where=red_flag_rules.c.active)

otp_codes = table(
    "otp_codes", Column("phone_lookup", LargeBinary, nullable=False),
    Column("code_hash", LargeBinary, nullable=False), Column("purpose", Text, nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("consumed_at", DateTime(timezone=True)), Column("attempts", Integer, nullable=False,
           server_default=text("0")), created(),
    CheckConstraint("attempts >= 0 AND expires_at > created_at"),
    CheckConstraint("purpose IN ('booking', 'appointments')"),
    CheckConstraint("octet_length(phone_lookup) = 32"),
)
Index("ix_otp_lookup_purpose", otp_codes.c.phone_lookup, otp_codes.c.purpose, otp_codes.c.expires_at)

admin_users = table(
    "admin_users", fk("hospital_id", "hospitals"), Column("auth_subject", Uuid, nullable=False),
    Column("email", Text, nullable=False), Column("role", Text, nullable=False),
    Column("active", Boolean, nullable=False, server_default=text("false")), created(),
    UniqueConstraint("auth_subject", "hospital_id"),
    CheckConstraint("role IN ('hospital_admin')"),
)

audit_log = table(
    "audit_log", Column("actor", Uuid), Column("actor_db_role", String(63)),
    Column("action", Text, nullable=False),
    Column("entity", Text, nullable=False), Column("entity_id", Uuid, nullable=False), created("at"),
)
Index("ix_audit_entity_at", audit_log.c.entity, audit_log.c.entity_id, audit_log.c.at)


Base = mapper.generate_base()


class Hospital(Base):
    __table__ = hospitals


class Doctor(Base):
    __table__ = doctors


class DoctorSchedule(Base):
    __table__ = doctor_schedules


class Appointment(Base):
    __table__ = appointments


class Patient(Base):
    __table__ = patients


class Assessment(Base):
    __table__ = assessments


class ChatMessage(Base):
    __table__ = chat_messages


class Formulary(Base):
    __table__ = formulary


class RedFlagRule(Base):
    __table__ = red_flag_rules


class OtpCode(Base):
    __table__ = otp_codes


class AdminUser(Base):
    __table__ = admin_users


class AuditLog(Base):
    __table__ = audit_log

