# Signal Plugin for Agent Zero

A security-first Signal integration plugin for Agent Zero that enables end-to-end encrypted messaging, group management, contact verification, and a real-time chat bridge — all powered by the Signal Protocol.

## Features

- **End-to-end encrypted messaging** — All messages use the Signal Protocol (X3DH + Double Ratchet) via the official `libsignal-client` library
- **Send** messages, reactions, and typing indicators to contacts and groups
- **Receive** messages with polling and configurable watch contacts
- **Chat bridge** — Persistent polling service routes Signal messages through Agent Zero's LLM
- **Group management** — Create, update, add/remove members, and manage Signal groups
- **Contact management** — List contacts, update names, verify safety numbers
- **Identity verification** — View and trust safety numbers for secure authentication
- **Disappearing messages** — Set auto-delete timers on conversations
- **Security-first design** — Input sanitization, rate limiting, contact allowlists, auth-gated elevated mode

## Installation Modes

The plugin supports two installation modes:

| Mode | Description | Best for |
|------|-------------|----------|
| **Integrated** (default) | signal-cli runs natively inside the Agent Zero container as a supervisord service. No extra Docker containers needed. | Simplest setup, single-container deployments |
| **External** | Uses a separate `signal-cli-rest-api` Docker container (bbernhard). | Multi-container setups, separation of concerns |

Both modes provide identical functionality — the plugin auto-detects the mode and uses the appropriate client (JSON-RPC for integrated, REST for external).

## Quick Start

### 1. Install the Plugin

**Using the install script (recommended):**

```bash
# Copy the plugin source into the container
docker cp a0-signal/. <container_name>:/tmp/a0-signal

# Integrated mode (default — signal-cli runs inside A0 container):
docker exec <container_name> bash /tmp/a0-signal/install.sh --integrated

# External mode (separate signal-cli-rest-api container):
docker exec <container_name> bash /tmp/a0-signal/install.sh
```

**Manual install:**

```bash
# Copy plugin into the container
docker cp a0-signal/. <container_name>:/a0/usr/plugins/signal/

# Create symlink for Python imports
docker exec <container_name> ln -sf /a0/usr/plugins/signal /a0/plugins/signal

# Install dependencies (add --integrated for native signal-cli)
docker exec <container_name> python /a0/usr/plugins/signal/initialize.py --integrated

# Enable the plugin
docker exec <container_name> touch /a0/usr/plugins/signal/.toggle-1

# Restart to load
docker exec <container_name> supervisorctl restart run_ui
```

### 2. Set Up Signal Backend

**Integrated mode** — Start the built-in signal-cli daemon:

```bash
docker exec <container_name> supervisorctl start signal_cli
```

**External mode** — Add a signal-cli-rest-api container to your `docker-compose.yml`:

```yaml
services:
  signal-api:
    image: bbernhard/signal-cli-rest-api:latest
    container_name: signal-api
    environment:
      - MODE=json-rpc
      - AUTO_RECEIVE_SCHEDULE=0 */5 * * * *
    volumes:
      - signal-cli-config:/home/.local/share/signal-cli
    networks:
      - internal

volumes:
  signal-cli-config:
```

Register or link a phone number — see [docs/SETUP_SIGNAL_API.md](docs/SETUP_SIGNAL_API.md) for the full guide.

### 3. Configure

**Option A -- Config file (most reliable):**

```bash
# Integrated mode:
docker exec <container_name> bash -c 'cat > /a0/usr/plugins/signal/config.json << EOF
{
  "api": { "mode": "integrated" },
  "phone_number": "+1234567890"
}
EOF'

# External mode:
docker exec <container_name> bash -c 'cat > /a0/usr/plugins/signal/config.json << EOF
{
  "api": { "mode": "external", "base_url": "http://signal-api:8080" },
  "phone_number": "+1234567890"
}
EOF'
```

**Option B -- Environment variables:**

