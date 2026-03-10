# Signal Integration Plugin for Agent Zero

Secure messaging via Signal with end-to-end encryption. Send and receive messages, manage groups, verify identities, and use Signal as a real-time chat interface to Agent Zero.

## Features

- **End-to-end encrypted messaging** — All messages use the Signal Protocol (X3DH + Double Ratchet) via the official `libsignal-client` library
- **Bidirectional communication** — Send messages from Agent Zero and receive responses via Signal
- **Persistent chat bridge** — Always-on supervisor service routes Signal messages through Agent Zero's LLM
- **Dual security modes** — Restricted mode (chat only) and elevated mode (full agent loop with auth)
- **Group management** — Create, update, and manage Signal groups
- **Contact management** — List contacts, update names, verify safety numbers
- **Identity verification** — View and trust safety numbers for secure authentication
- **Disappearing messages** — Set auto-delete timers on conversations
- **Security-first design** — Input sanitization, rate limiting, contact allowlists, HMAC-based auth

## Architecture

### Integrated Mode (Default)

```
[Agent Zero] ─── Python imports ───> [Signal Plugin]
                                          │
                                    JSON-RPC (localhost:8080)
                                          │
                                    [signal-cli daemon]  ← supervisord service
                                          │
                                    Signal Protocol (E2E encrypted)
                                          │
                                    [Signal Servers]
```

### Chat Bridge Architecture

```
[Signal App] ──── Signal Protocol ────> [Signal Servers]
                                              │
[signal-cli daemon] <── WebSocket ────────────┘
       │
  JSON-RPC (localhost:8080)
       │
[run_signal_bridge.py] ← supervisord service (polls for messages)
       │
  ┌────┴────────────────────┐
  │ Restricted Mode         │ Elevated Mode (after !auth)
  │ call_utility_model()    │ context.communicate()
  │ NO tools, NO code       │ Full agent loop + tools
  │ Conversation only       │ Code exec, file I/O, web
  └─────────────────────────┘
       │
[Signal App] <── response sent back via signal-cli
```

### External Mode (Alternative)

```
[Agent Zero] --httpx--> [signal-cli-rest-api] --Signal Protocol--> [Signal Servers]
                         (Docker container)
```

## Security Model

### Encryption
- Signal Protocol provides end-to-end encryption for all messages
- signal-cli handles all cryptographic operations locally
- Agent Zero never touches encryption keys directly

### Access Control
- Contact allowlist limits who the agent can interact with
- Chat bridge can be restricted to specific phone numbers
- Elevated mode requires authentication with a cryptographically secure key
- Rate limiting prevents abuse (10 messages per 60 seconds per user)
- Auth failures are rate-limited (5 failures in 5 minutes = lockout)

### Input Sanitization
- NFKC Unicode normalization defeats homoglyph attacks
- Zero-width character stripping prevents keyword splitting
- 100+ injection pattern blocking neutralizes prompt injection attempts
- Delimiter tag escaping prevents spoofing

### File Security
- All state files use restrictive permissions (0o600)
- Atomic writes prevent partial file corruption
- Sensitive config values are masked in API responses

## Tools

| Tool | Description |
|------|-------------|
| `signal_send` | Send messages, reactions, and typing indicators |
| `signal_read` | Receive messages, list groups/contacts, get profiles |
| `signal_chat` | Manage the chat bridge (start/stop/contacts) |
| `signal_groups` | Create, update, and manage Signal groups |
| `signal_contacts` | Manage contacts, verify identities, set disappearing messages |

## Quick Start

See [QUICKSTART.md](QUICKSTART.md) for the complete step-by-step deployment guide.

## Requirements

- Agent Zero instance (Docker container)
- A phone number for Signal registration/linking
- Python packages: `httpx`, `pyyaml` (auto-installed)
- For integrated mode: ~150MB disk space (signal-cli binary + data)
- For external mode: Docker (for signal-cli-rest-api container)

---

## Troubleshooting

### Installation Issues

