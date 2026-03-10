from helpers.tool import Tool, Response
from plugins.signal.helpers.signal_client import get_signal_config
from plugins.signal.helpers.signal_bridge import (
    start_chat_bridge,
    stop_chat_bridge,
    get_bridge_status,
    add_chat_contact,
    remove_chat_contact,
    get_chat_contacts,
)
from plugins.signal.helpers.sanitize import require_auth, validate_phone_number


class SignalChat(Tool):
    """Manage the Signal chat bridge — a persistent polling service that lets
    users chat with Agent Zero through Signal messages."""

    async def execute(self, **kwargs) -> Response:
        config = get_signal_config(self.agent)
        try:
            require_auth(config)
        except ValueError as e:
            return Response(message=f"Auth error: {e}", break_loop=False)

        action = self.args.get("action", "status")

        if action == "start":
            return await self._start()
        elif action == "stop":
            return await self._stop()
        elif action == "add_contact":
            return self._add_contact()
        elif action == "remove_contact":
            return self._remove_contact()
        elif action == "list":
            return self._list_contacts()
        elif action == "status":
            return self._status()
        else:
            return Response(
                message=f"Unknown action '{action}'. "
                "Use: start, stop, add_contact, remove_contact, list, status.",
                break_loop=False,
            )

    async def _start(self) -> Response:
        """Start the Signal chat bridge."""
        status = get_bridge_status()
        if status.get("running"):
            return Response(
                message=f"Signal chat bridge is already running (status: {status.get('status')}).",
                break_loop=False,
            )

        self.set_progress("Starting Signal chat bridge...")
        try:
            await start_chat_bridge()
            status = get_bridge_status()
            contacts = get_chat_contacts()
            msg = f"Signal chat bridge started (status: {status.get('status')})."
            if contacts:
                msg += f"\nListening for messages from {len(contacts)} contact(s)."
            else:
                msg += (
                    "\nNo chat contacts configured yet. "
                    "Use action 'add_contact' to register a phone number."
                )
            return Response(message=msg, break_loop=False)
        except Exception as e:
            return Response(
                message=f"Error starting chat bridge: {type(e).__name__}: {e}",
                break_loop=False,
            )

    async def _stop(self) -> Response:
        """Stop the Signal chat bridge."""
        status = get_bridge_status()
        if not status.get("running"):
            return Response(message="Signal chat bridge is not running.", break_loop=False)

        self.set_progress("Stopping Signal chat bridge...")
        try:
            await stop_chat_bridge()
            return Response(message="Signal chat bridge stopped.", break_loop=False)
        except Exception as e:
            return Response(
                message=f"Error stopping chat bridge: {type(e).__name__}: {e}",
                break_loop=False,
            )

    def _add_contact(self) -> Response:
        """Register a contact for the chat bridge."""
        phone_number = self.args.get("phone_number", "")
        label = self.args.get("label", "")

        try:
            phone_number = validate_phone_number(phone_number)
        except ValueError as e:
            return Response(message=f"Error: {e}", break_loop=False)

        add_chat_contact(phone_number, label)
        msg = f"Contact {phone_number} added to the chat bridge"
        if label:
            msg += f" ({label})"
        msg += ". Messages from this number will be routed to Agent Zero."
        return Response(message=msg, break_loop=False)

    def _remove_contact(self) -> Response:
        """Remove a contact from the chat bridge."""
        phone_number = self.args.get("phone_number", "")
        try:
            phone_number = validate_phone_number(phone_number)
        except ValueError as e:
            return Response(message=f"Error: {e}", break_loop=False)

        remove_chat_contact(phone_number)
        return Response(
            message=f"Contact {phone_number} removed from the chat bridge.",
            break_loop=False,
        )

    def _list_contacts(self) -> Response:
        """List all registered chat bridge contacts."""
        contacts = get_chat_contacts()
        if not contacts:
            return Response(
                message="No chat bridge contacts configured. "
                "Use action 'add_contact' to register a phone number.",
                break_loop=False,
            )

        lines = [f"Chat bridge contacts ({len(contacts)}):"]
        for number, info in contacts.items():
            label = info.get("label", number)
            added = info.get("added_at", "unknown")
            lines.append(f"  - {label} ({number}, added: {added})")

        status = get_bridge_status()
        if status.get("running"):
            lines.append(f"\nBridge status: {status.get('status')}")
        else:
            lines.append("\nBridge status: not running")

        return Response(message="\n".join(lines), break_loop=False)

    def _status(self) -> Response:
        """Get chat bridge status."""
        status = get_bridge_status()
        contacts = get_chat_contacts()

        if not status.get("running"):
            msg = f"Signal chat bridge is **not running** (status: {status.get('status', 'stopped')})."
            if contacts:
                msg += f"\n{len(contacts)} contact(s) configured but bridge is offline."
            return Response(message=msg, break_loop=False)

        lines = [
            f"Signal chat bridge is **{status.get('status')}**",
            f"  Registered contacts: {len(contacts)}",
        ]

        for number, info in contacts.items():
            label = info.get("label", number)
            lines.append(f"    - {label} ({number})")

        return Response(message="\n".join(lines), break_loop=False)
