"""Independently signed, revision-bound clinic decisions for the private staff workflow."""
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import hmac
from typing import Literal
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from pydantic import AwareDatetime, TypeAdapter
from sqlalchemy import text

from .admin_identity import AdminDenied, Digest, Signature, StaffService, transaction
from .booking_reservations import Key
from .doctor_schedules import LOCAL
from .hospital_search import SearchModel
from .red_flags import canonical
from .schedule_confirmation import Confirmation, ConfirmationDenied, authenticate


class ClinicDecision(SearchModel):
    id: UUID
    actor: UUID
    hospital: UUID
    revision: int
    appointment: UUID
    expected_change: AwareDatetime
    slot_start: AwareDatetime
    slot_end: AwareDatetime
    action: Literal["confirm", "cancel", "complete", "no_show"]
    expiry: AwareDatetime
    basis: Digest
    schedule_proof: Digest | None = None
    approver: UUID
    signature: Signature

    def signed_bytes(self):
        return b"roghyar:staff-clinic-decision:v1\0" + canonical(self.model_dump(mode="json",exclude={"signature"}))


class ClinicAuthority:
    def __init__(self,engine,anchors,trust,clock=lambda:datetime.now(UTC)):
        self.engine,self.anchors,self.trust,self.clock=engine,dict(anchors),trust,clock

    def record(self, decision: ClinicDecision, evidence: bytes, schedule: Confirmation | None=None):
        decision=ClinicDecision.model_validate(decision.model_dump())
        key=self.anchors.get(decision.approver)
        now=self.clock()
        if (key is None or decision.hospital not in key.hospitals or decision.actor==decision.approver
                or not key.starts<=now<decision.expiry<=min(key.expires,now+timedelta(hours=1))
                or type(evidence) is not bytes or not 0<len(evidence)<=65536
                or sha256(evidence).hexdigest()!=decision.basis):
            raise AdminDenied("untrusted_clinic_authority")
        try:
            key.key.verify(bytes.fromhex(decision.signature),decision.signed_bytes())
            if decision.action=="confirm":
                if schedule is None:
                    raise ValueError()
                schedule=Confirmation.model_validate(schedule.model_dump())
                authority_expiry=authenticate(schedule,self.trust.snapshot(),now)
                local = decision.slot_start.astimezone(LOCAL)
                exact = any(
                    0 <= (local - datetime.combine(local.date(),hours.start,tzinfo=LOCAL)).total_seconds()
                    and (local - datetime.combine(local.date(),hours.start,tzinfo=LOCAL)).total_seconds() % (hours.minutes*60)==0
                    and decision.slot_end == decision.slot_start+timedelta(minutes=hours.minutes)
                    and decision.slot_end <= datetime.combine(local.date(),hours.end,tzinfo=LOCAL)
                    for hours in schedule.sessions
                )
                if (not exact or local.date()!=schedule.day or schedule.hospital_id!=decision.hospital or schedule.state not in ("working","replacement")
                        or not schedule.effective_at<=now<decision.expiry<=min(authority_expiry,schedule.expires_at)
                        or decision.schedule_proof!=sha256(schedule.model_dump_json().encode()).hexdigest()):
                    raise ValueError()
            elif schedule is not None or decision.schedule_proof is not None:
                raise ValueError()
        except (ValueError, ConfirmationDenied, InvalidSignature):
            raise AdminDenied("untrusted_clinic_authority") from None
        with transaction(self.engine,"roghyar_staff_provisioner") as db:
            db.execute(text("""
                INSERT INTO roghyar.staff_clinic_approvals(id,actor,revision,hospital,appointment,expected_change,
                    slot_start,slot_end,action,expires_at,basis,proof_digest,proof_day,approver)
                VALUES(:id,:actor,:revision,:hospital,:appointment,:expected_change,:slot_start,:slot_end,:action,:expiry,:basis,
                    :schedule_proof,:day,:approver)
            """),decision.model_dump()|{"day":schedule.day if schedule else None})


class ClinicOperations:
    def __init__(self, staff:StaffService):
        self.staff=staff

    def apply(self,token,appointment:UUID,decision:UUID,key:str):
        key=TypeAdapter(Key).validate_python(key)
        with self.staff.authenticate(token) as (db,member):
            if member.staff_role not in ("hospital_admin","appointment_manager"):
                raise AdminDenied("forbidden")
            digest=hmac.digest(self.staff.key,b"staff-clinic-key:v1\0"+member.subject.bytes+key.encode(),sha256)
            result=db.execute(text("SELECT * FROM roghyar.apply_staff_clinic_decision(:appointment,:decision,:key)"),
                              {"appointment":appointment,"decision":decision,"key":digest}).one()
            if result.denial is not None:
                raise AdminDenied(result.denial)
            return {"appointment_id":appointment,"status":result.status,"event_id":result.event_id,
                    "provider_ack_status":"accepted" if result.status=="confirmed" else "unavailable",
                    "notification_status":"unavailable"}