#### "signal-cli not found" or binary not executable
```bash
# Verify binary exists and is executable
ls -la /opt/signal-cli-native/bin/signal-cli

# If missing, re-run installation
python3 /a0/usr/plugins/signal/initialize.py --integrated
```

#### "Import errors" or "ModuleNotFoundError: httpx"
```bash
# Reinstall Python dependencies into the A0 venv
/opt/venv-a0/bin/python -m pip install httpx pyyaml

# Or use uv (if available)
uv pip install httpx pyyaml --python /opt/venv-a0/bin/python
```

#### Plugin not showing in Agent Zero WebUI
```bash
# Check plugin symlink
ls -la /a0/plugins/signal
# Should point to /a0/usr/plugins/signal

# Check toggle file exists
ls /a0/usr/plugins/signal/.toggle-1

# Recreate if needed
ln -sf /a0/usr/plugins/signal /a0/plugins/signal
touch /a0/usr/plugins/signal/.toggle-1
supervisorctl restart run_ui
```

---

### Signal Linking Issues

#### "Connection closed!" when running link command
This usually means stale data from a previous link attempt. Clear and retry:
```bash
rm -rf /root/.local/share/signal-cli/data/*
/opt/signal-cli-native/bin/signal-cli --config /opt/signal-cli-data link -n "AgentZero"
```

