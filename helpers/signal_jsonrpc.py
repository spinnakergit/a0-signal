"""JSON-RPC client for the native signal-cli daemon.

signal-cli's built-in HTTP daemon (``signal-cli daemon --http``) exposes a
JSON-RPC 2.0 interface.  This module provides the same public API as the REST
client (``SignalClient``) so that the rest of the plugin (tools, bridge,
tests) can work transparently with either backend.

Endpoints used:
  POST /api/v1/rpc   — JSON-RPC method calls
  GET  /api/v1/check  — health check (200 OK)
"""

import asyncio
import itertools
from typing import Optional

import httpx

from plugins.signal.helpers.signal_client import SignalAPIError


_id_counter = itertools.count(1)


class SignalJsonRpcClient:
    """Async client that speaks JSON-RPC to a local signal-cli daemon."""

    DEFAULT_TIMEOUT = 30.0
    RECEIVE_TIMEOUT = 60.0

    def __init__(self, base_url: str, phone_number: str):
        if not phone_number:
            raise ValueError(
                "Signal phone number not configured. Set SIGNAL_PHONE_NUMBER env var "
                "or configure in Signal plugin settings."
            )
        self.base_url = (base_url or "http://127.0.0.1:8080").rstrip("/")
        self.phone_number = phone_number
        self._client: Optional[httpx.AsyncClient] = None

    async def _ensure_client(self):
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers={"Content-Type": "application/json"},
                timeout=httpx.Timeout(self.DEFAULT_TIMEOUT),
            )

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _rpc(self, method: str, params: Optional[dict] = None,
                   timeout: Optional[float] = None) -> dict | list | None:
        """Call a JSON-RPC method on the signal-cli daemon."""
        await self._ensure_client()
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "id": str(next(_id_counter)),
        }
        # Always include the account unless it's a multi-account method
        effective_params = {}
        if method not in ("listAccounts", "version"):
            effective_params["account"] = self.phone_number
        if params:
            effective_params.update(params)
        if effective_params:
            payload["params"] = effective_params

        url = f"{self.base_url}/api/v1/rpc"
        req_timeout = httpx.Timeout(timeout) if timeout else None
        try:
            resp = await self._client.post(url, json=payload, timeout=req_timeout)
        except httpx.TimeoutException:
            raise SignalAPIError(0, "Request timed out", f"rpc/{method}")
        except httpx.ConnectError:
            raise SignalAPIError(
                0,
                f"Cannot connect to signal-cli daemon at {self.base_url}. "
                "Ensure signal-cli is running (supervisorctl status signal_cli).",
                f"rpc/{method}",
            )

        if resp.status_code >= 400:
            raise SignalAPIError(resp.status_code, resp.text, f"rpc/{method}")

        data = resp.json()
        if "error" in data and data["error"]:
            err = data["error"]
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            raise SignalAPIError(-1, msg, f"rpc/{method}")
        return data.get("result")

    # ------------------------------------------------------------------
    # Health / meta
    # ------------------------------------------------------------------

    async def get_about(self) -> dict:
        """Get daemon version info."""
        result = await self._rpc("version")
        return {"versions": result} if result else {}

    async def get_identities(self) -> list:
        """List registered accounts."""
        try:
            result = await self._rpc("listAccounts")
            return result if isinstance(result, list) else []
        except SignalAPIError:
            return []

    async def get_profile(self, recipient: str = "") -> dict:
        """Get profile information for a contact."""
        number = recipient or self.phone_number
        try:
            result = await self._rpc("getUserStatus", {"recipient": [number]})
            if isinstance(result, list) and result:
                return result[0]
            return result or {}
        except SignalAPIError:
            return {}

    # ------------------------------------------------------------------
    # Sending messages
    # ------------------------------------------------------------------

    async def send_message(
        self,
        recipients: list[str],
        message: str,
        attachments: Optional[list[str]] = None,
    ) -> dict:
        params = {"recipient": recipients, "message": message}
        if attachments:
            params["attachment"] = attachments
        result = await self._rpc("send", params)
        return result or {}

    async def send_group_message(
        self,
        group_id: str,
        message: str,
        attachments: Optional[list[str]] = None,
    ) -> dict:
        params = {"groupId": group_id, "message": message}
        if attachments:
            params["attachment"] = attachments
        result = await self._rpc("send", params)
        return result or {}

    async def send_reaction(
        self,
        recipient: str,
        emoji: str,
        target_author: str,
        target_timestamp: int,
    ) -> dict:
        params = {
            "recipient": [recipient],
            "emoji": emoji,
            "targetAuthor": target_author,
            "targetTimestamp": target_timestamp,
        }
        result = await self._rpc("sendReaction", params)
        return result or {}

    async def send_typing(self, recipient: str) -> None:
        await self._rpc("sendTyping", {"recipient": [recipient]})

    async def mark_read(self, recipient: str, timestamps: list[int]) -> None:
        for ts in timestamps:
            await self._rpc("sendReceipt", {
                "recipient": recipient,
                "type": "read",
                "targetTimestamp": [ts],
            })

    # ------------------------------------------------------------------
    # Receiving messages
    # ------------------------------------------------------------------

    async def receive_messages(self, timeout_seconds: int = 1) -> list:
        """Receive pending messages from signal-cli.

        The daemon JSON-RPC ``receive`` method returns a list of envelopes,
        each wrapped as ``{"envelope": {...}}``.
        """
        result = await self._rpc("receive", timeout=self.RECEIVE_TIMEOUT)
        if not result:
            return []
        # Normalise: signal-cli returns flat envelopes, REST wraps in {envelope: ...}
        normalised = []
        for item in (result if isinstance(result, list) else [result]):
            if "envelope" in item:
                normalised.append(item)
            else:
                normalised.append({"envelope": item})
        return normalised

    # ------------------------------------------------------------------
    # Groups
    # ------------------------------------------------------------------

    async def list_groups(self) -> list:
        result = await self._rpc("listGroups")
        return result if isinstance(result, list) else []

    async def get_group(self, group_id: str) -> dict:
        groups = await self.list_groups()
        for g in groups:
            if g.get("id") == group_id or g.get("groupId") == group_id:
                return g
        raise SignalAPIError(404, f"Group not found: {group_id}", "listGroups")

    async def create_group(
        self, name: str, members: list[str], description: str = ""
    ) -> dict:
        params = {"name": name, "member": members}
        if description:
            params["description"] = description
        result = await self._rpc("updateGroup", params)
        return result or {}

    async def update_group(
        self,
        group_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> dict:
        params = {"groupId": group_id}
        if name is not None:
            params["name"] = name
        if description is not None:
            params["description"] = description
        result = await self._rpc("updateGroup", params)
        return result or {}

    async def add_group_members(self, group_id: str, members: list[str]) -> dict:
        result = await self._rpc("updateGroup", {
            "groupId": group_id,
            "member": members,
        })
        return result or {}

    async def remove_group_members(self, group_id: str, members: list[str]) -> dict:
        result = await self._rpc("updateGroup", {
            "groupId": group_id,
            "removeMember": members,
        })
        return result or {}

    async def leave_group(self, group_id: str) -> dict:
        result = await self._rpc("quitGroup", {"groupId": group_id})
        return result or {}

    # ------------------------------------------------------------------
    # Contacts
    # ------------------------------------------------------------------

    async def list_contacts(self) -> list:
        result = await self._rpc("listContacts")
        return result if isinstance(result, list) else []

    async def update_contact(
        self, recipient: str, name: str = "", expiration_seconds: int = 0
    ) -> None:
        params = {"recipient": recipient}
        if name:
            params["name"] = name
        if expiration_seconds > 0:
            params["expirationInSeconds"] = expiration_seconds
        await self._rpc("updateContact", params)

    async def get_identities_for(self, recipient: str) -> list:
        try:
            result = await self._rpc("listIdentities", {"number": recipient})
            return result if isinstance(result, list) else [result] if result else []
        except SignalAPIError:
            return []

    async def trust_identity(
        self, recipient: str, safety_number: str, trust_all: bool = False
    ) -> None:
        params = {"recipient": recipient}
        if trust_all:
            params["trustAllKnownKeys"] = True
        elif safety_number:
            params["verifiedSafetyNumber"] = safety_number
        await self._rpc("trust", params)

    # ------------------------------------------------------------------
    # Attachments
    # ------------------------------------------------------------------

    async def get_attachment(self, attachment_id: str) -> bytes:
        """Attachments in JSON-RPC mode are stored locally by signal-cli.

        This returns the path or raises an error — direct download not
        available through JSON-RPC, only through file system access.
        """
        raise SignalAPIError(
            501,
            "Direct attachment download not supported in integrated mode. "
            "Attachments are stored on disk by signal-cli.",
            "get_attachment",
        )

    # ------------------------------------------------------------------
    # Registration / linking
    # ------------------------------------------------------------------

    async def get_qr_link(self, device_name: str = "AgentZero") -> dict:
        result = await self._rpc("startLink", timeout=120.0)
        if isinstance(result, dict):
            return result
        # result is typically the device link URI string
        return {"deviceLinkUri": str(result)} if result else {}

    async def finish_link(self, device_name: str = "AgentZero") -> dict:
        result = await self._rpc("finishLink", {
            "deviceName": device_name,
        }, timeout=120.0)
        return result or {}

    async def register(
        self, phone_number: str, use_voice: bool = False, captcha: str = ""
    ) -> dict:
        params = {"account": phone_number, "voice": use_voice}
        if captcha:
            params["captcha"] = captcha
        result = await self._rpc("register", params)
        return result or {}

    async def verify(self, phone_number: str, code: str) -> dict:
        result = await self._rpc("verify", {
            "account": phone_number,
            "verificationCode": code,
        })
        return result or {}

    # ------------------------------------------------------------------
    # Health check (not JSON-RPC, uses built-in endpoint)
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Check if the daemon is responding."""
        await self._ensure_client()
        try:
            resp = await self._client.get(
                f"{self.base_url}/api/v1/check",
                timeout=httpx.Timeout(5.0),
            )
            return resp.status_code == 200
        except Exception:
            return False
