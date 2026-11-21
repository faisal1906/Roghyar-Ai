"""Verified private delivery callbacks. Clinic acceptance is outside this contract."""
from datetime import UTC, datetime, timedelta
import json
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .directory_governance import reject_duplicate_keys
from .hospital_search import SearchModel
from .notification_templates import Identifier, NotificationDenied, ProviderMessage, Signature, verify_artifact
from .red_flags import canonical
from .scoped_session import AuthorizationDenied, Principal, _scoped_transaction


class Receipt(SearchModel):
    provider: Identifier
    key_id: Identifier
    event_id: UUID
    job_id: UUID
    message_id: ProviderMessage
    status: Literal["accepted","delivered","failed","expired"]
    occurred_at: AwareDatetime
    expiry: AwareDatetime
    signature: Signature

    def signing_bytes(self):
        return b"roghyar:notification-provider-receipt:v1\0" + canonical(
            self.model_dump(mode="json",exclude={"signature"}))


class Receipts:
    def __init__(self, engine, authorities, clock=lambda: datetime.now(UTC)):
        if engine.echo or not engine.hide_parameters:
            raise ValueError("Unsafe callback configuration")
        self.engine,self.authorities,self.clock = engine, dict(authorities),clock

    def record(self, payload: bytes):
        now = self.clock()
        if type(payload) is not bytes or not 1 <= len(payload) <= 4096:
            raise NotificationDenied("invalid_receipt")
        try:
            json.loads(payload,object_pairs_hook=reject_duplicate_keys)
            receipt = Receipt.model_validate_json(payload)
            if (not now-timedelta(minutes=5) <= receipt.occurred_at <= now+timedelta(seconds=15)
                    or not now < receipt.expiry <= receipt.occurred_at+timedelta(minutes=5)):
                raise ValueError()
            verify_artifact(receipt,self.authorities.get(receipt.key_id),("receipt",receipt.provider),now)
        except (ValueError, RecursionError):
            raise NotificationDenied("invalid_receipt") from None
        data = receipt.model_dump(exclude={"signature","key_id","expiry"})
        try:
            with _scoped_transaction(self.engine,Principal("anonymous",None),"roghyar_notification_receipt",
                                     readonly=False) as db:
                db.execute(text("SET LOCAL statement_timeout='1500ms'"))
                job = db.execute(text("""
                    SELECT provider,state,message_id FROM roghyar.notification_outbox WHERE id=:job_id FOR UPDATE
                """),data).one_or_none()
                if (job is None or job.provider != receipt.provider
                        or job.message_id is not None and job.message_id != receipt.message_id):
                    raise NotificationDenied("invalid_receipt")
                prior = db.execute(text("""
                    SELECT job_id,message_id,status,occurred_at FROM roghyar.notification_receipts
                    WHERE provider=:provider AND event_id=:event_id
                """),data).one_or_none()
                if prior is not None:
                    if tuple(prior) != (receipt.job_id,receipt.message_id,receipt.status,receipt.occurred_at):
                        raise NotificationDenied("invalid_receipt")
                    return job.state
                if job.state not in ("submitting","accepted","unknown") and job.state != receipt.status:
                    raise NotificationDenied("invalid_transition")
                db.execute(text("""
                    INSERT INTO roghyar.notification_receipts(provider,event_id,job_id,message_id,status,occurred_at)
                    VALUES(:provider,:event_id,:job_id,:message_id,:status,:occurred_at)
                """),data)
                db.execute(text("""
                    UPDATE roghyar.notification_outbox SET state=:status,message_id=:message_id,
                        lease=NULL,worker=NULL,lease_until=NULL WHERE id=:job_id
                """),data)
                return receipt.status
        except (SQLAlchemyError,AuthorizationDenied):
            raise NotificationDenied("receipt_unavailable") from None