#### SSL certificate errors / connection timeouts during linking
signal-cli uses its own certificate pinning (Signal's CA, not the system trust store). This fails through some VPN configurations:
```bash
# Temporarily disable VPN, then retry the link command
# Once linked, VPN can be re-enabled — the existing connection works fine
```

#### Link succeeds but daemon can't find the account
After linking, signal-cli stores data in `~/.local/share/signal-cli/`. Copy to the daemon's data directory:
```bash
cp -r /root/.local/share/signal-cli/data/* /opt/signal-cli-data/
supervisorctl restart signal_cli
```

---

### Supervisor / Service Issues

#### signal_cli shows "STOPPED" or won't start
```bash
# Check logs for errors
tail -50 /var/log/signal-cli-error.log

# Common causes:
# 1. No account linked yet — link first, then start
# 2. Data not copied to /opt/signal-cli-data/
# 3. Binary not installed

# Verify and start
/opt/signal-cli-native/bin/signal-cli --version
supervisorctl start signal_cli
```

#### Supervisor not picking up signal_cli / signal_bridge configs
Some A0 container builds only read from the main `supervisord.conf` and ignore separate `.conf` files. Fix by appending to the main config:
```bash
# Check if configs are in the main file
grep "signal_cli\|signal_bridge" /etc/supervisor/conf.d/supervisord.conf

# If not, the install.sh script handles this automatically.
# Or manually append the [program:signal_cli] and [program:signal_bridge]
# sections to /etc/supervisor/conf.d/supervisord.conf

supervisorctl reread
supervisorctl update
```

#### signal_bridge enters BACKOFF / keeps crashing
```bash
# Check what's failing
tail -100 /var/log/signal-bridge-error.log

# Common causes:
# 1. signal_cli daemon not running — start it first
# 2. Missing API key in .env — check /a0/usr/.env
# 3. Import errors — check Python dependencies

# The bridge has startretries=20, so it will keep trying.
# Fix the root cause, then:
supervisorctl restart signal_bridge
```

#### Container event listener kills container due to bridge BACKOFF
If the bridge keeps crashing, supervisor may trigger a container restart. To prevent this while debugging, temporarily increase `startretries` in the supervisor config or stop the bridge:
```bash
supervisorctl stop signal_bridge
# Debug the issue, then restart
```

---

### Chat Bridge Issues

#### "API key required" (401) when receiving messages
The bridge runner couldn't load the LLM API key. This happens when:
1. `/a0/usr/.env` doesn't contain the API key
2. The bridge fell back to the HTTP API fallback (which requires a web API key)

Fix:
```bash
# Verify API key is in .env
grep API_KEY /a0/usr/.env

# If missing, add it (example for Venice)
echo 'API_KEY_VENICE=your-key-here' >> /a0/usr/.env

# Restart the bridge
supervisorctl restart signal_bridge
```

#### "Cannot connect to host localhost:55080" in elevated mode
The bridge is running in development mode instead of dockerized mode. Code execution requires `--dockerized=true`:
```bash
# Check the supervisor command
grep "signal_bridge" /etc/supervisor/conf.d/supervisord.conf
# Should include: --dockerized=true

# The run_signal_bridge.py script sets this automatically via:
# sys.argv = ["run_signal_bridge.py", "--dockerized=true"]
# If you're running manually, add the flag:
/opt/venv-a0/bin/python /a0/run_signal_bridge.py --dockerized=true
```

#### "No RFC password, cannot handle RFC calls" in elevated mode
Elevated mode code execution requires an RFC password:
```bash
# Generate and save
python3 -c "import secrets; print(f'RFC_PASSWORD={secrets.token_urlsafe(32)}')" >> /a0/usr/.env
supervisorctl restart signal_bridge
```

#### Messages sent but no response received
```bash
# 1. Check bridge is running
supervisorctl status signal_bridge

# 2. Check logs for incoming messages
tail -f /var/log/signal-bridge.log
# Send a message from your phone — you should see "From +1..."

# 3. Check allowed_numbers in config
cat /a0/usr/plugins/signal/config.json | python3 -m json.tool | grep -A5 allowed_numbers
# Your number must be in this list (or the list must be empty)

# 4. Check signal-cli daemon is receiving messages
tail -f /var/log/signal-cli.log
```

#### Bridge shows "connected" but doesn't receive messages
The signal-cli daemon may have a stale WebSocket connection:
```bash
supervisorctl restart signal_cli
# Wait 10 seconds for reconnection
supervisorctl restart signal_bridge
```

---

### Python Import Shadowing

This is the most complex issue you may encounter. The plugin's `helpers/` directory shadows Agent Zero's core `helpers/` module.

#### Symptoms
- `ImportError: cannot import name 'dotenv' from 'helpers'`
- `ModuleNotFoundError: No module named 'helpers.files'`
- The bridge works for chat but crashes when trying to use agent tools

#### Root Cause
Python's module resolution registers `plugins/signal/helpers/__init__.py` as the `helpers` namespace. When A0's `agent.py` → `models.py` tries `from helpers import dotenv`, it finds the plugin's empty `helpers/` instead of `/a0/helpers/`.

#### Solution
The `run_signal_bridge.py` script handles this by force-loading A0's helpers into `sys.modules` before any plugin imports:
```python
import importlib
_a0_helpers = importlib.import_module("helpers")
sys.modules["helpers"] = _a0_helpers
# Pre-load all submodules...
```

**This is why `run_signal_bridge.py` MUST live at `/a0/` root**, not inside the plugin directory. If it ran from within `plugins/signal/`, Python would still resolve `helpers` to the plugin's package.

---

### WebUI Issues

#### WebUI "Save" doesn't persist plugin settings
The outer A0 "Save" button does NOT save plugin custom fields. Use the plugin's own **"Save Signal Settings"** button inside the Signal settings panel.

#### Bridge shows "stopped" in WebUI but is actually running
The WebUI tracks the in-process bridge state (started via `start_chat_bridge()`). The supervisor-managed bridge runner is a separate process. The bridge IS running — check with:
```bash
supervisorctl status signal_bridge
```

---

### External Mode Issues

#### "websocket: upgrade token not found" when receiving messages
The signal-api container is running in `json-rpc` mode. The `/v1/receive` REST endpoint does not work in this mode — it requires a WebSocket connection. Switch to `native` mode:
```bash
docker stop signal-api && docker rm signal-api
docker run -d --name signal-api --network signal-net \
  -e MODE=native \
  -v signal-cli-config:/home/.local/share/signal-cli \
  bbernhard/signal-cli-rest-api:latest
```

#### Container crashes with "AUTO_RECEIVE_SCHEDULE can't be used with mode json-rpc"
Remove the `AUTO_RECEIVE_SCHEDULE` environment variable. It is incompatible with `json-rpc` mode and unnecessary in `native` mode (the bridge runner handles its own polling).

#### Send works but receive returns empty
This is the classic symptom of running in the wrong mode. `POST /v2/send` works in all modes, but `GET /v1/receive/{number}` only works in `native` or `normal` mode. Verify:
```bash
docker exec signal-api printenv MODE
# Must be "native" (not "json-rpc")
```

#### Bridge can't resolve "signal-api" hostname
Both containers must be on the same Docker network:
```bash
# Check network membership
docker network inspect signal-net

# If A0 container isn't on it:
docker network connect signal-net <a0-container>
```

#### Migrating data from integrated to external mode
Signal identity keys must be copied from the A0 container's `/opt/signal-cli-data/` to the Docker volume used by the external container. See [SETUP_SIGNAL_API.md](SETUP_SIGNAL_API.md#step-4-migrating-from-integrated-mode) for the full migration procedure.

---

### VPN / Network Issues

#### Signal connections fail through VPN
Signal uses its own certificate authority ("Signal Messenger, LLC"), which is not in the system trust store. Some VPN configurations intercept TLS and break signal-cli's certificate pinning.

**During initial linking:** Disable VPN temporarily.
**After linking:** VPN typically doesn't affect existing connections. signal-cli maintains a persistent WebSocket that survives VPN reconnections.

#### `curl` to `chat.signal.org` returns certificate error
This is expected — Signal uses its own CA. signal-cli handles certificate validation internally and doesn't rely on system certificates. The `curl` failure doesn't mean signal-cli can't connect.

---

### Performance

#### Browser/web tasks take 1-3 minutes via Signal
This is expected behavior. In elevated mode, web tasks use Agent Zero's full browser automation tool, which involves multiple LLM calls per step. Each step (navigate, evaluate, extract) takes 10-15 seconds.

#### Responses are truncated with "Show More" link
This is native Signal behavior for messages over ~2000 characters. The full response is available by tapping "Show More". This is working as intended.

---

### Recovery Procedures

#### Complete Reset (Nuclear Option)
```bash
# Stop everything
supervisorctl stop signal_cli signal_bridge

# Remove data (WARNING: you will need to re-link your phone number)
rm -rf /opt/signal-cli-data/*
rm -rf /root/.local/share/signal-cli/data/*

# Remove configs
rm -f /a0/usr/plugins/signal/config.json
rm -f /a0/usr/plugins/signal/data/chat_bridge_state.json

# Reinstall
cd /path/to/a0-signal
./install.sh --integrated

# Start fresh from Step 4 (Link a Signal Phone Number)
```

#### Preserve Account, Reset Bridge Only
```bash
supervisorctl stop signal_bridge
rm -f /a0/usr/plugins/signal/data/chat_bridge_state.json
supervisorctl start signal_bridge
```

#### Re-deploy After Container Rebuild (Integrated)
```bash
# Signal data is preserved in /opt/signal-cli-data/ volume
# Plugin config is preserved in /a0/usr/plugins/signal/ volume
# But binary and supervisor configs need reinstalling:

cd /path/to/a0-signal
./install.sh --integrated
# Binary is re-downloaded, supervisor configs are recreated
# Account data is preserved — no need to re-link

supervisorctl start signal_cli signal_bridge
```

#### Re-deploy After Container Rebuild (External)
```bash
# Signal data is preserved in the signal-cli-config Docker volume
# Plugin config is preserved in /a0/usr/plugins/signal/ volume
# The signal-api container is independent and unaffected

cd /path/to/a0-signal
./install.sh --external
# Supervisor bridge config is recreated (no signal-cli binary needed)
# signal-api container continues running with preserved account data

# Ensure A0 container is on the same Docker network
docker network connect signal-net <a0-container>
supervisorctl start signal_bridge
```
