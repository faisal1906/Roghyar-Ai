"""Separately configured SMS and official WhatsApp business gateway adapters."""
import asyncio
import json
from typing import Annotated, Literal, Protocol
from uuid import UUID

import httpx
from pydantic import Field, SecretStr, StringConstraints, ValidationError

from .directory_governance import reject_duplicate_keys
from .hospital_search import SearchModel
from .notification_templates import Channel, Locale, ProviderMessage
from .otp_service import normalize_phone
from .routing_provider import checked_endpoint


class ProviderOutcome(SearchModel):
    status: Literal["accepted","retry","failed","unknown"]
    message_id: ProviderMessage | None = None
    retry_seconds: Annotated[int, Field(ge=1, le=86400)] | None = None


class GatewayResponse(SearchModel):
    request_id: UUID
    status: Literal["accepted"]
    message_id: ProviderMessage


class Provider(Protocol):
    provider_id: str
    channel: Channel

    def send(self, request_id, phone, locale, message, template, parameters) -> ProviderOutcome: ...


class Gateway:
    def __init__(self, endpoint: str, *, approved_hosts: frozenset[str], secret: SecretStr):
        self.endpoint = checked_endpoint(endpoint, approved_hosts, path=self.path)
        if not 16 <= len(secret.get_secret_value()) <= 512:
            raise ValueError("Notification provider credentials required")
        self.secret = secret

    def send(self, request_id: UUID, phone: str, locale: Locale, message: str, template, parameters):
        body = {"request_id": str(request_id), "to": normalize_phone(phone), "locale": locale}
        return asyncio.run(self._send(body | self.payload(message, template, parameters), request_id))

    async def _send(self, body, request_id):
        try:
            raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
            if len(raw) > 2048:
                return ProviderOutcome(status="failed")
            async with asyncio.timeout(3), httpx.AsyncClient(
                trust_env=False, follow_redirects=False, timeout=2
            ) as client, client.stream("POST", self.endpoint, headers={
                "Authorization": "Bearer " + self.secret.get_secret_value(),
                "Idempotency-Key": str(request_id), "Accept-Encoding": "identity", "Cache-Control": "no-store",
                "Content-Type": "application/json",
            }, content=raw) as response:
                if response.status_code == 429:
                    retry = response.headers.get("Retry-After", "")
                    seconds = int(retry) if retry.isascii() and retry.isdecimal() and 0 < len(retry) <= 5 else 30
                    return ProviderOutcome(status="retry", retry_seconds=max(1,min(86400,seconds)))
                if response.status_code in (400,401,403,404,422) or 300 <= response.status_code < 400:
                    return ProviderOutcome(status="failed")
                if (response.status_code != 202
                        or response.headers.get("content-type", "").split(";")[0] != "application/json"
                        or response.headers.get("content-encoding", "identity") != "identity"):
                    return ProviderOutcome(status="unknown")
                data = bytearray()
                async for chunk in response.aiter_raw():
                    if len(data) + len(chunk) > 2048:
                        return ProviderOutcome(status="unknown")
                    data.extend(chunk)
                json.loads(data,object_pairs_hook=reject_duplicate_keys)
                result = GatewayResponse.model_validate_json(data)
                if result.request_id == request_id:
                    return ProviderOutcome(status="accepted",message_id=result.message_id)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            return ProviderOutcome(status="retry")
        except (ValidationError, TimeoutError, httpx.HTTPError, ValueError, RecursionError):
            pass
        return ProviderOutcome(status="unknown")


class AppointmentSMS(Gateway):
    channel = "sms"
    provider_id = "appointment_sms"
    path = "/v1/appointment-sms"

    def payload(self, message, template, parameters):
        return {"text": message}


class BusinessWhatsApp(Gateway):
    channel = "whatsapp"
    provider_id = "business_whatsapp"
    path = "/v1/appointment-whatsapp"

    def __init__(self, endpoint, *, approved_hosts, secret, sender: str):
        super().__init__(endpoint, approved_hosts=approved_hosts, secret=secret)
        from pydantic import TypeAdapter
        self.sender = TypeAdapter(Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{6,64}$")]).validate_python(sender)

    def payload(self, message, template, parameters):
        if template is None:
            raise ValueError("Business template required")
        return {"approved_sender": self.sender, "approved_template": template, "parameters": parameters}
