"""Leased PostgreSQL outbox. External I/O happens after the handoff transaction."""
from datetime import UTC, datetime, timedelta
from secrets import randbelow
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .http_safety import APIError
from .notification_provider import ProviderOutcome
from .notification_templates import ClinicApproval, NotificationDenied
from .otp_service import normalize_phone
from .rate_limit import Policy, RateLimiter
from .red_flags import canonical
from .scoped_session import AuthorizationDenied, Principal, _scoped_transaction

QUEUE = "roghyar.notification_outbox"
RELEASE = "lease=NULL,worker=NULL,lease_until=NULL"
READY = "state IN ('pending','scheduled','retry')"
RECIPIENT = text("SELECT * FROM roghyar.resolve_notification_recipient(:id,:lease,:now)")
MAX_ATTEMPTS = 4
LEASE_TIME = timedelta(seconds=30)
POLICIES = (
    ("patient_id", Policy("notify_recipient", 8, 86400)),
    ("appointment_id", Policy("notify_appointment", 8, 86400)),
    ("channel", Policy("notify_channel", 100, 86400)),
    ("kind", Policy("notify_template", 40, 86400)),
    (None, Policy("notify_aggregate", 150, 86400)),
)


class NotificationWorker:
    def __init__(self, engine, encryption, catalog, *, limiter: RateLimiter, providers=None,
                 enabled=False, clock=lambda: datetime.now(UTC), jitter=randbelow,
                 policies=POLICIES, concurrency=4):
        if engine.echo or not engine.hide_parameters or not 1 <= concurrency <= 16:
            raise ValueError("Unsafe notification execution configuration")
        self.engine, self.encryption, self.catalog = engine, encryption, catalog
        self.limiter, self.providers, self.enabled = limiter, dict(providers or {}), enabled
        self.clock, self.worker, self.jitter = clock, uuid4(), jitter
        self.policies, self.concurrency = tuple(policies), concurrency

    def transaction(self):
        return _scoped_transaction(self.engine, Principal("anonymous", None), "roghyar_notification_worker",
                                   readonly=False)

    def schedule(self, approval):
        approval = self.catalog.approve_reminders(approval, self.clock())
        with self.transaction() as db:
            return db.scalar(text("""
                SELECT roghyar.schedule_notification_reminders(:id,:starts,:now,:end,CAST(:proof AS jsonb))
            """), {"id": approval.appointment,"starts": approval.slot_start,"now": self.clock(),
                   "end": approval.expiry,"proof": approval.model_dump_json()})

    def recover(self, db, now):
        db.execute(text(f"""
            WITH stale AS (
                SELECT id FROM {QUEUE} WHERE (state IN ('leased','submitting') AND lease_until<=:now)
                 OR ({READY} AND expires_at<=:now)
                ORDER BY coalesce(lease_until,run_at),id LIMIT 50 FOR UPDATE SKIP LOCKED
            )
            UPDATE {QUEUE} j SET state=CASE WHEN j.state='submitting' THEN 'unknown'
                WHEN j.expires_at<=:now THEN 'expired' WHEN j.attempts>=4 THEN 'failed' ELSE 'retry' END,
                failure=CASE WHEN j.state='submitting' THEN 'timeout' WHEN j.expires_at<=:now THEN 'expired'
                    WHEN j.attempts>=4 THEN 'attempts' ELSE 'unavailable' END,{RELEASE}
            FROM stale WHERE j.id=stale.id
        """), {"now": now})

    def claim(self, limit=4):
        if type(limit) is not int or not 1 <= limit <= 16:
            raise ValueError("Invalid batch limit")
        if not self.enabled or not self.providers:
            return ()
        now = self.clock()
        with self.transaction() as db:
            self.recover(db, now)
            rows = db.execute(text(f"""
                WITH picked AS (
                    SELECT id FROM {QUEUE} WHERE {READY} AND run_at<=:now AND expires_at>:now
                        AND invalidated_at IS NULL AND channel=ANY(:channels) AND attempts<4
                    ORDER BY run_at,id LIMIT :limit FOR UPDATE SKIP LOCKED
                )
                UPDATE {QUEUE} j SET state='leased',lease=gen_random_uuid(),worker=:worker,lease_until=:until
                FROM picked WHERE j.id=picked.id
                RETURNING j.id,j.lease,j.channel,j.kind,j.patient_id,j.appointment_id
            """), {"now":now,"channels":list(self.providers),"limit":limit,"worker":self.worker,"until":now+LEASE_TIME})
            return tuple(rows)

    def quota(self, job):
        try:
            for field, policy in self.policies:
                self.limiter.enforce_identity(policy, field or "global", str(getattr(job,field)) if field else "all")
            return 0
        except APIError as exc:
            return max(1,min(3600,exc.retry_after or 30))

    def defer(self, db, scope, seconds, failure):
        db.execute(text(f"""
            UPDATE {QUEUE} SET state='retry',run_at=:next,failure=:failure,{RELEASE}
            WHERE id=:id AND lease=:lease AND worker=:worker AND state='leased'
        """), scope | {"next": self.clock()+timedelta(seconds=seconds),"failure":failure})

    def prepare(self, job, provider, retry):
        now = self.clock()
        scope = {"id":job.id,"lease":job.lease,"worker":self.worker,"now":now}
        with self.transaction() as db:
            db.execute(text("SET LOCAL statement_timeout='1500ms'"))
            row = db.execute(text(f"""
                SELECT * FROM {QUEUE} WHERE id=:id AND lease=:lease AND worker=:worker
                    AND state='leased' AND lease_until>:now FOR UPDATE
            """), scope).one_or_none()
            if row is None:
                return None
            if retry:
                self.defer(db,scope,retry,"quota")
                return None
            db.execute(text("SELECT pg_advisory_xact_lock(15901,hashtext(:provider))"),
                       {"provider": provider.provider_id})
            active = db.scalar(text(f"SELECT count(*) FROM {QUEUE} WHERE provider=:provider "
                                    "AND state='submitting' AND lease_until>:now"),
                               scope | {"provider":provider.provider_id})
            if active >= self.concurrency:
                self.defer(db,scope,5,"unavailable")
                return None
            recipient = db.execute(RECIPIENT,scope).one_or_none()
            try:
                if recipient is None:
                    raise NotificationDenied("recipient_unavailable")
                if row.clinic_proof is not None:
                    approval = self.catalog.approve_reminders(
                        ClinicApproval.model_validate_json(canonical(row.clinic_proof)), now)
                    if (approval.appointment != row.appointment_id or approval.slot_start != row.slot_start
                            or approval.expiry < row.expires_at):
                        raise NotificationDenied("unapproved_artifact")
                phone = normalize_phone(self.encryption.decrypt(
                    recipient.ciphertext,record_id=recipient.patient,field_name="phone"))
                if self.encryption.phone_lookup(phone) != recipient.lookup:
                    raise NotificationDenied("recipient_unavailable")
                render = self.catalog.render(row,now)
            except ValueError:
                db.execute(text(f"""
                    UPDATE {QUEUE} SET state='failed',failure='rejected',{RELEASE}
                    WHERE id=:id AND lease=:lease AND worker=:worker
                """),scope)
                return None
            db.execute(text(f"""
                UPDATE {QUEUE} SET state='submitting',attempts=attempts+1,provider=:provider
                WHERE id=:id AND lease=:lease AND worker=:worker
            """),scope | {"provider":provider.provider_id})
            return row,phone,render

    def still_eligible(self, job):
        with self.transaction() as db:
            return db.execute(RECIPIENT,{"id":job.id,"lease":job.lease,"now":self.clock()}).first() is not None

    def record(self, job, outcome, attempts):
        now = self.clock()
        state, failure, retry = outcome.status, None, now
        message = outcome.message_id if state == "accepted" else None
        if state == "accepted" and message is None:
            state = "unknown"
        if state == "retry":
            if attempts >= MAX_ATTEMPTS:
                state, failure = "failed","attempts"
            else:
                failure = "unavailable"
                seconds = max(outcome.retry_seconds or 0,15*(2**(attempts-1))+self.jitter(6))
                retry += timedelta(seconds=seconds)
        elif state == "unknown":
            failure = "timeout"
        elif state == "failed":
            failure = "rejected"
        with self.transaction() as db:
            db.execute(text(f"""
                UPDATE {QUEUE} SET state=CASE WHEN invalidated_at IS NOT NULL AND :state='retry'
                    THEN 'cancelled' ELSE :state END,message_id=:message,failure=:failure,run_at=:retry,{RELEASE}
                WHERE id=:id AND lease=:lease AND worker=:worker AND state='submitting' AND lease_until>:now
            """),{"id":job.id,"lease":job.lease,"worker":self.worker,"now":now,"state":state,
                  "message":message,"failure":failure,"retry":retry if state=="retry" else job.run_at})

    def process(self, job):
        if not self.enabled:
            return
        provider = self.providers.get(job.channel)
        if provider is None or provider.channel != job.channel:
            return
        try:
            prepared = self.prepare(job,provider,self.quota(job))
            if prepared is None:
                return
            row, phone, (message,template,parameters) = prepared
            if not self.still_eligible(job):
                outcome = ProviderOutcome(status="failed")
            else:
                try:
                    outcome = ProviderOutcome.model_validate(provider.send(
                        row.id, phone, row.locale, message, template, parameters).model_dump())
                except Exception:
                    outcome = ProviderOutcome(status="unknown")
            self.record(row,outcome,row.attempts+1)
        except (SQLAlchemyError, AuthorizationDenied):
            # The lease remains durable. A submitting lease recovers to unknown without re-sending.
            return

    def run_once(self, limit=4, *, stopped=lambda: False):
        """Bounded tick for a supervised executor. Closing prevents new provider calls."""
        if stopped():
            return 0
        batch = self.claim(limit)
        for job in batch:
            if stopped():
                break
            self.process(job)
        return len(batch)
