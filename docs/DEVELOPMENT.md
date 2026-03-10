# Signal Integration Plugin — Development Guide

## Project Structure

```
a0-signal/
├── plugin.yaml              # Plugin manifest
├── default_config.yaml      # Default settings
├── initialize.py            # Dependency installer + signal-cli setup
├── install.sh               # Deployment script
├── run_signal_bridge.py     # Standalone bridge runner (deployed to /a0/)
├── helpers/                 # Shared modules
│   ├── __init__.py
│   ├── signal_client.py        # Dual-mode factory + REST client (external mode)
│   ├── signal_jsonrpc.py       # JSON-RPC client (integrated mode)
│   ├── signal_daemon.py        # signal-cli daemon + bridge lifecycle management
│   ├── signal_bridge.py        # Chat bridge (message routing + auth)
│   ├── sanitize.py             # Prompt injection defense
│   └── poll_state.py           # Background polling state
├── tools/                   # Tool implementations (5)
│   ├── signal_send.py
│   ├── signal_read.py
│   ├── signal_chat.py
│   ├── signal_groups.py
│   └── signal_contacts.py
├── prompts/                 # Tool prompt definitions (5)
├── api/                     # API handlers (3)
│   ├── signal_test.py
│   ├── signal_config_api.py
│   └── signal_bridge_api.py
├── webui/                   # Dashboard and settings UI
│   ├── main.html
│   └── config.html
├── skills/                  # Skill definitions (3)
├── extensions/              # Agent init hooks
│   └── python/agent_init/_10_signal_chat.py
├── tests/                   # Regression test suite
│   └── regression_test.sh
└── docs/                    # Documentation
```

## Development Setup

1. Start the dev container:
   ```bash
   docker start <container>
   ```

2. Install the plugin:
   ```bash
   docker cp a0-signal/. <container>:/tmp/a0-signal/
   docker exec <container> bash -c "cd /tmp/a0-signal && ./install.sh --integrated"
   ```

3. Run tests:
   ```bash
   ./tests/regression_test.sh <container> <port>
   ```

## Adding a New Tool

1. Create `tools/signal_<action>.py` with a Tool subclass:
   ```python
   from helpers.tool import Tool, Response


   class SignalAction(Tool):
       async def execute(self, **kwargs) -> Response:
           # Implementation
           return Response(message="Result", break_loop=False)
   ```

2. Create `prompts/agent.system.tool.signal_<action>.md` with JSON examples

3. Add import test to `tests/regression_test.sh`

4. Update documentation

## Code Style

- Follow existing patterns from Discord/Telegram plugins
- Use `async/await` for all I/O operations
- Always close httpx clients when done
- Return `Response(message=..., break_loop=False)` from tools
- Sanitize ALL external content before passing to LLM
- All API handlers must have `requires_csrf() -> True`
- WebUI must use `data-sig=` attributes, not bare IDs
- WebUI must use `globalThis.fetchApi || fetch`

## Key Patterns

### Tool Implementation
```python
from helpers.tool import Tool, Response
from plugins.signal.helpers.signal_client import (
    SignalAPIError, get_signal_config, create_signal_client,
)
from plugins.signal.helpers.sanitize import require_auth, validate_phone_number

class MyTool(Tool):
    async def execute(self, **kwargs) -> Response:
        config = get_signal_config(self.agent)

        try:
            require_auth(config)
        except ValueError as e:
            return Response(message=f"Auth error: {e}", break_loop=False)

        # Factory returns the correct client for the configured mode
        # (SignalJsonRpcClient for integrated, SignalClient for external)
        client = create_signal_client(agent=self.agent)
        # ... use client ...
        await client.close()
        return Response(message="Done", break_loop=False)
```

### API Handler
```python
from helpers.api import ApiHandler, Request, Response

class MyApi(ApiHandler):
    @classmethod
    def requires_csrf(cls) -> bool:
        return True  # MANDATORY

    async def process(self, input: dict, request: Request) -> dict | Response:
        return {"ok": True}
```

