---
name: "signal-chat"
description: "Set up and manage a persistent Signal chat bridge to Agent Zero. Send messages to the registered Signal number and get AI responses with end-to-end encryption."
version: "1.0.0"
author: "AgentZero Signal Plugin"
license: "MIT"
tags: ["signal", "chat", "bridge", "encrypted"]
triggers:
  - "signal chat bridge"
  - "set up signal chat"
  - "start signal bridge"
  - "signal messaging"
allowed_tools:
  - signal_chat
  - signal_read
  - signal_send
metadata:
  complexity: "intermediate"
  category: "communication"
---

# Signal Chat Bridge

Set up a persistent chat bridge between Signal and Agent Zero. Users send Signal messages to the registered number and receive AI-powered responses — all end-to-end encrypted via the Signal Protocol.

## Setup Workflow

1. **Verify connection**: Use `signal_read` with `action: receive` to test the Signal API is working
2. **Register a contact**: Use `signal_chat` with `action: add_contact` to register a phone number
3. **Start the bridge**: Use `signal_chat` with `action: start` to begin listening for messages
4. **Test**: Send a message from the registered phone number to the Signal number

## Security Modes

- **Restricted mode** (default): The bridge responds conversationally only — no tools, no code execution, no file access
- **Elevated mode** (opt-in): After authenticating with `!auth <key>`, users get full Agent Zero capabilities

## Signal-Side Commands

Users send these as regular Signal messages:
- `!auth <key>` — Authenticate for elevated access
- `!deauth` — End elevated session
- `!status` — Check current mode

## Tips

- Use a dedicated phone number for the Signal API — not your personal one
- Keep the signal-cli-rest-api container on a private Docker network
- The chat bridge maintains separate conversation contexts per contact
- All messages are end-to-end encrypted — the bridge server never sees plaintext
