"""Private owner-scoped notification preferences and current delivery status."""
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .appointment_lifecycle import AppointmentLifecycle
from .hospital_search import SearchModel
from .notification_templates import Channel, Locale, NotificationDenied
from .scoped_session import AuthorizationDenied, Principal, _scoped_transaction


class Preference(SearchModel):
    channel: Channel
    locale: Locale
    enabled: bool
    reminders: bool


class NotificationPreferences:
    def __init__(self, lifecycle: AppointmentLifecycle):
        self.lifecycle = lifecycle

    def record(self, token, preference):
        pref = Preference.model_validate(preference.model_dump())
        credential = self.lifecycle.credential(token)
        try:
            with _scoped_transaction(self.lifecycle.engine, Principal("anonymous", None),
                                     "roghyar_appointment_writer", readonly=False) as db:
                auth = self.lifecycle.authenticate(db, credential)
                patient = db.scalar(text("SELECT id FROM roghyar.patients WHERE auth_subject=:subject"),
                                    {"subject": auth.subject})
                if patient is None:
                    raise NotificationDenied("recipient_unavailable")
                db.execute(text("SELECT pg_advisory_xact_lock(15701,hashtext(CAST(:id AS text)))"), {"id": patient})
                identity = uuid4()
                db.execute(text("""
                    INSERT INTO roghyar.notification_consent(id,patient_id,channel,locale,policy,reminders,enabled)
                    VALUES(:id,:patient,:channel,:locale,1,:reminders,:enabled)
                """), pref.model_dump() | {"id": identity, "patient": patient})
            return identity
        except (SQLAlchemyError, AuthorizationDenied):
            raise NotificationDenied("consent_unavailable") from None

    def status(self, token, appointment: UUID, *, approval_active=False):
        if not isinstance(appointment, UUID):
            raise NotificationDenied("invalid_query")
        credential = self.lifecycle.credential(token)
        try:
            with _scoped_transaction(self.lifecycle.engine, Principal("anonymous", None),
                                     "roghyar_appointment_writer", readonly=False) as db:
                self.lifecycle.authenticate(db,credential)
                if not db.scalar(text("SELECT id FROM roghyar.appointments WHERE id=:id"),{"id": appointment}):
                    raise NotificationDenied("not_found")
                if not approval_active:
                    return "unavailable"
                return db.scalar(text("""
                    SELECT state FROM roghyar.notification_outbox WHERE appointment_id=:id
                    ORDER BY created_at DESC,id DESC LIMIT 1
                """), {"id": appointment}) or "unavailable"
        except (SQLAlchemyError, AuthorizationDenied):
            raise NotificationDenied("status_unavailable") from None