```bash
SIGNAL_MODE=integrated          # or "external"
SIGNAL_PHONE_NUMBER=+1234567890
SIGNAL_API_URL=http://signal-api:8080  # external mode only
```

**Option C -- WebUI:**

Open Agent Zero's web interface, navigate to the Signal plugin settings page, select your installation mode, and enter your phone number.

### 4. Restart Agent Zero

```bash
docker exec <container_name> supervisorctl restart run_ui
```

### 5. Start Using It

Open Agent Zero's chat and try:

| What you want | What to say |
|---------------|-------------|
| Check connection | "Test the Signal connection" |
| Read messages | "Check for new Signal messages" |
| Send a message | "Send a Signal message to +1234567890 saying 'Hello from Agent Zero!'" |
| List groups | "List my Signal groups" |
| List contacts | "List my Signal contacts" |
| Verify identity | "Show the safety number for +1234567890" |
| Start chat bridge | "Add +1234567890 to the Signal chat bridge, then start it" |

## Documentation

| Document | Description |
|----------|-------------|
| [docs/README.md](docs/README.md) | Full reference — all tools, configuration, architecture |
| [docs/QUICKSTART.md](docs/QUICKSTART.md) | Step-by-step setup guide |
| [docs/SETUP_SIGNAL_API.md](docs/SETUP_SIGNAL_API.md) | signal-cli-rest-api setup and registration |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | How to extend and contribute |

## Tools

| Tool | Description |
|------|-------------|
| `signal_send` | Send messages, reactions, and typing indicators |
| `signal_read` | Receive messages, list groups/contacts, get profiles |
| `signal_chat` | Manage the chat bridge (start/stop/add contacts) |
| `signal_groups` | Create, update, and manage Signal groups |
| `signal_contacts` | Manage contacts, verify identities, set disappearing messages |

## Requirements

- Agent Zero (development branch with plugin framework)
- Python 3.10+
- A phone number for Signal registration
- Python packages: `httpx`, `pyyaml` (auto-installed by `initialize.py`)
- **Integrated mode**: No additional requirements (signal-cli native binary is downloaded automatically)
- **External mode**: Docker (for signal-cli-rest-api container)

## Architecture

```
a0-signal/
├── plugin.yaml              # Plugin manifest
├── default_config.yaml      # Default settings
├── config.json              # Active config (created on first save)
├── initialize.py            # Dependency installer
├── install.sh               # Automated installer
├── helpers/
│   ├── signal_client.py     # Dual-mode client factory + REST API client
│   ├── signal_jsonrpc.py    # JSON-RPC client (integrated mode)
│   ├── signal_daemon.py     # signal-cli daemon lifecycle management
│   ├── signal_bridge.py     # Chat bridge (polling-based)
│   ├── sanitize.py          # Security: input validation, injection defense
│   └── poll_state.py        # Polling state tracker
├── tools/                   # 5 tools (auto-discovered by framework)
├── prompts/                 # LLM tool descriptions
├── extensions/              # Agent lifecycle hooks
├── api/                     # WebUI API endpoints
├── webui/                   # Dashboard + settings UI
├── skills/                  # 3 skill definitions
├── data/                    # Runtime state (auto-created)
├── tests/                   # Regression test suite (64 tests)
└── docs/                    # Documentation
```

```
Integrated mode:
[Agent Zero] --JSON-RPC--> [signal-cli daemon :8080] --Signal Protocol--> [Signal Servers]
                            (supervisord, same container)

External mode:
[Agent Zero] --REST API--> [signal-cli-rest-api :8080] --Signal Protocol--> [Signal Servers]
                            (separate Docker container)
```

Both modes use `signal-cli` which handles all Signal Protocol encryption using the official `libsignal-client` library — the same cryptographic library used by the Signal mobile and desktop apps. The plugin's `create_signal_client()` factory transparently returns the appropriate client based on your configured mode.

