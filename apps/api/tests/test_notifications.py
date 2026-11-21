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
from roghyar.appointment_lifecycle import HistoryQuery
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
    history = system.lifecycle.service.mine(manage,HistoryQuery())
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


@pytest.mark.parametrize("phase",["queued","leased","in_flight","acknowledged","successor"])
def test_bk05_reminder_lifecycle_races(phase,notifications):
    from roghyar.appointment_lifecycle import Mutation

    system = notifications
    booking,_,manage,_ = system.start(future=True)
    worker = system.worker()
    approval = system.proof(booking)
    assert worker.schedule(approval)==0  # Pending alone cannot authorize attendance.
    system.confirmed(booking)
    assert worker.schedule(approval)==2 and worker.schedule(approval)==0
    reminders = [j for j in system.jobs() if j.kind.startswith("reminder")]
    assert {j.run_at for j in reminders}=={booking.slot_start-timedelta(hours=24),
                                         booking.slot_start-timedelta(hours=2)}
    system.now=booking.slot_start-timedelta(hours=24)
    if phase=="successor":
        ref = system.booking.proof()
        submit(system.booking.confirmation_writer,system.booking.proof(day=ref.day+timedelta(days=14)))
        decision = Mutation(action="reschedule",new_slot_start=booking.slot_start+timedelta(days=7))
    else:
        decision = CANCEL
    key = new_key()

    def change():
        return system.lifecycle.service.mutate(manage,booking.id,decision,key)

    # The controlled delivery clock can advance independently of the OTP/session test clock.
    if phase in ("leased","in_flight","acknowledged"):
        job, = worker.claim()
        if phase=="in_flight":
            system.providers["sms"].in_flight=change
        if phase=="leased":
            change()
        worker.process(job)
        if phase=="acknowledged":
            change()
    else:
        change()
    result = change()
    originals = [j for j in system.jobs() if j.kind.startswith("reminder")]
    assert all(j.invalidated_at is not None for j in originals)
    assert {j.state for j in originals}==({"accepted","cancelled"} if phase in ("in_flight","acknowledged")
                                          else {"cancelled"})
    assert len(system.jobs("cancelled" if phase!="successor" else "rescheduled"))==1
    if phase=="successor":
        successor, = system.jobs("successor")
        assert successor.appointment_id==result.successor.id and successor.slot_start==result.successor.slot_start
        assert all(j.appointment_id==booking.id for j in originals)
    else:
        assert not system.jobs("successor")


@pytest.mark.parametrize("mode",["leased_restart","submitting_restart","timeout","ack_db_failure","parallel"])
def test_bk05_lease_fencing_and_unknown_recovery(mode,notifications,monkeypatch):
    system = notifications
    system.start()
    worker, other = system.worker(), system.worker()
    if mode=="parallel":
        barrier = Barrier(2,timeout=5)

        def start(instance):
            barrier.wait()
            return instance.claim()

        with ThreadPoolExecutor(2) as pool:
            a,b=pool.map(start,(worker,other))
        assert len(a)+len(b)==1
        (worker if a else other).process((a or b)[0])
        assert len(system.providers["sms"].calls)==1
        return
    job, = worker.claim()
    if mode=="submitting_restart":
        assert worker.prepare(job,system.providers["sms"],0)
    elif mode=="timeout":
        system.providers["sms"].mode="crash"
        worker.process(job)
    elif mode=="ack_db_failure":
        def disconnected(*_):
            raise OperationalError(None,None,RuntimeError("SYNTHETIC"),hide_parameters=True)
        monkeypatch.setattr(worker,"record",disconnected)
        worker.process(job)
    system.now+=LEASE_TIME+timedelta(seconds=1)
    if mode=="leased_restart":
        reclaimed, = other.claim()
        assert reclaimed.lease!=job.lease
        worker.process(job)
        assert not system.providers["sms"].calls
        other.process(reclaimed)
        assert system.jobs()[0].state=="accepted"
    else:
        assert other.claim()==()
        stale, = system.jobs()
        assert stale.state=="unknown" and stale.attempts==1
        assert len(system.providers["sms"].calls)==(0 if mode=="submitting_restart" else 1)
        assert system.receipt_service.record(system.receipt(stale).model_dump_json().encode())=="delivered"


