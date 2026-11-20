"""Explicitly configured provider gateway. BK-02 installs no default source."""
import asyncio
import base64
import binascii
import json

import httpx
from pydantic import SecretStr

from .directory_governance import reject_duplicate_keys
from .routing_provider import checked_endpoint
from .schedule_confirmation import ConfirmationDenied


class ScheduleGateway:
    def __init__(self, endpoint: str, approved_hosts: frozenset[str], credential: SecretStr):
        self.endpoint = checked_endpoint(endpoint, approved_hosts, path="/v1/schedule-confirmation")
        if not credential.get_secret_value():
            raise ValueError("Provider credential required")
        self.credential = credential

    def retrieve(self) -> tuple[bytes, bytes]:
        return asyncio.run(self._retrieve())

    async def _retrieve(self):
        try:
            async with asyncio.timeout(3), httpx.AsyncClient(
                trust_env=False, follow_redirects=False, timeout=2
            ) as client, client.stream("GET", self.endpoint, headers={
                "Authorization": "Bearer " + self.credential.get_secret_value(),
                "Accept-Encoding": "identity", "Cache-Control": "no-store",
            }) as response:
                if (response.status_code != 200
                        or response.headers.get("content-type", "").split(";")[0] != "application/json"
                        or response.headers.get("content-encoding", "identity") != "identity"):
                    raise ValueError()
                raw = bytearray()
                async for chunk in response.aiter_raw():
                    if len(raw) + len(chunk) > 100000:
                        raise ValueError()
                    raw.extend(chunk)
                result = json.loads(raw, object_pairs_hook=reject_duplicate_keys)
                if type(result) is not dict or set(result) != {"document", "evidence"}:
                    raise ValueError()
                return tuple(base64.b64decode(result[key], validate=True) for key in ("document", "evidence"))
        except (ValueError, TypeError, binascii.Error, RecursionError, TimeoutError, httpx.HTTPError):
            raise ConfirmationDenied("provider_unavailable") from None
