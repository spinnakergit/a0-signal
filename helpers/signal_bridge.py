"""Persistent Signal chat bridge for Agent Zero.

Polls the signal-cli-rest-api for incoming messages and routes them through
Agent Zero's LLM. Supports restricted mode (conversation only) and elevated
mode (full agent loop with authentication).

SECURITY MODEL:
  - Restricted mode (default): Uses call_utility_model() — NO tools, NO code
    execution, NO file access. The LLM cannot perform system operations.
  - Elevated mode (opt-in): Authenticated users get full agent loop access via
    context.communicate(). Requires: allow_elevated=true in config + runtime
    auth via "!auth <key>" in Signal. Sessions expire after configurable timeout.
  - Per-number rate limiting prevents abuse.
  - All incoming content is sanitized before reaching the LLM.
"""

import asyncio
import collections
import hmac
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("signal_chat_bridge")

# Singleton bridge state
_bridge_task: Optional[asyncio.Task] = None
_bridge_running: bool = False
_bridge_status: str = "stopped"

CHAT_STATE_FILE = "chat_bridge_state.json"


# ---------------------------------------------------------------------------
# Persistent state (allowed numbers for chat bridge)
# ---------------------------------------------------------------------------

def _get_state_path() -> Path:
    candidates = [
        Path(__file__).parent.parent / "data" / CHAT_STATE_FILE,
        Path("/a0/usr/plugins/signal/data") / CHAT_STATE_FILE,
        Path("/a0/plugins/signal/data") / CHAT_STATE_FILE,
        Path("/git/agent-zero/usr/plugins/signal/data") / CHAT_STATE_FILE,
    ]
    for p in candidates:
        if p.exists():
            return p
    path = candidates[0]
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_chat_state() -> dict:
    path = _get_state_path()
    if path.exists():
        with open(path, "r") as f:
            return json.load(f)
    return {"contacts": {}, "contexts": {}}


def save_chat_state(state: dict):
    from plugins.signal.helpers.sanitize import secure_write_json
    secure_write_json(_get_state_path(), state)