@pytest.mark.parametrize("mode",["redis_down","quota","retry_budget","wrong_channel"])
def test_bk05_quota_retries_and_no_fallback(mode,notifications):
    system = notifications
    system.start()
    if mode=="retry_budget":
        worker = system.worker()
        system.providers["sms"].mode="retry"
        for attempt in range(1,5):
            assert worker.run_once()==1
            job, = system.jobs()
            assert job.attempts==attempt
            assert job.state==("retry" if attempt<4 else "failed")
            system.now+=timedelta(minutes=3)
        assert worker.run_once()==0 and len(system.providers["sms"].calls)==4
    elif mode=="wrong_channel":
        assert system.worker(providers={"whatsapp":system.providers["whatsapp"]}).claim()==()
    else:
        class Down:
            def consume(self,*_):
                raise CounterUnavailable()
        if mode=="redis_down":
            worker=system.worker(limiter=RateLimiter(Down(),system.quota_key))
        else:
            policy=Policy("synthetic_limit_test",1,600)
            limiter=RateLimiter(system.booking.otp.counter,system.quota_key)
            locator=system.jobs()[0].appointment_id
            limiter.enforce_identity(policy,"appointment_id",str(locator))
            worker=system.worker(limiter=limiter,policies=(("appointment_id",policy),))
        assert worker.run_once()==1
        job, = system.jobs()
        assert job.state=="retry" and job.attempts==0 and job.failure=="quota"
        assert not system.providers["sms"].calls
    assert not system.providers["whatsapp"].calls


@pytest.mark.parametrize("attack",["signature","provider","message","job","expired","duplicate","terminal"])
def test_bk05_callback_authority_and_conflicts(attack,notifications):
    system = notifications
    system.start()
    system.worker().run_once()
    job, = system.jobs()
    overrides = {"provider":{"provider":"other_provider"},"message":{"message_id":"other-message"},
                 "job":{"job_id":uuid4()},"expired":{"occurred_at":system.now-timedelta(minutes=10)}}
    invalid=system.receipt(job,**overrides.get(attack,{}))
    if attack=="signature":
        invalid=invalid.model_copy(update={"signature":"0"*128})
    if attack in ("duplicate","terminal"):
        valid=system.receipt(job)
        assert system.receipt_service.record(valid.model_dump_json().encode())=="delivered"
        invalid=system.receipt(job,status="failed",event_id=valid.event_id if attack=="duplicate" else uuid4())
    with pytest.raises(NotificationDenied):
        system.receipt_service.record(invalid.model_dump_json().encode())
    assert system.jobs()[0].state==("delivered" if attack in ("duplicate","terminal") else "accepted")


@pytest.mark.parametrize("stage",["booking","lifecycle"])
def test_bk05_atomic_outbox_rollback(stage,notifications):
    system=notifications
    create,manage=system.lifecycle.tokens()
    system.preferences.record(manage,Preference(channel="sms",locale="en",enabled=True,reminders=False))
    service=system.booking.service
    call=lambda:system.booking.reserve(create)
    expected=0
    event_table="roghyar.booking_events"
    if stage=="lifecycle":
        original=call()
        expected=1
        service=system.lifecycle.service
        call=lambda:service.mutate(manage,original.id,CANCEL,new_key())
        event_table="roghyar.appointment_lifecycle_events"

    def abort(conn,cursor,statement,parameters,context,executemany):
        if "INSERT INTO "+ event_table in statement:
            raise OperationalError(None,None,RuntimeError("SYNTHETIC"),hide_parameters=True)

    event.listen(service.engine,"after_cursor_execute",abort)
    try:
        from roghyar.appointment_lifecycle import LifecycleDenied
        with pytest.raises((BookingDenied,LifecycleDenied)):
            call()
    finally:
        event.remove(service.engine,"after_cursor_execute",abort)
    assert len(system.jobs())==expected
    assert not system.providers["sms"].calls


def test_bk05_private_callback_rls_and_metrics(notifications,database):
    system=notifications
    system.start()
    worker=system.worker()
    worker.run_once()
    job, = system.jobs()
    for conn,statement in ((database,"SELECT * FROM roghyar.notification_outbox"),
                           (system.worker_db,"SELECT phone_ciphertext FROM roghyar.patients"),
                           (system.worker_db,"SELECT verifier FROM roghyar.otp_challenges"),
                           (system.worker_db,"SELECT * FROM roghyar.assessments"),
                           (system.worker_db,"UPDATE roghyar.notification_outbox SET state='delivered'"),
                           (system.receipt_db,"UPDATE roghyar.appointments SET status='confirmed'"),
                           (system.receipt_db,"DELETE FROM roghyar.notification_receipts")):
        with pytest.raises(DBAPIError),conn.begin() as db:
            db.execute(text(statement))
    assert TestClient(app).post("/private/notification-receipts", json={}).status_code==404
    private=create_app(production=False)
    private.include_router(private_receipt_router(system.receipt_service))
    client=TestClient(private,base_url="https://synthetic.example.invalid")
    assert client.post("/private/notification-receipts",json={}).status_code==403
    response=client.post("/private/notification-receipts",json=system.receipt(job).model_dump(mode="json"))
    assert response.status_code==200 and response.headers["cache-control"]=="no-store"
    with system.worker_db.begin() as db:
        assert db.execute(text("SELECT * FROM roghyar.resolve_notification_recipient(:job,:lease,now())"),
                          {"job":job.id,"lease":uuid4()}).all()==[]
    assert worker.metrics().get("delivered")=={"jobs":1,"attempts":1}
