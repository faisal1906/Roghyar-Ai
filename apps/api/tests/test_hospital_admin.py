"""SYNTHETIC DEVELOPMENT ONLY. Restricted PostgreSQL staff authorization."""
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from os import urandom
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from directory_fixtures import directory_actors, governed_fixtures
from test_appointment_lifecycle import lifecycle
from test_booking_reservations import booking
from test_booking_slots import slots
from test_notifications import notifications
from test_otp_service import otp
from test_database_security import database
from test_doctor_governance import doctor_actors, doctors
from roghyar.admin_http import private_admin_router
from roghyar.admin_identity import (AUDIENCE, ISSUER, AdminDenied, Identity, Membership, Page, Proposal,
                                    Provisioner, ProvisioningAuthority, StaffLogin, StaffService)
from roghyar.doctor_schedules import LOCAL
from roghyar.main import app, create_app, engine

ORIGIN = "https://synthetic-admin.example.invalid"
EVIDENCE = b"SYNTHETIC DEVELOPMENT ONLY. Staff authority fixture."


class Provider:
    def __init__(self):
        self.subjects = {}

    def authenticate(self, proof):
        return Identity(subject=self.subjects[proof],issuer=ISSUER,audience=AUDIENCE,
                        authenticated_at=datetime.now(UTC))


@pytest.fixture
def administration(doctors, governed_fixtures):
    first=doctors.candidate()
    doctors.activate(first)
    second=governed_fixtures.candidate()
    governed_fixtures.publish(second)
    connections={}
    for role in ("staff_provisioner","staff_issuer","staff_runtime"):
        with engine.begin() as owner:
            owner.execute(text(f"ALTER ROLE roghyar_{role} PASSWORD 'synthetic-staff-local'"))
        connections[role]=create_engine(engine.url.set(username=f"roghyar_{role}",password="synthetic-staff-local"),
                                        hide_parameters=True)
    key,approver=Ed25519PrivateKey.generate(),uuid4()
    now=datetime.now(UTC)
    provision=Provisioner(connections["staff_provisioner"],{
        approver:ProvisioningAuthority(key.public_key(),frozenset({first.hospital_id,second.hospital_id}),
                                         now-timedelta(hours=1),now+timedelta(days=1))})
    provider=Provider()
    login=StaffLogin(connections["staff_issuer"],provider)
    service=StaffService(connections["staff_runtime"],key=urandom(32))
    subjects=set()

    def approval(subject,**overrides):
        subjects.add(subject)
        row=Membership(**({"subject":subject,"hospital":first.hospital_id,"revision":1,"role":"hospital_admin",
                           "active":True,"approver":approver,"expiry":now+timedelta(hours=2),
                           "evidence":sha256(EVIDENCE).hexdigest(),"signature":"0"*128}|overrides))
        return row.model_copy(update={"signature":key.sign(row.signed_bytes()).hex()})

    def enroll(**overrides):
        subject,proof=uuid4(),uuid4().hex
        provision.record(approval(subject,**overrides),EVIDENCE)
        provider.subjects[proof]=subject
        return subject,proof,login.login(proof)

    try:
        yield first,second,service,login,provision,approval,enroll,connections
    finally:
        for connection in connections.values():
            connection.dispose()
        with engine.begin() as owner:
            for table,column in (("staff_audit","actor"),("staff_sessions","subject"),("staff_memberships","subject")):
                owner.execute(text(f"DELETE FROM roghyar.{table} WHERE {column}=ANY(:ids)"),{"ids":list(subjects)})


def test_bk06_facility_scope_and_permission(administration):
    first,second,service,_,_,_,enroll,_=administration
    _,_,local=enroll()
    _,_,remote=enroll(hospital=second.hospital_id)
    assert [record["id"] for record in service.page(local,Page(section="doctors"))["records"]]==[first.doctor_id]
    assert service.page(remote,Page(section="doctors"))["records"]==[]
    _,_,editor=enroll(role="provider_editor")
    for section in ("appointments","audit"):
        with pytest.raises(AdminDenied,match="^forbidden$"):
            service.page(editor,Page(section=section))
    request=Proposal(target=first.doctor_id,action="doctor_correction",expected_revision=first.revision,
                     evidence=sha256(EVIDENCE).hexdigest())
    with pytest.raises(AdminDenied,match="^not_found$"):
        service.propose(remote,request,uuid4().hex)
    key=uuid4().hex
    event=service.propose(editor,request,key)
    assert service.propose(editor,request,key)==event
    with pytest.raises(AdminDenied,match="^idempotency_conflict$"):
        service.propose(editor,request.model_copy(update={"evidence":"f"*64}),key)
    assert service.page(local,Page(section="audit"))["records"][0]["id"]==event
    assert service.page(remote,Page(section="audit"))["records"]==[]
    assert service.page(local,Page(section="doctors"))["records"][0]["verified"]


def test_bk06_live_membership_revision_and_logout(administration):
    _,_,service,login,provision,approval,enroll,_=administration
    subject,proof,token=enroll()
    provision.record(approval(subject,revision=2,role="auditor"),EVIDENCE)
    with pytest.raises(AdminDenied,match="^unauthenticated$"):
        service.page(token,Page(section="doctors"))
    next_token=login.login(proof)
    assert service.page(next_token,Page(section="audit"))["role"]=="auditor"
    service.logout(next_token)
    with pytest.raises(AdminDenied,match="^unauthenticated$"):
        service.page(next_token,Page(section="audit"))
    provision.record(approval(subject,revision=3,active=False,role="auditor"),EVIDENCE)
    with pytest.raises(AdminDenied,match="^unauthenticated$"):
        login.login(proof)
    with pytest.raises(AdminDenied,match="^stale_revision$"):
        provision.record(approval(subject,revision=2),EVIDENCE)


