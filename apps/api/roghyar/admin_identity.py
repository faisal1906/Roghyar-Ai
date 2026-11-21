"""Private facility staff authentication, records and independently reviewed requests."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import hmac
import re
from secrets import token_urlsafe
from typing import Annotated, Literal, Protocol
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import AwareDatetime, Field, StringConstraints, model_validator
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .booking_reservations import Key
from .doctor_schedules import Schedule
from .hospital_search import SearchModel
from .red_flags import canonical
from .scoped_session import AuthorizationDenied, Principal, _scoped_transaction

Role = Literal["hospital_admin","appointment_manager","schedule_manager","provider_editor","auditor"]
Digest = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
Signature = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{128}$")]
Action = Literal["doctor_proposal","doctor_correction","doctor_withdrawal",
                 "schedule_proposal","schedule_correction","schedule_withdrawal"]
TOKEN = re.compile(r"staff1\.[A-Za-z0-9_-]{43}")
ISSUER = "roghyar-approved-staff-idp"
AUDIENCE = "roghyar-hospital-admin"


class AdminDenied(ValueError):
    pass


class Membership(SearchModel):
    subject: UUID
    hospital: UUID
    revision: Annotated[int, Field(ge=1,le=2147483647)]
    role: Role
    active: bool
    approver: UUID
    expiry: AwareDatetime
    evidence: Digest
    signature: Signature = Field(repr=False)

    def signed_bytes(self):
        return b"roghyar:staff-membership:v1\0" + canonical(self.model_dump(mode="json",exclude={"signature"}))


@dataclass(frozen=True)
class ProvisioningAuthority:
    key: Ed25519PublicKey
    hospitals: frozenset[UUID]
    starts: datetime
    expires: datetime


class Identity(SearchModel):
    subject: UUID
    issuer: Literal["roghyar-approved-staff-idp"]
    audience: Literal["roghyar-hospital-admin"]
    authenticated_at: AwareDatetime


class IdentityProvider(Protocol):
    def authenticate(self, proof: str) -> Identity: ...


class Proposal(SearchModel):
    target: UUID
    action: Action
    evidence: Digest
    expected_revision: Annotated[int, Field(ge=1,le=2147483647)] | None = None
    schedule: Schedule | None = None

    @model_validator(mode="after")
    def payload_matches_action(self):
        if (self.action in ("schedule_proposal","schedule_correction")) != (self.schedule is not None):
            raise ValueError("Invalid schedule submission")
        if self.action.endswith(("correction","withdrawal")) and self.expected_revision is None:
            raise ValueError("Current revision required")
        return self


class Page(SearchModel):
    section: Literal["doctors","schedules","appointments","audit"]
    limit: Annotated[int,Field(ge=1,le=50)] = 20


def subject_lock(db, subject):
    db.execute(text("SELECT pg_advisory_xact_lock(16100,hashtext(CAST(:subject AS text)))"),{"subject":subject})


@contextmanager
def transaction(engine, role):
    if engine.echo or not engine.hide_parameters:
        raise AdminDenied("unsafe_database")
    try:
        with _scoped_transaction(engine,Principal("anonymous",None),role,readonly=False) as db:
            db.execute(text("SET LOCAL statement_timeout='2000ms'"))
            db.execute(text("SET LOCAL lock_timeout='1500ms'"))
            yield db
    except (SQLAlchemyError, AuthorizationDenied):
        raise AdminDenied("dependency_unavailable") from None


class Provisioner:
    def __init__(self,engine,authorities,clock=lambda:datetime.now(UTC)):
        self.engine,self.authorities,self.clock=engine,dict(authorities),clock

    def record(self, approval: Membership, evidence: bytes):
        approval=Membership.model_validate(approval.model_dump())
        anchor=self.authorities.get(approval.approver)
        now=self.clock()
        if (anchor is None or approval.hospital not in anchor.hospitals or approval.approver==approval.subject
                or not anchor.starts<=now<approval.expiry<=anchor.expires
                or not isinstance(evidence,bytes) or not 0<len(evidence)<=65536
                or sha256(evidence).hexdigest()!=approval.evidence):
            raise AdminDenied("untrusted_provisioning")
        try:
            anchor.key.verify(bytes.fromhex(approval.signature),approval.signed_bytes())
        except InvalidSignature:
            raise AdminDenied("untrusted_provisioning") from None
        with transaction(self.engine,"roghyar_staff_provisioner") as db:
            subject_lock(db,approval.subject)
            prior=db.execute(text("""
                SELECT revision,hospital FROM roghyar.staff_memberships
                WHERE subject=:subject ORDER BY revision DESC LIMIT 1
            """),{"subject":approval.subject}).one_or_none()
            if approval.revision!=(1 if prior is None else prior.revision+1):
                raise AdminDenied("stale_revision")
            if prior is not None and prior.hospital not in anchor.hospitals:
                raise AdminDenied("untrusted_provisioning")
            db.execute(text("""
                INSERT INTO roghyar.staff_memberships(subject,revision,hospital,role,active,expires_at,approver,evidence_digest)
                VALUES(:subject,:revision,:hospital,:role,:active,:expiry,:approver,:digest)
            """),approval.model_dump()|{"digest":bytes.fromhex(approval.evidence)})


class StaffLogin:
    def __init__(self,engine,provider: IdentityProvider|None=None,clock=lambda:datetime.now(UTC)):
        self.engine,self.provider,self.clock=engine,provider,clock

    def login(self, proof: str) -> str:
        if self.provider is None or type(proof) is not str or not 1<=len(proof)<=4096:
            raise AdminDenied("unauthenticated")
        try:
            identity=Identity.model_validate(self.provider.authenticate(proof).model_dump())
        except Exception:
            raise AdminDenied("unauthenticated") from None
        now=self.clock()
        if not now-timedelta(minutes=2)<=identity.authenticated_at<=now:
            raise AdminDenied("unauthenticated")
        token="staff1."+token_urlsafe(32)
        with transaction(self.engine,"roghyar_staff_issuer") as db:
            subject_lock(db,identity.subject)
            membership=db.execute(text("""
                SELECT revision,active,expires_at FROM roghyar.staff_memberships
                WHERE subject=:subject ORDER BY revision DESC LIMIT 1
            """),{"subject":identity.subject}).one_or_none()
            if membership is None or not membership.active or membership.expires_at<=now:
                raise AdminDenied("unauthenticated")
            db.execute(text("""
                INSERT INTO roghyar.staff_sessions(id,subject,revision,digest,issued_at,expires_at)
                VALUES(:id,:subject,:revision,:digest,:issued,:expiry)
            """),{"id":uuid4(),"subject":identity.subject,"revision":membership.revision,
                  "digest":sha256(token.encode()).digest(),"issued":now,
                  "expiry":min(now+timedelta(minutes=10),membership.expires_at)})
        return token


PAGES = {
    "doctors": """SELECT id,full_name,specialty,published,verified FROM roghyar.doctors ORDER BY id LIMIT :limit""",
    "schedules": """SELECT doctor_id,revision,expires_at,schedule FROM roghyar.doctor_current
                    ORDER BY doctor_id LIMIT :limit""",
    "appointments": """SELECT id,doctor_id,slot_start,slot_end,status,status_changed_at FROM roghyar.appointments
                       ORDER BY slot_start DESC,id DESC LIMIT :limit""",
    "audit": """SELECT id,actor,revision,action,target,recorded_at FROM roghyar.staff_audit
                ORDER BY recorded_at DESC,id DESC LIMIT :limit""",
}


class StaffService:
    def __init__(self,engine,*,key:bytes):
        if type(key) is not bytes or len(key)!=32:
            raise ValueError("Dedicated staff replay key required")
        self.engine,self.key=engine,key

    @contextmanager
    def authenticate(self,token):
        if type(token) is not str or TOKEN.fullmatch(token) is None:
            raise AdminDenied("unauthenticated")
        with transaction(self.engine,"roghyar_staff_runtime") as db:
            db.execute(text("SELECT set_config('roghyar.staff_capability',:token,true)"),{"token":token})
            member=db.execute(text("SELECT * FROM roghyar.staff_access()")).one_or_none()
            if member is None:
                raise AdminDenied("unauthenticated")
            yield db,member

    def logout(self,token):
        with self.authenticate(token) as (db,_):
            db.execute(text("SELECT roghyar.staff_logout()"))

    def page(self,token,query:Page):
        query=Page.model_validate(query.model_dump())
        with self.authenticate(token) as (db,member):
            if (query.section=="audit" and member.staff_role not in ("hospital_admin","auditor")
                    or query.section=="appointments" and member.staff_role not in
                    ("hospital_admin","appointment_manager","auditor")):
                raise AdminDenied("forbidden")
            return {"facility":member.hospital,"role":member.staff_role,
                    "records":[dict(row) for row in db.execute(text(PAGES[query.section]),
                                                               {"limit":query.limit}).mappings()]}

    def propose(self,token,proposal:Proposal,key:Key):
        from pydantic import TypeAdapter
        key=TypeAdapter(Key).validate_python(key)
        proposal=Proposal.model_validate(proposal.model_dump())
        with self.authenticate(token) as (db,member):
            is_doctor=proposal.action.startswith("doctor_")
            if member.staff_role not in ("hospital_admin","provider_editor" if is_doctor else "schedule_manager"):
                raise AdminDenied("forbidden")
            payload=proposal.model_dump(mode="json")
            digest=hmac.digest(self.key,b"staff-key:v1\0"+member.subject.bytes+key.encode(),sha256)
            fingerprint=hmac.digest(self.key,b"staff-proposal:v1\0"+canonical(payload),sha256)
            params={"actor":member.subject,"revision":member.revision,"key":digest}
            db.execute(text("SELECT pg_advisory_xact_lock(:lock)"),{"lock":int.from_bytes(digest[:8],"big",signed=True)})
            prior=db.execute(text("""
                SELECT id,fingerprint FROM roghyar.staff_audit WHERE actor=:actor AND revision=:revision AND key_digest=:key
            """),params).one_or_none()
            if prior:
                if not hmac.compare_digest(prior.fingerprint,fingerprint):
                    raise AdminDenied("idempotency_conflict")
                return prior.id
            if proposal.action!="doctor_proposal":
                target=db.execute(text("""
                    SELECT d.id,c.revision FROM roghyar.doctors d LEFT JOIN roghyar.doctor_current c ON c.doctor_id=d.id
                    WHERE d.id=:id
                """),{"id":proposal.target}).one_or_none()
                if target is None:
                    raise AdminDenied("not_found")
                if proposal.expected_revision is not None and target.revision!=proposal.expected_revision:
                    raise AdminDenied("stale_revision")
            return db.scalar(text("""
                INSERT INTO roghyar.staff_audit(actor,revision,hospital,action,target,key_digest,fingerprint,submission)
                VALUES(:actor,:revision,:hospital,:action,:target,:key,:fingerprint,CAST(:submission AS jsonb))
                RETURNING id
            """),params|{"hospital":member.hospital,"action":proposal.action,"target":proposal.target,
                        "fingerprint":fingerprint,"submission":canonical(payload).decode()})