### Signal Client Usage
```python
from plugins.signal.helpers.signal_client import create_signal_client

# Factory auto-detects mode from config and returns the correct client
client = create_signal_client()

# Send a message (same API regardless of mode)
await client.send_message(
    recipients=["+0987654321"],
    message="Hello from Agent Zero!",
)

# Receive messages
messages = await client.receive_messages()

# List groups
groups = await client.list_groups()

await client.close()
```

### Sanitization
```python
from plugins.signal.helpers.sanitize import (
    sanitize_content,
    sanitize_username,
    validate_phone_number,
    validate_group_id,
    is_contact_allowed,
)

# Always sanitize user-provided content before passing to LLM
safe_content = sanitize_content(raw_message)
safe_name = sanitize_username(contact_name)

# Validate identifiers before API calls
validate_phone_number("+1234567890")  # raises ValueError if invalid
validate_group_id("dGVzdA==")         # raises ValueError if invalid

# Check allowlist
if not is_contact_allowed(sender, config):
    return  # silently ignore
```

---

## Architecture Deep Dive

### Signal Protocol Stack
```
Agent Zero Plugin (Python, httpx)
    ↓ JSON-RPC (integrated) or REST (external)
signal-cli daemon / signal-cli-rest-api
    ↓ Signal Protocol (X3DH + Double Ratchet)
libsignal-client (Rust, official Signal crypto library)
    ↓ Encrypted messages
Signal Servers
```

### Dual-Mode Architecture
The `create_signal_client()` factory in `signal_client.py` returns the appropriate client:
- **Integrated mode**: `SignalJsonRpcClient` — JSON-RPC 2.0 over HTTP to local daemon
- **External mode**: `SignalClient` — REST API to bbernhard container

Both clients implement the same public API (send_message, receive_messages, list_groups, etc.) so tools and the bridge work transparently with either backend.

### External Mode: Key Findings

The external mode was validated end-to-end in March 2026. Critical findings:

| Topic | Detail |
|-------|--------|
| **Container mode** | Must use `MODE=native`. `json-rpc` mode requires WebSocket for receive; REST `/v1/receive` fails. |
| **Send endpoint** | `POST /v2/send` works in all modes (native, json-rpc, normal). |
| **Receive endpoint** | `GET /v1/receive/{number}` only works in `native` or `normal` mode. |
| **AUTO_RECEIVE_SCHEDULE** | Incompatible with `json-rpc` mode; causes container crash. Unnecessary in `native` mode. |
| **Docker networking** | Containers communicate via Docker bridge network DNS (e.g., `http://signal-api:8080`). |
| **Bridge runner** | `create_signal_client(config)` factory transparently returns `SignalClient` for external mode. |
| **Health check** | External mode uses `get_about()` (REST) instead of `health_check()` (JSON-RPC daemon endpoint). |
| **Data migration** | Signal identity keys must be copied from integrated `/opt/signal-cli-data/` to Docker volume. |

### Chat Bridge Architecture

The chat bridge has two execution paths:

#### In-Process Bridge (agent_init extension)
- Triggered by `_10_signal_chat.py` when a WebUI conversation starts
- Runs as an asyncio task inside the Agent Zero web process
- Uses `start_chat_bridge()` / `stop_chat_bridge()` from `signal_bridge.py`
- Tracked by the WebUI status indicator

#### Standalone Bridge Runner (supervisor service) — RECOMMENDED
- Runs as `/a0/run_signal_bridge.py` managed by supervisord
- Independent of the WebUI process
- Handles import shadowing, env vars, and --dockerized flag
- Starts on container boot (if `autostart=true`)
- More reliable and always-on

**Why both?** The in-process bridge is useful for quick testing and WebUI control. The standalone runner is for production — it survives WebUI restarts and doesn't require a user to open the WebUI to start the bridge.

### Standalone Bridge Runner: Key Design Decisions

#### 1. Lives at `/a0/` root, NOT inside the plugin directory

```
/a0/run_signal_bridge.py          ← HERE (correct)
/a0/usr/plugins/signal/run_...    ← NOT here (would cause import shadowing)
```