@pytest.mark.parametrize("kind",["signature","scope","self","evidence"])
def test_bk06_independent_provisioning(kind,administration):
    _,_,_,_,provision,approval,_,_=administration
    subject=uuid4()
    row=approval(subject,**({"hospital":uuid4()} if kind=="scope" else
                          {"approver":subject} if kind=="self" else {}))
    if kind=="signature":
        row=row.model_copy(update={"signature":"0"*128})
    with pytest.raises(AdminDenied,match="^untrusted_provisioning$"):
        provision.record(row,EVIDENCE if kind!="evidence" else b"wrong")


def test_bk06_database_privileges_and_public_closure(administration,database):
    _,_,service,_,_,_,enroll,connections=administration
    _,_,token=enroll()
    with connections["staff_runtime"].begin() as db:
        assert db.scalar(text("SELECT count(id) FROM roghyar.doctors"))==0
    with service.authenticate(token) as (db,member):
        for statement in ("SELECT * FROM roghyar.patients","SELECT * FROM roghyar.assessments",
                          "SELECT * FROM roghyar.staff_sessions",
                          "UPDATE roghyar.doctors SET verified=true",
                          "UPDATE roghyar.appointments SET status='confirmed'",
                          "DELETE FROM roghyar.staff_audit"):
            with pytest.raises(DBAPIError),db.begin_nested():
                db.execute(text(statement))
    with pytest.raises(DBAPIError),database.begin() as db:
        db.execute(text("SELECT * FROM roghyar.staff_memberships"))
    assert TestClient(app).get("/admin/doctors").status_code==404
    assert TestClient(app).post("/admin/session",json={}).status_code==404


def test_bk06_private_http(administration):
    _,_,service,login,_,_,enroll,_=administration
    _,proof,_=enroll()
    private=create_app(production=False)
    private.include_router(private_admin_router(login,service,origin=ORIGIN))
    client=TestClient(private,base_url=ORIGIN)
    assert client.post("/admin/session",json={"proof":proof}).status_code==403
    assert client.get("/admin/doctors",headers={"Origin":ORIGIN}).status_code==401
    response=client.post("/admin/session",json={"proof":proof},headers={"Origin":ORIGIN})
    assert response.status_code==200 and response.headers["cache-control"]=="no-store"
    headers={"Origin":ORIGIN,"Authorization":"Bearer "+response.json()["token"]}
    assert client.get("/admin/doctors",headers=headers).status_code==200
    assert client.get("/admin/doctors?hospital=other",headers=headers).status_code==422
    assert client.delete("/admin/session",headers=headers).json()=={"status":"revoked"}
    assert client.get("/admin/doctors",headers=headers).status_code==401


def test_bk06_clinic_confirmation_binding_and_notification(notifications, administration):
    from roghyar.admin_clinic import ClinicAuthority, ClinicDecision, ClinicOperations

    system=notifications
    _,_,service,_,_,_,enroll,connections=administration
    appointment,_,_,_=system.start(future=True)
    staff,_,token=enroll(role="appointment_manager")
    _,_,auditor=enroll(role="auditor")
    confirmation=system.booking.proof(day=appointment.slot_start.astimezone(LOCAL).date())
    reviewer,key=uuid4(),Ed25519PrivateKey.generate()
    now=datetime.now(UTC)
    issuer=ClinicAuthority(connections["staff_provisioner"],{reviewer:ProvisioningAuthority(
        key.public_key(),frozenset({appointment.hospital_id}),now-timedelta(minutes=1),now+timedelta(hours=1))},
        system.booking.trust)
    row=service.page(token,Page(section="appointments"))["records"][0]
    decision=ClinicDecision(id=uuid4(),actor=staff,hospital=appointment.hospital_id,revision=1,
        appointment=appointment.id,expected_change=row["status_changed_at"],slot_start=appointment.slot_start,
        slot_end=appointment.slot_end,action="confirm",expiry=now+timedelta(minutes=10),
        basis=sha256(EVIDENCE).hexdigest(),schedule_proof=sha256(confirmation.model_dump_json().encode()).hexdigest(),
        approver=reviewer,signature="0"*128)
    ops=ClinicOperations(service)
    try:
        with pytest.raises(AdminDenied,match="^not_found$"):
            ops.apply(token,appointment.id,decision.id,uuid4().hex)
        with pytest.raises(AdminDenied,match="^untrusted_clinic_authority$"):
            issuer.record(decision,EVIDENCE,confirmation)
        decision=decision.model_copy(update={"signature":key.sign(decision.signed_bytes()).hex()})
        issuer.record(decision,EVIDENCE,confirmation)
        with pytest.raises(AdminDenied,match="^forbidden$"):
            ops.apply(auditor,appointment.id,decision.id,uuid4().hex)
        request=uuid4().hex
        first=ops.apply(token,appointment.id,decision.id,request)
        assert first["status"]=="confirmed" and first["provider_ack_status"]=="accepted"
        assert ops.apply(token,appointment.id,decision.id,request)==first
        with pytest.raises(AdminDenied,match="^stale_revision$"):
            ops.apply(token,appointment.id,decision.id,uuid4().hex)
        assert len(system.jobs("clinic_confirmed")) == 1
        assert not system.providers["sms"].calls
    finally:
        with engine.begin() as owner:
            owner.execute(text("DELETE FROM roghyar.staff_clinic_events WHERE decision=:id"),{"id":decision.id})
            owner.execute(text("DELETE FROM roghyar.staff_clinic_approvals WHERE id=:id"),{"id":decision.id})