## Security

This plugin has been designed with security as the foremost priority. **Read this section carefully before enabling elevated mode.**

### Core Protections

- **End-to-end encryption** — All messages are encrypted with the Signal Protocol. The REST API container handles all cryptographic operations — Agent Zero never touches encryption keys directly.
- **Chat bridge privilege isolation** — The chat bridge uses direct LLM calls (`call_utility_model`) instead of the full agent loop. In restricted mode (the default), Signal users have **zero access** to tools, code execution, file operations, or system resources. This is enforced architecturally, not by prompt instructions.
- **Prompt injection defense** — Input sanitization with Unicode homoglyph normalization (NFKC), zero-width character stripping, and pattern-based injection detection (100+ patterns).
- **E.164 phone number validation** — All phone numbers are validated against the E.164 format before use in API calls.
- **Group ID validation** — Group identifiers are validated as base64-encoded strings.
- **Atomic file writes** — State and config files written atomically with restrictive permissions (`0o600`).
- **Per-user rate limiting** — Sliding window rate limiter (10 messages per 60 seconds) on the chat bridge.
- **Contact allowlist enforcement** — Configured contact allowlists are checked consistently across all tools.
- **Auth failure lockout** — 5 failed authentication attempts within 5 minutes triggers a lockout period.
- **Sanitized error messages** — Internal details (file paths, stack traces) are never exposed to users.
- **CSRF protection** — All API handlers require CSRF tokens.

### Contact Allowlist

The **Contact Allowlist** restricts which phone numbers the agent can interact with. When configured, only listed numbers can send/receive messages — all other numbers are silently ignored.

- **Empty allowlist** (default): All contacts allowed.
- **Populated allowlist**: Only listed phone numbers can interact. Changes take effect immediately.

Configure via WebUI (Settings > Allowed Contacts) or in `config.json`:
```json
{
  "allowed_contacts": ["+1234567890", "+0987654321"]
}
```

### Elevated Mode -- IMPORTANT

Elevated mode allows authenticated Signal users to access the **full Agent Zero agent loop** — including tools, code execution, file access, and all system capabilities. This is powerful but carries significant security implications.

**How elevated mode works:**
1. An admin enables `allow_elevated: true` in config and obtains the auth key from the WebUI
2. A Signal user sends `!auth <key>` to the registered number
3. The user's session is elevated for the configured timeout (default: 5 minutes)
4. The user sends `!deauth` to end the session early
5. Session state and conversation history are cleared on deauth

**Recommended configuration for elevated mode:**

> Use a dedicated phone number for the Signal API and restrict the chat bridge to a single trusted phone number via the allowed contacts list. This provides the strongest security posture.

**What elevated mode grants access to:**
- Agent Zero's full tool suite (code execution, file operations, web access, etc.)
- The host system's filesystem and network (within Agent Zero's container)
- All other installed Agent Zero plugins and capabilities

**Only enable elevated mode if you fully understand these implications and trust every user on the allowlist.**

## Troubleshooting

See the [docs/README.md](docs/README.md) for the full reference.

**Common issues:**

- **"Signal API URL not configured"** — Set `SIGNAL_API_URL` or configure in WebUI settings
- **"Signal phone number not configured"** — Set `SIGNAL_PHONE_NUMBER` or configure in WebUI settings
- **"Cannot connect to Signal API"** — Ensure signal-cli-rest-api container is running and accessible
- **Plugin not loading** — Check symlink: `ls -la /a0/plugins/signal` should point to `/a0/usr/plugins/signal`
- **Import errors** — The symlink at `/a0/plugins/signal` -> `/a0/usr/plugins/signal` is required for `from plugins.signal.helpers...` imports
- **"Registration failed"** — See [docs/SETUP_SIGNAL_API.md](docs/SETUP_SIGNAL_API.md) for registration troubleshooting

## License

MIT License — see [LICENSE](LICENSE).
