from helpers.tool import Tool, Response
from plugins.signal.helpers.signal_client import (
    SignalAPIError, get_signal_config, create_signal_client,
)
from plugins.signal.helpers.sanitize import (
    require_auth, validate_group_id, validate_phone_number,
    sanitize_group_name, is_contact_allowed,
)


class SignalGroups(Tool):
    """Manage Signal groups — list, create, update, add/remove members."""

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
            elif action == "info":
                return await self._info(client)
            elif action == "create":
                return await self._create(client, config)
            elif action == "update":
                return await self._update(client)
            elif action == "add_members":
                return await self._add_members(client)
            elif action == "remove_members":
                return await self._remove_members(client)
            elif action == "leave":
                return await self._leave(client)
            else:
                return Response(
                    message=f"Unknown action '{action}'. "
                    "Use: list, info, create, update, add_members, remove_members, leave.",
                    break_loop=False,
                )

        except SignalAPIError as e:
            return Response(message=f"Signal API error: {e}", break_loop=False)
        except Exception as e:
            return Response(
                message=f"Error with Signal groups: {type(e).__name__}: {e}",
                break_loop=False,
            )

    async def _list(self, client) -> Response:
        """List all Signal groups."""
        self.set_progress("Listing Signal groups...")
        groups = await client.list_groups()
        await client.close()

        if not groups:
            return Response(message="No Signal groups found.", break_loop=False)

        lines = [f"Signal groups ({len(groups)}):"]
        for g in groups:
            name = sanitize_group_name(g.get("name", "Unknown"))
            gid = g.get("id", g.get("internal_id", "?"))
            members = len(g.get("members", []))
            blocked = " [BLOCKED]" if g.get("blocked") else ""
            lines.append(f"  - {name} (ID: {gid}, members: {members}){blocked}")

        return Response(message="\n".join(lines), break_loop=False)

    async def _info(self, client) -> Response:
        """Get details about a specific group."""
        group_id = self.args.get("group_id", "")
        try:
            group_id = validate_group_id(group_id)
        except ValueError as e:
            return Response(message=f"Error: {e}", break_loop=False)

        self.set_progress("Getting group info...")
        group = await client.get_group(group_id)
        await client.close()

        name = sanitize_group_name(group.get("name", "Unknown"))
        members = group.get("members", [])
        admins = group.get("admins", [])
        desc = group.get("description", "")

        lines = [
            f"Group: {name}",
            f"  ID: {group_id}",
            f"  Members ({len(members)}):",
        ]
        for m in members[:50]:  # Cap display at 50
            lines.append(f"    - {m}")
        if len(members) > 50:
            lines.append(f"    ... and {len(members) - 50} more")
        if admins:
            lines.append(f"  Admins: {', '.join(admins)}")
        if desc:
            lines.append(f"  Description: {desc}")

        return Response(message="\n".join(lines), break_loop=False)

    async def _create(self, client, config: dict) -> Response:
        """Create a new group."""
        name = self.args.get("name", "")
        members_str = self.args.get("members", "")
        description = self.args.get("description", "")

        if not name:
            return Response(message="Error: name is required.", break_loop=False)
        if not members_str:
            return Response(message="Error: members is required (comma-separated phone numbers).", break_loop=False)

        members = [m.strip() for m in members_str.split(",") if m.strip()]

        # Validate all member phone numbers
        validated = []
        for m in members:
            try:
                validated.append(validate_phone_number(m))
            except ValueError as e:
                return Response(message=f"Error with member {m}: {e}", break_loop=False)

        self.set_progress(f"Creating group '{name}'...")
        result = await client.create_group(
            name=name, members=validated, description=description
        )
        await client.close()

        gid = result.get("id", "unknown") if isinstance(result, dict) else "unknown"
        return Response(
            message=f"Group '{name}' created with {len(validated)} member(s) (ID: {gid}).",
            break_loop=False,
        )

    async def _update(self, client) -> Response:
        """Update group name or description."""
        group_id = self.args.get("group_id", "")
        name = self.args.get("name", "")
        description = self.args.get("description", "")

        try:
            group_id = validate_group_id(group_id)
        except ValueError as e:
            return Response(message=f"Error: {e}", break_loop=False)

        if not name and not description:
            return Response(
                message="Error: provide 'name' and/or 'description' to update.",
                break_loop=False,
            )

        self.set_progress("Updating group...")
        await client.update_group(
            group_id=group_id,
            name=name or None,
            description=description or None,
        )
        await client.close()

        updates = []
        if name:
            updates.append(f"name='{name}'")
        if description:
            updates.append("description updated")
        return Response(
            message=f"Group {group_id} updated ({', '.join(updates)}).",
            break_loop=False,
        )

    async def _add_members(self, client) -> Response:
        """Add members to a group."""
        group_id = self.args.get("group_id", "")
        members_str = self.args.get("members", "")

        try:
            group_id = validate_group_id(group_id)
        except ValueError as e:
            return Response(message=f"Error: {e}", break_loop=False)

        if not members_str:
            return Response(message="Error: members is required.", break_loop=False)

        members = [m.strip() for m in members_str.split(",") if m.strip()]
        validated = []
        for m in members:
            try:
                validated.append(validate_phone_number(m))
            except ValueError as e:
                return Response(message=f"Error with member {m}: {e}", break_loop=False)

        self.set_progress("Adding members to group...")
        await client.add_group_members(group_id, validated)
        await client.close()

        return Response(
            message=f"Added {len(validated)} member(s) to group {group_id}.",
            break_loop=False,
        )

    async def _remove_members(self, client) -> Response:
        """Remove members from a group."""
        group_id = self.args.get("group_id", "")
        members_str = self.args.get("members", "")

        try:
            group_id = validate_group_id(group_id)
        except ValueError as e:
            return Response(message=f"Error: {e}", break_loop=False)

        if not members_str:
            return Response(message="Error: members is required.", break_loop=False)

        members = [m.strip() for m in members_str.split(",") if m.strip()]
        validated = []
        for m in members:
            try:
                validated.append(validate_phone_number(m))
            except ValueError as e:
                return Response(message=f"Error with member {m}: {e}", break_loop=False)

        self.set_progress("Removing members from group...")
        await client.remove_group_members(group_id, validated)
        await client.close()

        return Response(
            message=f"Removed {len(validated)} member(s) from group {group_id}.",
            break_loop=False,
        )

    async def _leave(self, client) -> Response:
        """Leave a group."""
        group_id = self.args.get("group_id", "")

        try:
            group_id = validate_group_id(group_id)
        except ValueError as e:
            return Response(message=f"Error: {e}", break_loop=False)

        self.set_progress("Leaving group...")
        await client.leave_group(group_id)
        await client.close()

        return Response(message=f"Left group {group_id}.", break_loop=False)
