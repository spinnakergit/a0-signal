"""Signal client — dual-mode support for integrated and external backends.

Mode: integrated (default)
    signal-cli native binary runs inside the A0 container as a supervisord
    service.  Communication is via JSON-RPC over HTTP (localhost:8080).

Mode: external
    A separate signal-cli-rest-api Docker container (bbernhard) exposes a
    REST API.  Communication is via the bbernhard REST endpoints.

Both modes provide the same public API so the rest of the plugin (tools,
bridge, tests) works transparently.

SECURITY:
  - Integrated mode binds to 127.0.0.1 only — not network-accessible.
  - External mode: The REST API has NO built-in auth; it must run on a
    private Docker network or behind an auth proxy.
  - This client adds an optional bearer token header for secured proxies.
"""

import asyncio
import os
import time
from typing import Optional

import httpx


# ---------------------------------------------------------------------------
# Configuration loader
# ---------------------------------------------------------------------------

def get_signal_config(agent=None) -> dict:
    """Load Signal config through the plugin framework with env var overrides."""
    try:
        from helpers import plugins
        config = plugins.get_plugin_config("signal", agent=agent) or {}
    except Exception:
        config = {}

    # Environment variables override file config
    if os.environ.get("SIGNAL_API_URL"):
        config.setdefault("api", {})["base_url"] = os.environ["SIGNAL_API_URL"]
    if os.environ.get("SIGNAL_API_TOKEN"):
        config.setdefault("api", {})["auth_token"] = os.environ["SIGNAL_API_TOKEN"]
    if os.environ.get("SIGNAL_PHONE_NUMBER"):
        config["phone_number"] = os.environ["SIGNAL_PHONE_NUMBER"]
    if os.environ.get("SIGNAL_MODE"):
        config.setdefault("api", {})["mode"] = os.environ["SIGNAL_MODE"]
    return config


# ---------------------------------------------------------------------------
# Factory — returns the appropriate client for the configured mode
# ---------------------------------------------------------------------------

def create_signal_client(config: dict = None, agent=None):
    """Create the appropriate Signal client based on config mode.

    Args:
        config: Plugin config dict.  If None, loaded via get_signal_config.
        agent: Optional agent for loading config.

    Returns:
        SignalClient (external mode) or SignalJsonRpcClient (integrated mode).
    """
    if config is None:
        config = get_signal_config(agent)

    api_config = config.get("api", {})
    mode = api_config.get("mode", "integrated")
    phone_number = config.get("phone_number", "")

    if mode == "external":
        return SignalClient(
            base_url=api_config.get("base_url", "http://signal-api:8080"),
            phone_number=phone_number,
            auth_token=api_config.get("auth_token", ""),
        )

    # Integrated mode — use JSON-RPC client
    from plugins.signal.helpers.signal_jsonrpc import SignalJsonRpcClient
    return SignalJsonRpcClient(
        base_url=api_config.get("base_url", "http://127.0.0.1:8080"),
        phone_number=phone_number,
    )


class SignalAPIError(Exception):
    """Error from the signal-cli-rest-api backend."""

    def __init__(self, status: int, body: str, endpoint: str):
        self.status = status
        self.body = body
        self.endpoint = endpoint
        super().__init__(f"Signal API error {status} on {endpoint}: {body}")


