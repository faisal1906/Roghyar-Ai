"""SYNTHETIC DEVELOPMENT ONLY. Live PostgreSQL and Redis with local provider doubles."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import os
from threading import Barrier
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import DBAPIError, OperationalError

from directory_fixtures import directory_actors, governed_fixtures
from test_appointment_lifecycle import CANCEL, lifecycle
from test_booking_reservations import PHONE, booking, new_key
from test_booking_slots import slots, submit
from test_database_security import database
from test_doctor_governance import doctor_actors, doctors
from test_otp_service import otp
from roghyar.booking_reservations import BookingDenied
from roghyar.main import app, create_app, engine
from roghyar.notification_consent import NotificationPreferences, Preference
from roghyar.notification_http import private_receipt_router
from roghyar.notification_provider import AppointmentSMS, BusinessWhatsApp, ProviderOutcome
from roghyar.notification_receipts import Receipt, Receipts
from roghyar.notification_templates import Catalog, ClinicApproval, NotificationDenied, Signer, Template
from roghyar.notification_worker import LEASE_TIME, NotificationWorker, SyntheticPermit
from roghyar.rate_limit import CounterUnavailable, Policy, RateLimiter


class Fake:
    def __init__(self, channel):
        self.channel = channel
        self.provider_id = "synthetic_" + channel
        self.calls = []
        self.mode = "accepted"
        self.in_flight = lambda: None

    def send(self, request, phone, locale, message, template, parameters):
        self.calls.append((request,phone,locale,message,template,parameters))
        self.in_flight()
        if self.mode == "crash":
            raise TimeoutError("SYNTHETIC")
        return ProviderOutcome(status=self.mode,message_id=f"test-{request.hex}" if self.mode == "accepted" else None,
                               retry_seconds=5 if self.mode=="retry" else None)


class System:
    def __init__(self, booking, lifecycle, worker_db, receipt_db):
        self.booking,self.lifecycle = booking,lifecycle
        self.worker_db,self.receipt_db = worker_db,receipt_db
        self.now = datetime.now(UTC)+timedelta(seconds=1)
        self.providers = {channel:Fake(channel) for channel in ("sms","whatsapp")}
        self.review,self.clinic,self.callback = (Ed25519PrivateKey.generate() for _ in range(3))
        self.quota_key = SecretStr(os.urandom(32).hex())
        self.preferences = NotificationPreferences(lifecycle.service)
        kinds = ("booking","cancelled","rescheduled","successor","reminder24","reminder2")
        templates = []
        for channel in self.providers:
            for kind in kinds:
                row = Template(channel=channel,locale="en",kind=kind,version=1,
                    approved_text=f"SYNTHETIC DEVELOPMENT ONLY {kind} {{when}}",
                    provider_template="synthetic_template" if channel=="whatsapp" else None,reviewer="review_copy",
                    expiry=self.now+timedelta(days=14),signature="0"*128)
                templates.append(row.model_copy(update={"signature":self.review.sign(row.signing_bytes()).hex()}))
        self.catalog = Catalog(templates, {"review_copy":self.signer(self.review,
            {("sms","en"),("whatsapp","en")})}, {"review_clinic":self.signer(self.clinic,
            {("reminder",str(booking.doctor.hospital_id))})})
        self.receipt_service = Receipts(receipt_db,{"synthetic_callback":self.signer(self.callback,
            {("receipt",p.provider_id) for p in self.providers.values()})},clock=lambda:self.now)

    def signer(self,key,scopes):
        return Signer(key.public_key(),self.now-timedelta(hours=1),self.now+timedelta(days=14),frozenset(scopes))

    def worker(self, **options):
        default = {"limiter":RateLimiter(self.booking.otp.counter,self.quota_key),"providers":self.providers,
                   "clock":lambda:self.now,"jitter":lambda _:0,"permit":SyntheticPermit(self.now+timedelta(days=10))}
        return NotificationWorker(self.worker_db,self.booking.otp.encryption,self.catalog,**(default|options))

    def start(self, channel="sms", locale="en", enable=True, reminders=True, future=False):
        create,manage = self.lifecycle.tokens()
        if enable:
            self.preferences.record(manage,Preference(channel=channel,locale=locale,enabled=True,reminders=reminders))
        key = new_key()
        if future:
            ref = self.booking.proof()
            submit(self.booking.confirmation_writer,self.booking.proof(day=ref.day+timedelta(days=7)))
            booked = self.booking.reserve(create,key=key,slot_start=self.booking.intervals[0].start_utc+timedelta(days=7))
        else:
            booked = self.booking.reserve(create,key=key)
        self.now = datetime.now(UTC)+timedelta(seconds=1)
        return booked,create,manage,key

    def jobs(self, kind=None):
        with engine.begin() as db:
            return db.execute(text("""
                SELECT j.* FROM roghyar.notification_outbox j JOIN roghyar.appointments a ON a.id=j.appointment_id
                WHERE a.doctor_id=:doctor AND (CAST(:kind AS text) IS NULL OR j.kind=:kind)
                ORDER BY j.created_at,j.id
            """),{"doctor":self.booking.doctor.doctor_id,"kind":kind}).all()

    def proof(self, appointment, **kwargs):
        row = ClinicApproval(**({"appointment":appointment.id,"hospital":appointment.hospital_id,
                               "slot_start":appointment.slot_start,"reviewer":"review_clinic",
                               "recorded_at":self.now,"expiry":appointment.slot_start,"signature":"0"*128}|kwargs))
        return row.model_copy(update={"signature":self.clinic.sign(row.signing_bytes()).hex()})

    def receipt(self, job, **changes):
        row = Receipt(**({"provider":job.provider,"key_id":"synthetic_callback","event_id":uuid4(),"job_id":job.id,
                         "message_id":job.message_id or f"test-{job.id.hex}","status":"delivered","occurred_at":self.now,
                         "expiry":self.now+timedelta(minutes=1),"signature":"0"*128}|changes))
        return row.model_copy(update={"signature":self.callback.sign(row.signing_bytes()).hex()})

    def confirmed(self, appointment):
        with engine.begin() as db:
            db.execute(text("UPDATE roghyar.appointments SET status='confirmed' WHERE id=:id"),
                       {"id":appointment.id})


@pytest.fixture
def notifications(booking,lifecycle):
    engines = []
    for role in ("notification_worker","notification_receipt"):
        with engine.begin() as db:
            db.execute(text(f"ALTER ROLE roghyar_{role} PASSWORD 'synthetic-notification-local'"))
        engines.append(create_engine(engine.url.set(username="roghyar_"+role,password="synthetic-notification-local"),
                                     hide_parameters=True))
    fixture = System(booking,lifecycle,*engines)
    try:
        yield fixture
    finally:
        for conn in engines:
            conn.dispose()
        with engine.begin() as db:
            db.execute(text("""
                DELETE FROM roghyar.notification_receipts WHERE job_id IN (SELECT id FROM roghyar.notification_outbox)
            """))
            db.execute(text("DELETE FROM roghyar.notification_outbox"))
            db.execute(text("DELETE FROM roghyar.notification_consent"))


@pytest.mark.parametrize("channel",["sms","whatsapp"])
@pytest.mark.parametrize("mode",["accepted","rate","timeout","redirect","oversized","wrong_request"])
def test_bk05_gateway_pinned_contract(channel,mode,monkeypatch):
    identity = uuid4()

    def send(request):
        import json

        payload = json.loads(request.content)
        assert request.headers["Idempotency-Key"] == str(identity)
        assert request.headers["Authorization"] == "Bearer SYNTHETIC-ONLY-CREDENTIAL"
        assert payload["to"] == "+923012345678"
        if channel == "whatsapp":
            assert payload["approved_sender"] == "SYNTHETIC_SENDER" and "text" not in payload
        else:
            assert payload["text"] == "SYNTHETIC"
        if mode == "timeout":
            raise httpx.ReadTimeout("SYNTHETIC")
        replies = {"rate":429,"redirect":302}
        content = (b"x"*2049 if mode=="oversized" else
                   json.dumps({"request_id":str(uuid4() if mode=="wrong_request" else identity),
                               "status":"accepted","message_id":"synthetic-job"}).encode())
        return httpx.Response(replies.get(mode,202),
            headers={"Content-Type":"application/json","Retry-After":"45"},stream=httpx.ByteStream(content))

    client = httpx.AsyncClient
    monkeypatch.setattr(httpx,"AsyncClient",lambda **kwargs:client(transport=httpx.MockTransport(send),**kwargs))
    gateway = AppointmentSMS if channel=="sms" else BusinessWhatsApp
    extra = {"sender":"SYNTHETIC_SENDER"} if channel=="whatsapp" else {}
    provider = gateway(f"https://8.8.8.8{gateway.path}",approved_hosts=frozenset({"8.8.8.8"}),
                       secret=SecretStr("SYNTHETIC-ONLY-CREDENTIAL"),**extra)
    outcome = provider.send(identity,PHONE,"en","SYNTHETIC","synthetic_template",())
    assert outcome.status == {"accepted":"accepted","rate":"retry","redirect":"failed"}.get(mode,"unknown")
    if mode == "rate":
        assert outcome.retry_seconds == 45


@pytest.mark.parametrize("channel",["sms","whatsapp"])
def test_bk05_consent_booking_replay_and_signed_delivery(channel,notifications,caplog):
    system = notifications
    booking,create,manage,key = system.start(channel=channel)
    assert system.booking.reserve(create,key=key) == booking
    job, = system.jobs()
    assert job.state=="pending" and job.channel==channel
    assert system.worker(permit=None).claim()==()
    assert system.preferences.status(manage,booking.id)=="unavailable"
    worker = system.worker()
    assert worker.run_once()==1
    accepted, = system.jobs()
    assert accepted.state=="accepted" and accepted.attempts==1
    assert system.preferences.status(manage,booking.id,approval_active=True)=="accepted"
    calls = system.providers[channel].calls
    assert len(calls)==1 and calls[0][1]=="+923012345678" and "SYNTHETIC" in calls[0][3]
    unused = "whatsapp" if channel=="sms" else "sms"
    assert system.providers[unused].calls==[]
    receipt = system.receipt(accepted)
    assert system.receipt_service.record(receipt.model_dump_json().encode())=="delivered"
    assert system.receipt_service.record(receipt.model_dump_json().encode())=="delivered"
    assert system.preferences.status(manage,booking.id,approval_active=True)=="delivered"
    assert system.booking.reserve(create,key=key)==booking
    history = system.lifecycle.service.mine(manage,__import__("roghyar.appointment_lifecycle",fromlist=["HistoryQuery"]).HistoryQuery())
    assert history.appointments[0].status=="pending" and history.appointments[0].provider_ack_status=="unavailable"
    assert PHONE not in caplog.text and calls[0][3] not in caplog.text


def test_bk05_no_default_consent_no_historical_backfill(notifications):
    system = notifications
    book,_,manage,_ = system.start(enable=False)
    assert system.jobs()==[]
    system.preferences.record(manage,Preference(channel="whatsapp",locale="en",enabled=True,reminders=True))
    assert system.jobs()==[] and system.preferences.status(manage,book.id,approval_active=True)=="unavailable"


@pytest.mark.parametrize("mode",["withdraw","missing_translation","revoked_template","phone_mismatch","identity_revoked"])
def test_bk05_eligibility_revalidated(mode,notifications):
    system = notifications
    book,_,manage,_ = system.start(locale="ps" if mode=="missing_translation" else "en")
    worker = system.worker()
    job, = worker.claim()
    if mode=="withdraw":
        system.preferences.record(manage,Preference(channel="sms",locale="en",enabled=False,reminders=False))
    elif mode=="revoked_template":
        system.catalog.template_keys["review_copy"] = replace(system.catalog.template_keys["review_copy"],revoked=True)
    elif mode in ("phone_mismatch","identity_revoked"):
        with engine.begin() as db:
            if mode=="identity_revoked":
                db.execute(text("UPDATE roghyar.otp_identities SET revoked_at=now()"))
            else:
                row, = system.jobs()
                encrypted = system.booking.otp.encryption.encrypt("+923012345679",record_id=row.patient_id,field_name="phone")
                db.execute(text("UPDATE roghyar.patients SET phone_ciphertext=:cipher WHERE id=:id"),
                           {"cipher":encrypted,"id":row.patient_id})
    worker.process(job)
    result, = system.jobs()
    assert result.state==("cancelled" if mode=="withdraw" else "failed")
    assert not system.providers["sms"].calls and not system.providers["whatsapp"].calls
