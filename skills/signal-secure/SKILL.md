---
name: "signal-secure"
description: "Security-focused Signal operations — verify identities, manage safety numbers, set disappearing messages, and enforce access controls."
version: "1.0.0"
author: "AgentZero Signal Plugin"
license: "MIT"
tags: ["signal", "security", "encryption", "identity", "verification"]
triggers:
  - "signal security"
  - "verify signal identity"
  - "signal safety number"
  - "disappearing messages"
  - "signal trust"
allowed_tools:
  - signal_contacts
  - signal_groups
  - signal_read
metadata:
  complexity: "advanced"
  category: "security"
---

# Signal Security Operations

Leverage Signal's security features — identity verification, safety numbers, disappearing messages, and access controls.

## Identity Verification

Signal's safety numbers are the foundation of identity verification. Each contact pair has a unique safety number that changes if either person's keys change.

### View Safety Number
```json
{"action": "identity", "phone_number": "+1234567890"}
```

### Trust After Verification
After verifying the safety number out-of-band (in person, verified call):
```json
{"action": "trust", "phone_number": "+1234567890", "safety_number": "12345 67890 ..."}
```

**IMPORTANT**: Only trust identities after verifying safety numbers through a secure channel. Never auto-trust without explicit human verification.

## Disappearing Messages

Set messages to auto-delete after a time period:

```json
{"action": "disappearing", "phone_number": "+1234567890", "seconds": "3600"}
```

Common values:
- 300 = 5 minutes
- 3600 = 1 hour
- 86400 = 1 day
- 604800 = 1 week

## Security Best Practices

1. **Verify identities**: Always verify safety numbers with contacts before sharing sensitive information
2. **Use disappearing messages**: Enable for conversations with sensitive content
3. **Restrict contacts**: Use the `allowed_contacts` config to limit who can interact with the agent
4. **Dedicated number**: Use a dedicated phone number for the Signal API, not a personal one
5. **Network isolation**: Keep signal-cli-rest-api on a private Docker network
6. **Elevated mode**: Only enable if needed, use short session timeouts, and share auth keys securely
7. **Monitor identities**: Watch for safety number changes — they may indicate key compromise

## Signal Protocol Security

The Signal Protocol provides:
- **X3DH key exchange**: Establishes shared secrets without prior contact
- **Double Ratchet**: Provides forward secrecy and post-compromise security
- **AES-256-CBC**: Symmetric encryption for message content
- **HMAC-SHA256**: Message authentication
- **libsignal-client**: Same cryptographic library used by the official Signal app