def add_chat_contact(phone_number: str, label: str = ""):
    """Register a phone number for the chat bridge."""
    state = load_chat_state()
    state.setdefault("contacts", {})[phone_number] = {
        "label": label or phone_number,
        "added_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    save_chat_state(state)


def remove_chat_contact(phone_number: str):
    state = load_chat_state()
    state.get("contacts", {}).pop(phone_number, None)
    state.get("contexts", {}).pop(phone_number, None)
    save_chat_state(state)


def get_chat_contacts() -> dict:
    return load_chat_state().get("contacts", {})


def get_context_id(contact_key: str) -> Optional[str]:
    return load_chat_state().get("contexts", {}).get(contact_key)


def set_context_id(contact_key: str, context_id: str):
    state = load_chat_state()
    state.setdefault("contexts", {})[contact_key] = context_id
    save_chat_state(state)


# ---------------------------------------------------------------------------
# Chat Bridge
# ---------------------------------------------------------------------------

class SignalChatBridge:
    """Polls for Signal messages and routes them through Agent Zero."""

    MAX_CHAT_MESSAGE_LENGTH = 4000
    MAX_HISTORY_MESSAGES = 20
    RATE_LIMIT_MAX = 10
    RATE_LIMIT_WINDOW = 60  # seconds
    AUTH_MAX_FAILURES = 5
    AUTH_FAILURE_WINDOW = 300  # 5 min lockout

    CHAT_SYSTEM_PROMPT = (
        "You are a friendly, helpful AI assistant chatting with users on Signal.\n\n"
        "IMPORTANT CONSTRAINTS:\n"
        "- You are a conversational chat bot ONLY. You have NO access to tools, files, "
        "commands, terminals, or any system resources.\n"
        "- If users ask you to run commands, access files, list directories, execute code, "
        "or perform any system operations, explain that you don't have those capabilities.\n"
        "- NEVER fabricate or make up file listings, directory contents, command outputs, "
        "or system information. You genuinely do not have access to any of these.\n"
        "- Be helpful, friendly, and conversational within these constraints.\n"
        "- You can help with general knowledge, answer questions, have discussions, "
        "write text, brainstorm ideas, and more — just not anything involving system access.\n"
        "- Each message shows the sender's phone number or name. Respond naturally.\n"
        "- IMPORTANT: Signal messages are end-to-end encrypted. Maintain the trust "
        "that comes with this secure channel. Do not expose sensitive information.\n"
    )

    def __init__(self):
        self._rate_limits: dict[str, collections.deque] = {}
        self._conversations: dict[str, list[dict]] = {}
        self._elevated_sessions: dict[str, dict] = {}
        self._auth_failures: dict[str, collections.deque] = {}
        self._client = None

    def _get_config(self) -> dict:
        """Load config, preferring direct JSON read to avoid import shadowing.

        When running inside the standalone bridge runner (run_signal_bridge.py),
        the A0 plugin framework's get_plugin_config() can fail because
        plugins/signal/helpers/ shadows /a0/helpers/. Reading config.json
        directly is more reliable and avoids the dependency on the plugin
        framework being fully initialized.
        """
        # Try direct JSON read first (most reliable in bridge runner context)
        for config_path in [
            Path("/a0/usr/plugins/signal/config.json"),
            Path("/a0/plugins/signal/config.json"),
            Path(__file__).parent.parent / "config.json",
        ]:
            try:
                if config_path.exists():
                    return json.loads(config_path.read_text())
            except Exception:
                continue

        # Fallback to plugin framework
        try:
            from plugins.signal.helpers.signal_client import get_signal_config
            return get_signal_config()
        except Exception:
            return {}

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def _is_elevated(self, phone_number: str) -> bool:
        config = self._get_config()
        if not config.get("chat_bridge", {}).get("allow_elevated", False):
            return False
        session = self._elevated_sessions.get(phone_number)
        if not session:
            return False
        timeout = config.get("chat_bridge", {}).get("session_timeout", 3600)
        if timeout > 0 and time.monotonic() - session["at"] > timeout:
            del self._elevated_sessions[phone_number]
            return False
        return True

    def _get_auth_key(self, config: dict) -> str:
        bridge_config = config.get("chat_bridge", {})
        auth_key = bridge_config.get("auth_key", "")
        if not auth_key and bridge_config.get("allow_elevated", False):
            from plugins.signal.helpers.sanitize import generate_auth_key
            auth_key = generate_auth_key()
            bridge_config["auth_key"] = auth_key
            config["chat_bridge"] = bridge_config
            try:
                from plugins.signal.helpers.sanitize import secure_write_json
                config_candidates = [
                    Path("/a0/usr/plugins/signal/config.json"),
                    Path("/a0/plugins/signal/config.json"),
                    Path(__file__).parent.parent / "config.json",
                ]
                for cp in config_candidates:
                    if cp.exists():
                        existing = json.loads(cp.read_text())
                        existing.setdefault("chat_bridge", {})["auth_key"] = auth_key
                        secure_write_json(cp, existing)
                        logger.info("Auto-generated auth key for elevated mode")
                        break
            except Exception as e:
                logger.warning(f"Could not persist auto-generated auth key: {type(e).__name__}")
        return auth_key

    # ------------------------------------------------------------------
    # Auth command handling
    # ------------------------------------------------------------------

    async def _handle_auth_command(self, phone_number: str, text: str) -> Optional[str]:
        """Handle !auth, !deauth, !status commands.

        Returns a response string if the message was a command, None otherwise.
        """
        text = text.strip()

        if text.lower() == "!deauth":
            if phone_number in self._elevated_sessions:
                del self._elevated_sessions[phone_number]
                logger.info(f"Elevated session ended: {phone_number}")
                return "Session ended. Back to restricted mode."
            return "No active elevated session."

        if text.lower() == "!status":
            if self._is_elevated(phone_number):
                session = self._elevated_sessions[phone_number]
                elapsed = int(time.monotonic() - session["at"])
                config = self._get_config()
                timeout = config.get("chat_bridge", {}).get("session_timeout", 3600)
                if timeout > 0:
                    remaining = max(0, timeout - elapsed)
                    expire_info = f"Session expires in {remaining // 3600}h {(remaining % 3600) // 60}m"
                else:
                    expire_info = "Session does not expire"
                return f"Mode: Elevated (full agent access)\n{expire_info}. Send !deauth to end."
            config = self._get_config()
            elevated_available = config.get("chat_bridge", {}).get("allow_elevated", False)
            if elevated_available:
                return "Mode: Restricted (chat only). Send !auth <key> to elevate."
            return "Mode: Restricted (chat only). Elevated mode is not enabled."

        if text.lower().startswith("!auth"):
            config = self._get_config()
            if not config.get("chat_bridge", {}).get("allow_elevated", False):
                return "Elevated mode is not enabled in the configuration."

            auth_key = self._get_auth_key(config)
            if not auth_key:
                return "Elevated mode is enabled but no auth key could be generated."

            # Check auth failure rate limit
            now = time.monotonic()
            if phone_number not in self._auth_failures:
                self._auth_failures[phone_number] = collections.deque()
            failures = self._auth_failures[phone_number]
            while failures and now - failures[0] > self.AUTH_FAILURE_WINDOW:
                failures.popleft()
            if len(failures) >= self.AUTH_MAX_FAILURES:
                return "Too many failed attempts. Please wait before trying again."

            parts = text.split(maxsplit=1)
            provided_key = parts[1].strip() if len(parts) > 1 else ""

            # Constant-time comparison to prevent timing attacks
            if provided_key and hmac.compare_digest(provided_key, auth_key):
                self._elevated_sessions[phone_number] = {
                    "at": now,
                    "number": phone_number,
                }
                timeout = config.get("chat_bridge", {}).get("session_timeout", 3600)
                if timeout > 0:
                    hours = timeout // 3600
                    mins = (timeout % 3600) // 60
                    parts_str = []
                    if hours:
                        parts_str.append(f"{hours}h")
                    if mins:
                        parts_str.append(f"{mins}m")
                    duration = " ".join(parts_str) or "0m"
                    expire_msg = f"Session expires in {duration}."
                else:
                    expire_msg = "Session does not expire."
                logger.info(f"Elevated session granted: {phone_number}")
                return (
                    f"Elevated session active. {expire_msg} "
                    f"You now have full Agent Zero access. Send !deauth to end."
                )
            else:
                failures.append(now)
                remaining = self.AUTH_MAX_FAILURES - len(failures)
                logger.warning(f"Failed auth attempt: {phone_number}")
                return f"Authentication failed. {remaining} attempt(s) remaining."

        return None  # Not a command

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    def _check_rate_limit(self, phone_number: str) -> Optional[str]:
        now = time.monotonic()
        if phone_number not in self._rate_limits:
            self._rate_limits[phone_number] = collections.deque()
        timestamps = self._rate_limits[phone_number]
        while timestamps and now - timestamps[0] > self.RATE_LIMIT_WINDOW:
            timestamps.popleft()
        if len(timestamps) >= self.RATE_LIMIT_MAX:
            return f"Rate limit: max {self.RATE_LIMIT_MAX} messages per {self.RATE_LIMIT_WINDOW}s. Please wait."
        timestamps.append(now)
        return None

    # ------------------------------------------------------------------
    # Message processing
    # ------------------------------------------------------------------

    async def process_message(
        self, phone_number: str, text: str, source_name: str = "",
        attachments: Optional[list] = None,
    ) -> str:
        """Process an incoming Signal message and return the response text."""
        from plugins.signal.helpers.sanitize import sanitize_content, sanitize_username

        # Check if it's a command
        if text.strip().startswith("!"):
            cmd_response = await self._handle_auth_command(phone_number, text)
            if cmd_response is not None:
                return cmd_response

        # Content length limit
        if len(text) > self.MAX_CHAT_MESSAGE_LENGTH:
            return (
                f"Message too long ({len(text)} chars). "
                f"Max: {self.MAX_CHAT_MESSAGE_LENGTH}."
            )

        # Rate limiting
        rate_msg = self._check_rate_limit(phone_number)
        if rate_msg:
            return rate_msg

        # Route based on elevation status
        is_elevated = self._is_elevated(phone_number)

        try:
            if is_elevated:
                return await self._get_elevated_response(
                    phone_number, text, source_name, attachments
                )
            else:
                return await self._get_agent_response(
                    phone_number, text, source_name
                )
        except Exception as e:
            logger.error(f"Agent error: {type(e).__name__}: {e}")
            return "An error occurred while processing your message."

    # ------------------------------------------------------------------
    # Restricted mode: direct LLM call, NO tools
    # ------------------------------------------------------------------

    async def _get_agent_response(
        self, phone_number: str, text: str, source_name: str = ""
    ) -> str:
        """Get LLM response via direct model call (no agent loop, no tools).

        SECURITY: Intentionally bypasses the full agent loop. The LLM is called
        directly via call_utility_model(), providing NO tool access.
        """
        try:
            from agent import AgentContext, AgentContextType
            from initialize import initialize_agent

            context_id = get_context_id(phone_number)
            context = None

            if context_id:
                context = AgentContext.get(context_id)

            if context is None:
                config = initialize_agent()
                context = AgentContext(config=config, type=AgentContextType.USER)
                set_context_id(phone_number, context.id)
                logger.info(f"Created new context {context.id} for {phone_number}")

            agent = context.agent0

            from plugins.signal.helpers.sanitize import sanitize_content, sanitize_username
            display_name = sanitize_username(source_name) if source_name else phone_number
            safe_text = sanitize_content(text)

            # Per-contact conversation history
            if phone_number not in self._conversations:
                self._conversations[phone_number] = []
            history = self._conversations[phone_number]
            history.append({"role": "user", "name": display_name, "content": safe_text})

            if len(history) > self.MAX_HISTORY_MESSAGES:
                self._conversations[phone_number] = history[-self.MAX_HISTORY_MESSAGES:]
                history = self._conversations[phone_number]

            formatted = []
            for msg in history:
                if msg["role"] == "user":
                    formatted.append(f"{msg['name']}: {msg['content']}")
                else:
                    formatted.append(f"Assistant: {msg['content']}")
            conversation_text = "\n".join(formatted)

            response = await agent.call_utility_model(
                system=self.CHAT_SYSTEM_PROMPT,
                message=conversation_text,
            )

            history.append({"role": "assistant", "content": response})
            return response if isinstance(response, str) else str(response)

        except ImportError:
            return await self._get_agent_response_http(phone_number, text)

    # ------------------------------------------------------------------
    # Elevated mode: full agent loop with tools
    # ------------------------------------------------------------------

    async def _get_elevated_response(
        self, phone_number: str, text: str, source_name: str = "",
        attachments: Optional[list] = None,
    ) -> str:
        """Route through the full Agent Zero agent loop.

        SECURITY: Only called for users who have authenticated via !auth <key>.
        """
        try:
            from agent import AgentContext, AgentContextType, UserMessage
            from initialize import initialize_agent

            context_id = get_context_id(phone_number)
            context = None

            if context_id:
                context = AgentContext.get(context_id)

            if context is None:
                config = initialize_agent()
                context = AgentContext(config=config, type=AgentContextType.USER)
                set_context_id(phone_number, context.id)
                logger.info(f"Created new elevated context {context.id} for {phone_number}")

            from plugins.signal.helpers.sanitize import sanitize_content, sanitize_username
            display_name = sanitize_username(source_name) if source_name else phone_number
            safe_text = sanitize_content(text)
            prefixed_text = (
                f"[Signal Chat Bridge - authenticated message from {display_name}]\n"
                f"{safe_text}"
            )

            # Handle image attachments
            attachment_paths = []
            temp_files = []
            if attachments:
                import tempfile
                for att in attachments:
                    try:
                        att_id = att.get("id", "")
                        content_type = att.get("contentType", "")
                        if att_id and content_type.startswith("image/"):
                            from plugins.signal.helpers.signal_client import create_signal_client
                            client = create_signal_client()
                            img_bytes = await client.get_attachment(att_id)
                            await client.close()
                            suffix = "." + content_type.split("/")[-1]
                            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
                            tmp.write(img_bytes)
                            tmp.close()
                            attachment_paths.append(tmp.name)
                            temp_files.append(tmp.name)
                    except Exception:
                        pass

            user_msg = UserMessage(message=prefixed_text, attachments=attachment_paths)
            task = context.communicate(user_msg)
            result = await task.result()

            # Clean up temp files
            for path in temp_files:
                try:
                    os.unlink(path)
                except OSError:
                    pass

            return result if isinstance(result, str) else str(result)

        except ImportError:
            return await self._get_agent_response_http(phone_number, text)

    # ------------------------------------------------------------------
    # HTTP fallback
    # ------------------------------------------------------------------

    async def _get_agent_response_http(self, phone_number: str, text: str) -> str:
        """Fallback: route through Agent Zero's HTTP API."""
        import httpx

        config = self._get_config()
        api_port = config.get("chat_bridge", {}).get("api_port", 80)
        api_key = config.get("chat_bridge", {}).get("api_key", "")

        context_id = get_context_id(phone_number) or ""

        async with httpx.AsyncClient(timeout=300.0) as client:
            payload = {
                "message": text,
                "context_id": context_id,
            }
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["X-API-KEY"] = api_key

            resp = await client.post(
                f"http://localhost:{api_port}/api/api_message",
                json=payload,
                headers=headers,
            )
            if resp.status_code != 200:
                return f"Agent API error ({resp.status_code}): {resp.text}"
            data = resp.json()

            if data.get("context_id"):
                set_context_id(phone_number, data["context_id"])

            return data.get("response", "No response from agent.")


# ---------------------------------------------------------------------------
# Bridge lifecycle
# ---------------------------------------------------------------------------

_bridge_instance: Optional[SignalChatBridge] = None


async def _poll_loop():
    """Main polling loop — receives messages and processes them."""
    global _bridge_running, _bridge_status, _bridge_instance

    from plugins.signal.helpers.signal_client import get_signal_config, create_signal_client

    _bridge_running = True
    _bridge_status = "connecting"

    if _bridge_instance is None:
        _bridge_instance = SignalChatBridge()

    try:
        config = get_signal_config()
        client = create_signal_client(config)

        # Verify connection
        try:
            about = await client.get_about()
            _bridge_status = "connected"
            logger.info(f"Signal chat bridge connected (API version: {about})")
        except Exception as e:
            _bridge_status = "connected"  # May still work even if /about fails
            logger.info(f"Signal chat bridge started (could not verify API: {e})")

        poll_interval = config.get("polling", {}).get("interval_seconds", 30)
        bridge_config = config.get("chat_bridge", {})
        allowed_numbers = bridge_config.get("allowed_numbers", [])
        chat_contacts = get_chat_contacts()

        while _bridge_running:
            try:
                envelopes = await client.receive_messages(timeout_seconds=1)

                for env in envelopes or []:
                    envelope = env.get("envelope", env)
                    source = envelope.get("sourceNumber") or envelope.get("source", "")
                    source_name = envelope.get("sourceName", "")
                    data_msg = envelope.get("dataMessage")

                    if not data_msg or not source:
                        continue

                    text = data_msg.get("message", "")
                    if not text:
                        continue

                    # Contact allowlist check
                    if allowed_numbers and source not in allowed_numbers:
                        logger.debug(f"Ignoring message from non-allowed number: {source}")
                        continue

                    # Chat contacts check (if configured, only respond to registered contacts)
                    if chat_contacts and source not in chat_contacts:
                        logger.debug(f"Ignoring message from non-registered contact: {source}")
                        continue

                    # Process the message
                    attachments = data_msg.get("attachments", [])
                    logger.info(f"Processing message from {source}")

                    # Send typing indicator
                    try:
                        await client.send_typing(source)
                    except Exception:
                        pass

                    response = await _bridge_instance.process_message(
                        phone_number=source,
                        text=text,
                        source_name=source_name,
                        attachments=attachments,
                    )

                    # Send response
                    try:
                        await client.send_message(
                            recipients=[source],
                            message=response,
                        )
                    except Exception as e:
                        logger.error(f"Failed to send response to {source}: {e}")

                    # Send read receipt
                    ts = data_msg.get("timestamp", envelope.get("timestamp"))
                    if ts:
                        try:
                            await client.mark_read(source, [ts])
                        except Exception:
                            pass

            except Exception as e:
                logger.error(f"Poll loop error: {type(e).__name__}: {e}")

            await asyncio.sleep(poll_interval)

    except Exception as e:
        logger.error(f"Bridge fatal error: {type(e).__name__}: {e}")
    finally:
        _bridge_running = False
        _bridge_status = "stopped"
        if client:
            await client.close()


async def start_chat_bridge() -> None:
    """Start the Signal chat bridge as a background polling task."""
    global _bridge_task, _bridge_running, _bridge_status

    if _bridge_running:
        return

    _bridge_task = asyncio.create_task(_poll_loop())
    # Wait a moment for the connection to establish
    await asyncio.sleep(2)

    if not _bridge_running:
        raise RuntimeError("Bridge failed to start. Check Signal API configuration.")


async def stop_chat_bridge() -> None:
    """Stop the Signal chat bridge."""
    global _bridge_task, _bridge_running, _bridge_status

    _bridge_running = False
    _bridge_status = "stopping"

    if _bridge_task:
        _bridge_task.cancel()
        try:
            await _bridge_task
        except (asyncio.CancelledError, Exception):
            pass
        _bridge_task = None

    _bridge_status = "stopped"


def get_bridge_status() -> dict:
    """Get current bridge status."""
    global _bridge_running, _bridge_status

    contacts = get_chat_contacts()
    return {
        "running": _bridge_running,
        "status": _bridge_status,
        "contacts": len(contacts),
    }
