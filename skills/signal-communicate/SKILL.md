---
name: "signal-communicate"
description: "Send messages, reactions, and manage groups via Signal with end-to-end encryption."
version: "1.0.0"
author: "AgentZero Signal Plugin"
license: "MIT"
tags: ["signal", "messaging", "groups", "encrypted"]
triggers:
  - "send signal message"
  - "signal group"
  - "message on signal"
  - "signal contacts"
allowed_tools:
  - signal_send
  - signal_read
  - signal_groups
  - signal_contacts
metadata:
  complexity: "basic"
  category: "communication"
---

# Signal Communication

Send and receive messages, manage groups, and interact with contacts via Signal — all with end-to-end encryption.

## Sending Messages

Use `signal_send` to send messages to phone numbers or groups:

```json
{"action": "send", "recipient": "+1234567890", "content": "Hello!"}
```

## Reading Messages

Use `signal_read` to receive pending messages:

```json
{"action": "receive"}
```

Messages are consumed on read — each call returns new messages since the last call.

## Group Management

Use `signal_groups` to manage groups:
- List groups: `{"action": "list"}`
- Create group: `{"action": "create", "name": "Team", "members": "+123,+456"}`
- Add members: `{"action": "add_members", "group_id": "...", "members": "+789"}`

## Contact Management

Use `signal_contacts` to manage contacts:
- List contacts: `{"action": "list"}`
- Update name: `{"action": "update", "phone_number": "+123", "name": "Alice"}`
- Verify identity: `{"action": "identity", "phone_number": "+123"}`
- Set disappearing: `{"action": "disappearing", "phone_number": "+123", "seconds": "86400"}`

## Tips

- All phone numbers must be in E.164 format (+1234567890)
- Group IDs are base64-encoded strings
- Reactions require the original message's author and timestamp
