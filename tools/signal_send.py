from helpers.tool import Tool, Response
from plugins.signal.helpers.signal_client import (
    SignalAPIError, get_signal_config, create_signal_client,
)
from plugins.signal.helpers.sanitize import (
    require_auth, validate_recipient, is_contact_allowed,
)


class SignalSend(Tool):
    """Send a message or reaction via Signal."""

    async def execute(self, **kwargs) -> Response:
        recipient = self.args.get("recipient", "")
        content = self.args.get("content", "")
        action = self.args.get("action", "send")

        config = get_signal_config(self.agent)
        try:
            require_auth(config)
        except ValueError as e:
            return Response(message=f"Auth error: {e}", break_loop=False)

        try:
            recipient = validate_recipient(recipient, "recipient")
        except ValueError as e:
            return Response(message=f"Error: {e}", break_loop=False)

        if not is_contact_allowed(recipient, config):
            return Response(
                message=f"Error: {recipient} is not in the allowed contacts list.",
                break_loop=False,
            )

        try:
            client = create_signal_client(agent=self.agent)

            if action == "send":
                if not content:
                    return Response(
                        message="Error: content is required for sending.",
                        break_loop=False,
                    )

                self.set_progress(f"Sending message to {recipient}...")
                result = await client.send_message(
                    recipients=[recipient],
                    message=content,
                )
                await client.close()

                timestamp = ""
                if isinstance(result, dict):
                    timestamp = result.get("timestamp", "")

                return Response(
                    message=f"Message sent to {recipient}"
                    + (f" (timestamp: {timestamp})" if timestamp else "")
                    + ".",
                    break_loop=False,
                )

            elif action == "react":
                emoji = self.args.get("emoji", "")
                target_author = self.args.get("target_author", "")
                target_timestamp = self.args.get("target_timestamp", "")

                if not emoji or not target_author or not target_timestamp:
                    return Response(
                        message="Error: emoji, target_author, and target_timestamp "
                        "are required for reactions.",
                        break_loop=False,
                    )

                try:
                    ts = int(target_timestamp)
                except ValueError:
                    return Response(
                        message="Error: target_timestamp must be a number.",
                        break_loop=False,
                    )

                self.set_progress(f"Sending reaction to {recipient}...")
                await client.send_reaction(
                    recipient=recipient,
                    emoji=emoji,
                    target_author=target_author,
                    target_timestamp=ts,
                )
                await client.close()

                return Response(
                    message=f"Reaction {emoji} sent to message from {target_author}.",
                    break_loop=False,
                )

            elif action == "typing":
                await client.send_typing(recipient)
                await client.close()
                return Response(
                    message=f"Typing indicator sent to {recipient}.",
                    break_loop=False,
                )

            else:
                return Response(
                    message=f"Unknown action '{action}'. Use 'send', 'react', or 'typing'.",
                    break_loop=False,
                )

        except SignalAPIError as e:
            return Response(message=f"Signal API error: {e}", break_loop=False)
        except Exception as e:
            return Response(
                message=f"Error sending via Signal: {type(e).__name__}: {e}",
                break_loop=False,
            )