class SignalClient:
    """Async HTTP client for signal-cli-rest-api.

    All methods are async and use httpx for connection pooling and timeouts.
    """

    # Timeout for most operations (seconds)
    DEFAULT_TIMEOUT = 30.0
    # Longer timeout for receiving (may block while waiting)
    RECEIVE_TIMEOUT = 60.0

    def __init__(self, base_url: str, phone_number: str, auth_token: str = ""):
        if not base_url:
            raise ValueError(
                "Signal API base URL not configured. Set SIGNAL_API_URL env var "
                "or configure in Signal plugin settings."
            )
        if not phone_number:
            raise ValueError(
                "Signal phone number not configured. Set SIGNAL_PHONE_NUMBER env var "
                "or configure in Signal plugin settings."
            )
        self.base_url = base_url.rstrip("/")
        self.phone_number = phone_number
        self._client: Optional[httpx.AsyncClient] = None
        self._headers = {"Content-Type": "application/json"}
        if auth_token:
            self._headers["Authorization"] = f"Bearer {auth_token}"

    @classmethod
    def from_config(cls, agent=None) -> "SignalClient":
        """Create a client from the plugin configuration."""
        config = get_signal_config(agent)
        api_config = config.get("api", {})
        return cls(
            base_url=api_config.get("base_url", "http://signal-api:8080"),
            phone_number=config.get("phone_number", ""),
            auth_token=api_config.get("auth_token", ""),
        )

    async def _ensure_client(self):
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers=self._headers,
                timeout=httpx.Timeout(self.DEFAULT_TIMEOUT),
            )

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _request(
        self, method: str, endpoint: str, timeout: Optional[float] = None, **kwargs
    ) -> dict | list | None:
        """Make an HTTP request to the signal-cli-rest-api."""
        await self._ensure_client()
        url = f"{self.base_url}{endpoint}"

        req_timeout = httpx.Timeout(timeout) if timeout else None
        try:
            resp = await self._client.request(method, url, timeout=req_timeout, **kwargs)
        except httpx.TimeoutException:
            raise SignalAPIError(0, "Request timed out", endpoint)
        except httpx.ConnectError:
            raise SignalAPIError(
                0,
                f"Cannot connect to Signal API at {self.base_url}. "
                "Ensure signal-cli-rest-api is running and accessible.",
                endpoint,
            )

        if resp.status_code == 204:
            return None
        if resp.status_code >= 400:
            body = resp.text
            raise SignalAPIError(resp.status_code, body, endpoint)

        # Some endpoints return empty body on success
        if not resp.text.strip():
            return None
        return resp.json()

    # ------------------------------------------------------------------
    # Account / identity
    # ------------------------------------------------------------------

    async def get_about(self) -> dict:
        """Get signal-cli-rest-api version and capabilities."""
        return await self._request("GET", "/v1/about")

    async def get_identities(self) -> list:
        """List registered/linked identities."""
        # Different API versions use different endpoints
        try:
            return await self._request("GET", "/v1/accounts")
        except SignalAPIError:
            return []

    async def get_profile(self, recipient: str = "") -> dict:
        """Get profile information for a contact or self."""
        number = recipient or self.phone_number
        return await self._request(
            "GET", f"/v1/profiles/{number}"
        )

    # ------------------------------------------------------------------
    # Sending messages
    # ------------------------------------------------------------------

    async def send_message(
        self,
        recipients: list[str],
        message: str,
        attachments: Optional[list[str]] = None,
    ) -> dict:
        """Send a message to one or more recipients.

        Args:
            recipients: List of phone numbers (E.164) or group IDs.
            message: Text content.
            attachments: Optional list of base64-encoded attachment strings
                         or file paths on the signal-api container.
        """
        payload = {
            "message": message,
            "number": self.phone_number,
            "recipients": recipients,
        }
        if attachments:
            payload["base64_attachments"] = attachments
        return await self._request("POST", "/v2/send", json=payload)

    async def send_group_message(
        self,
        group_id: str,
        message: str,
        attachments: Optional[list[str]] = None,
    ) -> dict:
        """Send a message to a Signal group.

        Args:
            group_id: The group's internal ID (base64-encoded).
            message: Text content.
            attachments: Optional list of base64-encoded attachments.
        """
        payload = {
            "message": message,
            "number": self.phone_number,
            "recipients": [group_id],
        }
        if attachments:
            payload["base64_attachments"] = attachments
        return await self._request("POST", "/v2/send", json=payload)

    async def send_reaction(
        self,
        recipient: str,
        emoji: str,
        target_author: str,
        target_timestamp: int,
    ) -> dict:
        """Send a reaction to a specific message.

        Args:
            recipient: Phone number or group ID.
            emoji: The emoji to react with.
            target_author: Phone number of the message author.
            target_timestamp: Timestamp of the target message.
        """
        payload = {
            "recipient": recipient,
            "reaction": emoji,
            "target_author": target_author,
            "timestamp": target_timestamp,
            "number": self.phone_number,
        }
        return await self._request(
            "POST", f"/v1/reactions/{self.phone_number}", json=payload
        )

    async def send_typing(self, recipient: str) -> None:
        """Send a typing indicator."""
        payload = {"recipient": recipient}
        await self._request(
            "PUT", f"/v1/typing-indicator/{self.phone_number}", json=payload
        )

    async def mark_read(self, recipient: str, timestamps: list[int]) -> None:
        """Send read receipts for messages."""
        payload = {"recipient": recipient, "timestamps": timestamps}
        await self._request(
            "POST", f"/v1/receipts/{self.phone_number}", json=payload
        )

    # ------------------------------------------------------------------
    # Receiving messages
    # ------------------------------------------------------------------

    async def receive_messages(self, timeout_seconds: int = 1) -> list:
        """Receive pending messages.

        Returns a list of message envelope dicts from signal-cli.
        Each envelope contains: source, sourceNumber, timestamp, dataMessage, etc.

        Note: signal-cli-rest-api may use AUTO_RECEIVE_SCHEDULE internally.
        This endpoint returns messages that have been received since the last call.
        """
        return await self._request(
            "GET",
            f"/v1/receive/{self.phone_number}",
            timeout=self.RECEIVE_TIMEOUT,
            params={"timeout": str(timeout_seconds)},
        ) or []

    # ------------------------------------------------------------------
    # Groups
    # ------------------------------------------------------------------

    async def list_groups(self) -> list:
        """List all Signal groups for the registered number."""
        return await self._request(
            "GET", f"/v1/groups/{self.phone_number}"
        ) or []

    async def get_group(self, group_id: str) -> dict:
        """Get details for a specific group."""
        groups = await self.list_groups()
        for g in groups:
            if g.get("id") == group_id or g.get("internal_id") == group_id:
                return g
        raise SignalAPIError(404, f"Group not found: {group_id}", "list_groups")

    async def create_group(
        self, name: str, members: list[str], description: str = ""
    ) -> dict:
        """Create a new Signal group."""
        payload = {
            "name": name,
            "members": members,
            "description": description,
        }
        return await self._request(
            "POST", f"/v1/groups/{self.phone_number}", json=payload
        )

    async def update_group(
        self,
        group_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> dict:
        """Update group name or description."""
        payload = {}
        if name is not None:
            payload["name"] = name
        if description is not None:
            payload["description"] = description
        return await self._request(
            "PUT", f"/v1/groups/{self.phone_number}/{group_id}", json=payload
        )

    async def add_group_members(self, group_id: str, members: list[str]) -> dict:
        """Add members to a group."""
        payload = {"members": members}
        return await self._request(
            "POST", f"/v1/groups/{self.phone_number}/{group_id}/members", json=payload
        )

    async def remove_group_members(self, group_id: str, members: list[str]) -> dict:
        """Remove members from a group."""
        payload = {"members": members}
        return await self._request(
            "DELETE", f"/v1/groups/{self.phone_number}/{group_id}/members", json=payload
        )

    async def leave_group(self, group_id: str) -> dict:
        """Leave a group."""
        return await self._request(
            "DELETE", f"/v1/groups/{self.phone_number}/{group_id}"
        )

    # ------------------------------------------------------------------
    # Contacts
    # ------------------------------------------------------------------

    async def list_contacts(self) -> list:
        """List known contacts."""
        return await self._request(
            "GET", f"/v1/contacts/{self.phone_number}"
        ) or []

    async def update_contact(
        self, recipient: str, name: str = "", expiration_seconds: int = 0
    ) -> None:
        """Update contact name or set disappearing message timer."""
        payload = {"recipient": recipient}
        if name:
            payload["name"] = name
        if expiration_seconds > 0:
            payload["expiration_in_seconds"] = expiration_seconds
        await self._request(
            "PUT", f"/v1/contacts/{self.phone_number}", json=payload
        )

    async def get_identities_for(self, recipient: str) -> list:
        """Get identity/safety number information for a contact."""
        try:
            result = await self._request(
                "GET", f"/v1/identities/{self.phone_number}/{recipient}"
            )
            return result if isinstance(result, list) else [result] if result else []
        except SignalAPIError:
            return []

    async def trust_identity(
        self, recipient: str, safety_number: str, trust_all: bool = False
    ) -> None:
        """Trust a contact's identity (safety number verification).

        Args:
            recipient: Phone number to trust.
            safety_number: The verified safety number.
            trust_all: If True, trust all known keys (less secure).
        """
        payload = {
            "recipient": recipient,
            "trust_all_known_keys": trust_all,
        }
        if safety_number and not trust_all:
            payload["verified_safety_number"] = safety_number
        await self._request(
            "PUT", f"/v1/identities/{self.phone_number}/trust/{recipient}",
            json=payload,
        )

    # ------------------------------------------------------------------
    # Attachments
    # ------------------------------------------------------------------

    async def get_attachment(self, attachment_id: str) -> bytes:
        """Download an attachment by ID.

        Returns raw bytes of the attachment.
        """
        await self._ensure_client()
        url = f"{self.base_url}/v1/attachments/{attachment_id}"
        resp = await self._client.get(url)
        if resp.status_code >= 400:
            raise SignalAPIError(resp.status_code, resp.text, f"attachments/{attachment_id}")
        return resp.content

    # ------------------------------------------------------------------
    # QR code linking (for initial setup)
    # ------------------------------------------------------------------

    async def get_qr_link(self, device_name: str = "AgentZero") -> dict:
        """Start the linking process (link as secondary device).

        Returns a dict with a QR code URI to scan with the Signal app.
        """
        return await self._request(
            "GET",
            f"/v1/qrcodelink?device_name={device_name}",
            timeout=120.0,
        )

    async def register(
        self, phone_number: str, use_voice: bool = False, captcha: str = ""
    ) -> dict:
        """Register a new phone number (primary device).

        Requires SMS or voice verification.
        """
        payload = {"use_voice": use_voice}
        if captcha:
            payload["captcha"] = captcha
        return await self._request(
            "POST", f"/v1/register/{phone_number}", json=payload
        )

    async def verify(self, phone_number: str, code: str) -> dict:
        """Verify registration with the code received via SMS/voice."""
        payload = {"token": code}
        return await self._request(
            "POST", f"/v1/register/{phone_number}/verify/{code}", json=payload
        )


def format_messages(envelopes: list, include_timestamps: bool = True) -> str:
    """Format received Signal message envelopes into readable text.

    All external content is sanitized before reaching the LLM.
    """
    from plugins.signal.helpers.sanitize import sanitize_content, sanitize_username

    lines = []
    for env in envelopes:
        envelope = env.get("envelope", env)
        source = envelope.get("sourceNumber") or envelope.get("source", "Unknown")
        source_name = envelope.get("sourceName", "")
        data_msg = envelope.get("dataMessage")
        if not data_msg:
            continue

        text = data_msg.get("message", "")
        if not text:
            continue

        ts = data_msg.get("timestamp", envelope.get("timestamp", 0))
        display_name = sanitize_username(source_name) if source_name else source
        safe_text = sanitize_content(text)

        attachments = data_msg.get("attachments", [])
        attach_text = ""
        if attachments:
            names = [
                a.get("filename", a.get("contentType", "file"))
                for a in attachments
            ]
            attach_text = f" [Attachments: {', '.join(names)}]"

        if include_timestamps and ts:
            import datetime
            try:
                dt = datetime.datetime.fromtimestamp(ts / 1000, tz=datetime.timezone.utc)
                ts_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            except (OSError, ValueError):
                ts_str = str(ts)
            lines.append(f"[{ts_str}] {display_name} ({source}): {safe_text}{attach_text}")
        else:
            lines.append(f"{display_name} ({source}): {safe_text}{attach_text}")

    if not lines:
        return "No messages."
    return "\n".join(lines)
