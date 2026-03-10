from helpers.tool import Tool, Response
from plugins.signal.helpers.signal_client import (
    SignalAPIError, get_signal_config, create_signal_client, format_messages,
)
from plugins.signal.helpers.sanitize import require_auth, is_contact_allowed


class SignalRead(Tool):
    """Receive and read pending Signal messages, or list groups/contacts."""

    async def execute(self, **kwargs) -> Response:
        action = self.args.get("action", "receive")

        config = get_signal_config(self.agent)
        try:
            require_auth(config)
        except ValueError as e:
            return Response(message=f"Auth error: {e}", break_loop=False)

        try:
            client = create_signal_client(agent=self.agent)

            if action == "receive":
                return await self._receive(client, config)
            elif action == "groups":
                return await self._list_groups(client)
            elif action == "contacts":
                return await self._list_contacts(client)
            elif action == "profile":
                return await self._get_profile(client)
            else:
                return Response(
                    message=f"Unknown action '{action}'. "
                    "Use 'receive', 'groups', 'contacts', or 'profile'.",
                    break_loop=False,
                )

        except SignalAPIError as e:
            return Response(message=f"Signal API error: {e}", break_loop=False)
        except Exception as e:
            return Response(
                message=f"Error reading Signal: {type(e).__name__}: {e}",
                break_loop=False,
            )

    async def _receive(self, client, config: dict) -> Response:
        """Receive pending messages from Signal."""
        self.set_progress("Receiving Signal messages...")
        envelopes = await client.receive_messages(timeout_seconds=1)
        await client.close()

        if not envelopes:
            return Response(message="No new messages.", break_loop=False)

        # Filter by allowed contacts
        allowed = config.get("allowed_contacts", [])
        if allowed:
            filtered = []
            for env in envelopes:
                envelope = env.get("envelope", env)
                source = envelope.get("sourceNumber") or envelope.get("source", "")
                if source in allowed:
                    filtered.append(env)
            envelopes = filtered

        # Filter to only data messages (ignore receipts, typing, etc.)
        data_envelopes = []
        for env in envelopes:
            envelope = env.get("envelope", env)
            if envelope.get("dataMessage"):
                data_envelopes.append(env)

        if not data_envelopes:
            return Response(
                message=f"Received {len(envelopes)} envelope(s) but none contained messages.",
                break_loop=False,
            )

        result = format_messages(data_envelopes)
        return Response(
            message=f"Received {len(data_envelopes)} message(s):\n\n{result}",
            break_loop=False,
        )

    async def _list_groups(self, client) -> Response:
        """List all Signal groups."""
        self.set_progress("Listing Signal groups...")
        groups = await client.list_groups()
        await client.close()

        if not groups:
            return Response(message="No groups found.", break_loop=False)

        from plugins.signal.helpers.sanitize import sanitize_group_name

        lines = [f"Signal groups ({len(groups)}):"]
        for g in groups:
            name = sanitize_group_name(g.get("name", "Unknown"))
            gid = g.get("id", g.get("internal_id", "?"))
            members = len(g.get("members", []))
            lines.append(f"  - {name} (ID: {gid}, members: {members})")

        return Response(message="\n".join(lines), break_loop=False)

    async def _list_contacts(self, client) -> Response:
        """List known Signal contacts."""
        self.set_progress("Listing Signal contacts...")
        contacts = await client.list_contacts()
        await client.close()

        if not contacts:
            return Response(message="No contacts found.", break_loop=False)

        from plugins.signal.helpers.sanitize import sanitize_username

        lines = [f"Signal contacts ({len(contacts)}):"]
        for c in contacts:
            name = sanitize_username(c.get("name", ""))
            number = c.get("number", c.get("address", {}).get("number", "?"))
            blocked = " [BLOCKED]" if c.get("blocked") else ""
            lines.append(f"  - {name or 'No name'} ({number}){blocked}")

        return Response(message="\n".join(lines), break_loop=False)

    async def _get_profile(self, client) -> Response:
        """Get profile info for a contact or self."""
        number = self.args.get("phone_number", "")
        self.set_progress("Getting profile...")

        try:
            profile = await client.get_profile(number)
            await client.close()

            if not profile:
                return Response(message="No profile data available.", break_loop=False)

            from plugins.signal.helpers.sanitize import sanitize_username
            name = sanitize_username(profile.get("name", ""))
            about = profile.get("about", "")

            lines = [f"Profile for {number or 'self'}:"]
            if name:
                lines.append(f"  Name: {name}")
            if about:
                lines.append(f"  About: {about}")

            return Response(message="\n".join(lines), break_loop=False)
        except SignalAPIError:
            await client.close()
            return Response(
                message=f"Could not get profile for {number or 'self'}.",
                break_loop=False,
            )
