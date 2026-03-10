from helpers.tool import Tool, Response
from plugins.signal.helpers.signal_client import (
    SignalAPIError, get_signal_config, create_signal_client,
)
from plugins.signal.helpers.sanitize import (
    require_auth, validate_phone_number, sanitize_username,
)


class SignalContacts(Tool):
    """Manage Signal contacts — list, update names, verify safety numbers,
    and set disappearing message timers."""

    async def execute(self, **kwargs) -> Response:
        action = self.args.get("action", "list")

        config = get_signal_config(self.agent)
        try:
            require_auth(config)
        except ValueError as e:
            return Response(message=f"Auth error: {e}", break_loop=False)

        try:
            client = create_signal_client(agent=self.agent)

            if action == "list":
                return await self._list(client)
            elif action == "update":
                return await self._update(client)
            elif action == "identity":
                return await self._identity(client)
            elif action == "trust":
                return await self._trust(client)
            elif action == "disappearing":
                return await self._set_disappearing(client)
            else:
                return Response(
                    message=f"Unknown action '{action}'. "
                    "Use: list, update, identity, trust, disappearing.",
                    break_loop=False,
                )

        except SignalAPIError as e:
            return Response(message=f"Signal API error: {e}", break_loop=False)
        except Exception as e:
            return Response(
                message=f"Error with Signal contacts: {type(e).__name__}: {e}",
                break_loop=False,
            )

    async def _list(self, client) -> Response:
        """List all known contacts."""
        self.set_progress("Listing contacts...")
        contacts = await client.list_contacts()
        await client.close()

        if not contacts:
            return Response(message="No contacts found.", break_loop=False)

        lines = [f"Signal contacts ({len(contacts)}):"]
        for c in contacts:
            name = sanitize_username(c.get("name", ""))
            number = c.get("number", c.get("address", {}).get("number", "?"))
            blocked = " [BLOCKED]" if c.get("blocked") else ""
            expire = c.get("messageExpirationTime", 0)
            expire_text = ""
            if expire:
                if expire >= 86400:
                    expire_text = f" [disappearing: {expire // 86400}d]"
                elif expire >= 3600:
                    expire_text = f" [disappearing: {expire // 3600}h]"
                elif expire >= 60:
                    expire_text = f" [disappearing: {expire // 60}m]"
                else:
                    expire_text = f" [disappearing: {expire}s]"
            lines.append(f"  - {name or 'No name'} ({number}){blocked}{expire_text}")

        return Response(message="\n".join(lines), break_loop=False)

    async def _update(self, client) -> Response:
        """Update a contact's name."""
        phone_number = self.args.get("phone_number", "")
        name = self.args.get("name", "")

        try:
            phone_number = validate_phone_number(phone_number)
        except ValueError as e:
            return Response(message=f"Error: {e}", break_loop=False)

        if not name:
            return Response(
                message="Error: name is required for update.",
                break_loop=False,
            )

        self.set_progress(f"Updating contact {phone_number}...")
        await client.update_contact(phone_number, name=name)
        await client.close()

        return Response(
            message=f"Contact {phone_number} updated (name: {name}).",
            break_loop=False,
        )

    async def _identity(self, client) -> Response:
        """Get identity/safety number info for a contact.

        Safety numbers are how Signal verifies that you're communicating with
        the right person. Comparing safety numbers out-of-band (in person or
        via another secure channel) provides strong authentication.
        """
        phone_number = self.args.get("phone_number", "")

        try:
            phone_number = validate_phone_number(phone_number)
        except ValueError as e:
            return Response(message=f"Error: {e}", break_loop=False)

        self.set_progress(f"Getting identity for {phone_number}...")
        identities = await client.get_identities_for(phone_number)
        await client.close()

        if not identities:
            return Response(
                message=f"No identity information available for {phone_number}.",
                break_loop=False,
            )

        lines = [f"Identity for {phone_number}:"]
        for ident in identities:
            trust = ident.get("trust_level", ident.get("trustLevel", "unknown"))
            safety = ident.get("safety_number", ident.get("safetyNumber", ""))
            added = ident.get("added_timestamp", ident.get("addedTimestamp", ""))
            lines.append(f"  Trust level: {trust}")
            if safety:
                lines.append(f"  Safety number: {safety}")
            if added:
                lines.append(f"  Added: {added}")

        return Response(message="\n".join(lines), break_loop=False)

    async def _trust(self, client) -> Response:
        """Trust a contact's identity after verifying their safety number.

        SECURITY: Trusting an identity means you confirm that the safety number
        matches what the contact shows on their device. Only do this after
        verifying the safety number through a secure out-of-band channel
        (in person, verified phone call, etc.).
        """
        phone_number = self.args.get("phone_number", "")
        safety_number = self.args.get("safety_number", "")
        trust_all = self.args.get("trust_all", "false").lower() == "true"

        try:
            phone_number = validate_phone_number(phone_number)
        except ValueError as e:
            return Response(message=f"Error: {e}", break_loop=False)

        if not safety_number and not trust_all:
            return Response(
                message="Error: provide safety_number for verified trust, "
                "or set trust_all=true to trust all known keys (less secure).",
                break_loop=False,
            )

        self.set_progress(f"Trusting identity for {phone_number}...")
        await client.trust_identity(
            recipient=phone_number,
            safety_number=safety_number,
            trust_all=trust_all,
        )
        await client.close()

        if trust_all:
            return Response(
                message=f"Trusted all known keys for {phone_number}. "
                "Note: this is less secure than verifying a specific safety number.",
                break_loop=False,
            )
        return Response(
            message=f"Identity for {phone_number} trusted with verified safety number.",
            break_loop=False,
        )

    async def _set_disappearing(self, client) -> Response:
        """Set disappearing message timer for a contact.

        This enables Signal's disappearing messages feature, where messages
        automatically delete after the specified time.
        """
        phone_number = self.args.get("phone_number", "")
        seconds_str = self.args.get("seconds", "0")

        try:
            phone_number = validate_phone_number(phone_number)
        except ValueError as e:
            return Response(message=f"Error: {e}", break_loop=False)

        try:
            seconds = int(seconds_str)
        except ValueError:
            return Response(
                message="Error: seconds must be a number.",
                break_loop=False,
            )

        self.set_progress(f"Setting disappearing timer for {phone_number}...")
        await client.update_contact(
            phone_number, expiration_seconds=seconds
        )
        await client.close()

        if seconds == 0:
            return Response(
                message=f"Disappearing messages disabled for {phone_number}.",
                break_loop=False,
            )

        if seconds >= 86400:
            duration = f"{seconds // 86400} day(s)"
        elif seconds >= 3600:
            duration = f"{seconds // 3600} hour(s)"
        elif seconds >= 60:
            duration = f"{seconds // 60} minute(s)"
        else:
            duration = f"{seconds} second(s)"

        return Response(
            message=f"Disappearing messages set to {duration} for {phone_number}.",
            break_loop=False,
        )