**Why:** Python's module resolution. If the script ran from within `plugins/signal/`, the plugin's `helpers/__init__.py` would be registered as the `helpers` namespace, shadowing A0's core `helpers/` package. Running from `/a0/` root means Python finds `/a0/helpers/` first.

#### 2. Force-loads A0 helpers via importlib

```python
import importlib
_a0_helpers = importlib.import_module("helpers")
sys.modules["helpers"] = _a0_helpers
for _sub in ["dotenv", "files", "plugins", ...]:
    _mod = importlib.import_module(f"helpers.{_sub}")
    sys.modules[f"helpers.{_sub}"] = _mod
```

**Why:** Even at `/a0/` root, later imports of plugin code can cause Python to re-resolve the `helpers` namespace. Pre-loading all submodules into `sys.modules` ensures the cache is populated with the correct references before any plugin code is imported.

#### 3. Sets `--dockerized=true` via sys.argv

```python
sys.argv = ["run_signal_bridge.py", "--dockerized=true"]
from helpers import runtime
runtime.initialize()
```

**Why:** Without this flag, A0's `runtime.is_development()` returns True, and code execution tries RFC (Remote Function Call) HTTP requests to `localhost:55080` instead of executing locally. This causes "Cannot connect to host localhost:55080" errors in elevated mode.

#### 4. Loads `.env` files manually

```python
for env_path in ["/a0/usr/.env", "/a0/.env"]:
    # Parse KEY=VALUE lines
```

**Why:** The bridge runs as a supervisor service, not through A0's normal startup. It doesn't inherit the web UI's environment. LLM API keys (like `API_KEY_VENICE`) are in `/a0/usr/.env` and must be loaded explicitly.

#### 5. Reads config.json directly (not through plugin framework)

```python
for p in ["/a0/usr/plugins/signal/config.json", "/a0/plugins/signal/config.json"]:
    with open(p) as f: config = json.load(f)
```

**Why:** `get_signal_config()` goes through A0's plugin framework, which requires the full A0 context to be initialized. The standalone runner may not have the plugin framework fully loaded. Direct JSON read is simpler and more reliable.

---

## Python Import Shadowing — Detailed Analysis

This is the most significant technical challenge in the plugin and affects any A0 plugin that has a `helpers/` subdirectory.

### The Problem

```
/a0/helpers/              ← A0 core helpers (dotenv, files, plugins, etc.)
/a0/plugins/signal/helpers/  ← Plugin helpers (signal_client, signal_bridge, etc.)
```

Python's import system registers packages by directory. When `plugins/signal/helpers/__init__.py` exists (even if empty), Python may register it as the `helpers` namespace, depending on import order and the working directory.

### Import Chain That Fails

```
run_signal_bridge.py
  → from plugins.signal.helpers.signal_bridge import SignalChatBridge
    → signal_bridge.py: from plugins.signal.helpers.signal_client import ...
      → signal_client.py: from helpers import plugins     ← FAILS
        Python finds plugins/signal/helpers/ instead of /a0/helpers/
        ImportError: cannot import name 'plugins' from 'helpers'
```

### The Fix

Pre-populate `sys.modules` with the correct A0 helpers BEFORE any plugin code is imported:

```python
import importlib
import sys

# This resolves to /a0/helpers/ because we're running from /a0/
_a0_helpers = importlib.import_module("helpers")
sys.modules["helpers"] = _a0_helpers

# Pre-load all submodules that A0 core code needs
for sub in ["dotenv", "files", "plugins", "print_style", "yaml",
            "cache", "errors", "extension", "crypto", "defer", "dirty_json"]:
    try:
        mod = importlib.import_module(f"helpers.{sub}")
        sys.modules[f"helpers.{sub}"] = mod
    except Exception:
        pass

# NOW it's safe to import plugin code
from plugins.signal.helpers.signal_bridge import SignalChatBridge  # Works!
```

### Why Other Fixes Don't Work

