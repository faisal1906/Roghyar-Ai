"""Fixed-endpoint SMS gateway. An acknowledgement is not delivery confirmation."""
import asyncio
from uuid import UUID

import httpx
from pydantic import SecretStr

from .routing_provider import checked_endpoint


class SMSUnavailable(RuntimeError):
    pass


class SMSGateway:
    def __init__(self, endpoint: str, *, approved_hosts: frozenset[str], secret: SecretStr):
        self.endpoint = checked_endpoint(endpoint, approved_hosts, path="/v1/otp")
        if not secret.get_secret_value():
            raise ValueError("SMS credential required")
        self.secret = secret

    def send(self, phone: str, code: str, challenge: UUID) -> bool:
        return asyncio.run(self.send_async(phone, code, challenge))

    async def send_async(self, phone, code, challenge):
        try:
            async with asyncio.timeout(3), httpx.AsyncClient(
                trust_env=False, follow_redirects=False, timeout=2
            ) as client, client.stream("POST", self.endpoint, headers={
                "Authorization": "Bearer " + self.secret.get_secret_value(),
                "Accept-Encoding": "identity", "Cache-Control": "no-store",
            }, json={"phone": phone, "code": code, "request_id": str(challenge)}) as response:
                if response.status_code != 202:
                    return False
                if (response.headers.get("content-type", "").split(";")[0] != "application/json"
                        or response.headers.get("content-encoding", "identity") != "identity"):
                    return False
                data = bytearray()
                async for chunk in response.aiter_raw():
                    if len(data) + len(chunk) > 2048:
                        return False
                    data.extend(chunk)
                import json
                from .directory_governance import reject_duplicate_keys
                return json.loads(data, object_pairs_hook=reject_duplicate_keys) == {
                    "request_id": str(challenge), "status": "accepted"}
        except (TimeoutError, httpx.HTTPError, ValueError, RecursionError):
            raise SMSUnavailable("provider_unavailable") from None