| Approach | Why It Fails |
|----------|-------------|
| `sys.path.insert(0, "/a0")` | Doesn't help — the issue is sys.modules caching, not path order |
| `sys.path.pop(0)` to remove plugin dir | Plugin code can't find its own imports |
| Rename `helpers/` to `_helpers/` | Breaks all existing imports, tools, and API handlers |
| Remove `helpers/__init__.py` | Breaks Python package imports for the plugin |
| Use relative imports in plugin | A0's plugin loader doesn't support relative imports |

### Impact on Other Plugins

Any A0 plugin with a `helpers/` directory may encounter this issue if:
1. The plugin code is imported in a standalone process (not via A0's normal boot)
2. The standalone process needs A0 core helpers (agent, models, etc.)

The `importlib` pre-loading pattern in `run_signal_bridge.py` is the recommended solution.

---

## Supervisor Service Architecture

### Services Overview

| Service | Process | Purpose |
|---------|---------|---------|
| `run_ui` | A0 web server | Agent Zero WebUI + API |
| `signal_cli` | signal-cli daemon | Signal Protocol, message send/receive |
| `signal_bridge` | run_signal_bridge.py | Message polling + LLM routing |

### Startup Order

```
Container starts
  → supervisord starts all autostart=true services
    → signal_cli starts (daemon needs 5-10s to connect to Signal servers)
    → signal_bridge starts (waits up to 60s for daemon health check)
    → run_ui starts (A0 web interface)
```

The bridge runner has retry logic — it polls the daemon health endpoint every 2 seconds for up to 60 seconds before giving up.

### Log Files

| Log | Content |
|-----|---------|
| `/var/log/signal-cli.log` | Daemon stdout (connection info, message routing) |
| `/var/log/signal-cli-error.log` | Daemon stderr (errors, warnings) |
| `/var/log/signal-bridge.log` | Bridge stdout (message processing, LLM calls) |
| `/var/log/signal-bridge-error.log` | Bridge stderr (Python tracebacks) |

### Useful Debugging Commands

```bash
# Real-time bridge activity
tail -f /var/log/signal-bridge.log

# Check for Python errors
tail -50 /var/log/signal-bridge-error.log

# Signal daemon activity
tail -f /var/log/signal-cli.log

# Service status
supervisorctl status signal_cli signal_bridge

# Restart bridge (picks up config changes)
supervisorctl restart signal_bridge

# Test daemon directly
curl -s http://127.0.0.1:8080/api/v1/check
```

---

## Security Layers

| Layer | Component | Protection |
|-------|-----------|------------|
| Transport | Signal Protocol | E2E encryption |
| API | httpx + auth token | Bearer token auth |
| Input | sanitize.py | Injection defense |
| Access | allowlist | Contact filtering |
| Auth | HMAC comparison | Timing-safe auth |
| Rate | sliding window | Abuse prevention |
| Files | atomic write + 0o600 | Data protection |
| CSRF | requires_csrf=True | WebUI protection |
| Elevated | session timeout + lockout | Privilege escalation defense |
| Execution | --dockerized=true | Container-scoped code execution |

---

## Testing

Run the full regression suite:
```bash
./tests/regression_test.sh <container> <port>
```

Target: 75 tests across 11 categories (container health, installation, imports, APIs, sanitization, tools, prompts, skills, WebUI, framework compatibility, security hardening).

### Manual End-to-End Test Checklist

**Restricted Mode:**
- [ ] Send conversational message → get LLM response
- [ ] Ask knowledge question → accurate answer
- [ ] Request tool use → get "no tool access" response
- [ ] Send 10+ rapid messages → rate limiting activates

**Elevated Mode:**
- [ ] `!auth <key>` → session activated
- [ ] Create file → file appears on disk
- [ ] Run Python script → output returned
- [ ] Read file → contents returned
- [ ] Web search → results returned
- [ ] Fetch URL (browser) → page summarized (expect 30-120s)
- [ ] `!status` → session info displayed
- [ ] `!deauth` → back to restricted
- [ ] Read nonexistent file → graceful error

**Session Expiry:**
- [ ] Wait for session_timeout → elevated access revoked automatically
- [ ] After expiry, tool requests → restricted mode response
