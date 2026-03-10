# Signal Plugin — Complete Deployment Guide

This guide walks through every step to get the Signal plugin running on an Agent Zero container, from installation through a working chat bridge with both restricted and elevated modes.

**Tested on:** Agent Zero dev-latest container (March 2026)
**Time estimate:** 15-30 minutes (depending on download speed)

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Installation Mode Selection](#2-installation-mode-selection)
3. [Install the Plugin](#3-install-the-plugin)
4. [Link a Signal Phone Number](#4-link-a-signal-phone-number)
5. [Configure the Plugin](#5-configure-the-plugin)
6. [Start the Services](#6-start-the-services)
7. [Test Basic Messaging](#7-test-basic-messaging)
8. [Set Up the Chat Bridge](#8-set-up-the-chat-bridge)
9. [Enable Elevated Mode](#9-enable-elevated-mode-optional)
10. [Verify Everything Works](#10-verify-everything-works)
11. [Persistent Services](#11-persistent-services-survive-container-restart)

---

## 1. Prerequisites

- A running Agent Zero Docker container (e.g., `agent-zero-dev-latest`)
- A phone number to use with Signal (can be your existing number — Agent Zero links as a secondary device)
- The Signal app installed on your primary phone
- Shell access to the container (`docker exec -it <container> bash`)
- An LLM API key configured in `/a0/usr/.env` (e.g., `API_KEY_VENICE=...`)

**Important:** If you use a VPN, you may need to disable it during the initial Signal linking process. signal-cli uses Signal's own certificate pinning and may fail to connect through some VPN configurations. Once linked, the connection typically works fine through VPN.

---

## 2. Installation Mode Selection

| Mode | How It Works | Best For |
|------|-------------|----------|
| **Integrated** | signal-cli runs natively inside the A0 container as a supervisord service. No extra containers needed. | Single-container deployments, simplicity |
| **External** | A separate `signal-cli-rest-api` Docker container (bbernhard) provides the API. | Multi-container deployments, existing signal-cli setups, lower A0 container footprint |

This guide covers **both modes**. Sections marked **(Integrated only)** or **(External only)** apply to that mode.

For a dedicated external mode setup guide with Docker Compose examples and migration instructions, see [SETUP_SIGNAL_API.md](SETUP_SIGNAL_API.md).

---

## 3. Install the Plugin

### Option A: From Source (Recommended)

Copy the plugin source into the container and run the installer:

**Integrated mode:**
```bash
# From your host machine
docker cp /path/to/a0-signal/. <container>:/tmp/a0-signal/

# Inside the container
docker exec -it <container> bash
cd /tmp/a0-signal
./install.sh --integrated
```

This will:
- Copy all plugin files to `/a0/usr/plugins/signal/`
- Install Python dependencies (`httpx`, `pyyaml`) into `/opt/venv-a0/`
- Download the signal-cli v0.14.1 native binary (~92MB GraalVM image) to `/opt/signal-cli-native/`
- Create supervisord configs for `signal_cli` (daemon) and `signal_bridge` (chat runner)
- Copy `run_signal_bridge.py` to `/a0/run_signal_bridge.py`
- Create the plugin symlink at `/a0/plugins/signal`
- Create the agent_init extension symlink
- Enable the plugin (`.toggle-1`)

**External mode:**
```bash
# From your host machine
docker cp /path/to/a0-signal/. <container>:/tmp/a0-signal/

# Inside the container
docker exec -it <container> bash
cd /tmp/a0-signal
./install.sh --external
```

This does the same as integrated but skips the signal-cli binary download and signal_cli supervisor config. You also need the external signal-api container — see [SETUP_SIGNAL_API.md](SETUP_SIGNAL_API.md).

### Option B: Manual Installation

```bash
# Inside the container
# 1. Copy plugin files
cp -r /path/to/a0-signal /a0/usr/plugins/signal

# 2. Create symlinks
ln -sf /a0/usr/plugins/signal /a0/plugins/signal
ln -sf /a0/usr/plugins/signal/extensions/python/agent_init/_10_signal_chat.py \
       /a0/extensions/python/agent_init/_10_signal_chat.py

# 3. Copy bridge runner to A0 root (MUST be at /a0/, not inside plugin dir)
cp /a0/usr/plugins/signal/run_signal_bridge.py /a0/run_signal_bridge.py
chmod 755 /a0/run_signal_bridge.py

# 4. Install dependencies
python3 /a0/usr/plugins/signal/initialize.py --integrated

# 5. Enable plugin
touch /a0/usr/plugins/signal/.toggle-1

# 6. Create data directory
mkdir -p /a0/usr/plugins/signal/data
chmod 700 /a0/usr/plugins/signal/data
```

### Verify Installation

```bash
# Check signal-cli binary
/opt/signal-cli-native/bin/signal-cli --version
# Expected: signal-cli 0.14.1

# Check supervisor configs
supervisorctl status signal_cli signal_bridge
# Expected: both show STOPPED (not yet started)

# Check symlinks
ls -la /a0/plugins/signal
# Expected: symlink to /a0/usr/plugins/signal

ls -la /a0/run_signal_bridge.py
# Expected: file exists with execute permission
```

---

## 4. Link a Signal Phone Number

The plugin needs a Signal identity. The easiest approach is linking as a **secondary device** to your existing Signal account.

### Step 1: Start the signal-cli Daemon

```bash
supervisorctl start signal_cli
```

Wait 5 seconds, then verify:
```bash
supervisorctl status signal_cli
# Expected: signal_cli  RUNNING  pid XXXX, uptime 0:00:XX
```

### Step 2: Generate the Link URI

```bash
/opt/signal-cli-native/bin/signal-cli --config /opt/signal-cli-data link -n "AgentZero"
```

This outputs a `sgnl://linkdevice?uuid=...` URI. You need to get this to your phone.

**Options to scan the QR code:**
- **Easiest:** Copy the URI and paste it into an online QR code generator on your phone's browser
- **Terminal:** If you have `qrencode` installed: `echo "sgnl://..." | qrencode -t UTF8`
- **WebUI:** Use the Signal plugin's registration wizard in Agent Zero's web interface

### Step 3: Scan with Signal App

1. Open Signal on your primary phone
2. Go to **Settings > Linked Devices**
3. Tap **Link New Device**
4. Scan the QR code

The terminal command should complete with a success message showing your phone number.

### Step 4: Copy Account Data

signal-cli stores the linked account data in `~/.local/share/signal-cli/`. Copy it to the persistent data directory:

```bash
cp -r /root/.local/share/signal-cli/data/* /opt/signal-cli-data/
```

### Step 5: Restart the Daemon

```bash
supervisorctl restart signal_cli
```

### Verify Linking

```bash
# Check daemon health
curl -s http://127.0.0.1:8080/api/v1/check
# Expected: 200 OK

# Send a test message (replace numbers)
curl -X POST http://127.0.0.1:8080/api/v1/rpc \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "send",
    "id": "1",
    "params": {
      "account": "+1YOURNUMBER",
      "recipient": ["+1TESTNUMBER"],
      "message": "Hello from Agent Zero!"
    }
  }'
```

### Troubleshooting Linking

| Problem | Solution |
|---------|----------|
| "Connection closed!" during link | Clear stale data: `rm -rf /root/.local/share/signal-cli/data/*` and retry |
| SSL certificate errors | Disable VPN during linking. signal-cli uses its own cert pinning |
| Link command hangs | Ensure no firewall blocks outbound connections to Signal servers |
| "Device not found" after restart | Make sure you copied data to `/opt/signal-cli-data/` |

---

## 5. Configure the Plugin

### Option A: WebUI Settings (Preferred)

1. Restart Agent Zero: `supervisorctl restart run_ui`
2. Open the WebUI in your browser
3. Navigate to **Settings > Plugins > Signal Integration**
4. Set:
   - **Mode:** Integrated
   - **Phone Number:** Your linked number (E.164 format: `+1234567890`)
   - **Allowed Contacts:** Phone numbers that can interact with the agent
5. Click **Save Signal Settings**

**Important:** The outer "Save" button in the A0 settings page does NOT save plugin custom fields. You must use the plugin's own "Save Signal Settings" button.

### Option B: Environment Variables

Add to `/a0/usr/.env`:

```bash
# Integrated mode:
SIGNAL_MODE=integrated
SIGNAL_PHONE_NUMBER=+1234567890
SIGNAL_API_URL=http://127.0.0.1:8080

# External mode:
SIGNAL_MODE=external
SIGNAL_PHONE_NUMBER=+1234567890
SIGNAL_API_URL=http://signal-api:8080
```

### Option C: Direct Config File

Create/edit `/a0/usr/plugins/signal/config.json`:

```json
{
  "api": {
    "mode": "integrated",
    "base_url": "http://127.0.0.1:8080",
    "auth_token": ""
  },
```

For external mode, change `mode` and `base_url`:

```json
{
  "api": {
    "mode": "external",
    "base_url": "http://signal-api:8080",
    "auth_token": ""
  },
  "phone_number": "+1234567890",
  "allowed_contacts": ["+1YOURCONTACT"],
  "polling": {
    "interval_seconds": 30,
    "auto_analyze_images": true
  },
  "chat_bridge": {
    "auto_start": true,
    "allowed_numbers": ["+1YOURCONTACT"],
    "allow_elevated": false,
    "auth_key": "",
    "session_timeout": 3600
  },
  "security": {
    "max_message_length": 4000,
    "sanitize_content": true,
    "strip_injection_patterns": true
  }
}
```

---

## 6. Start the Services

**Integrated mode:**
```bash
# Start the signal-cli daemon
supervisorctl start signal_cli

# Start the chat bridge runner
supervisorctl start signal_bridge

# Restart Agent Zero to load the plugin
supervisorctl restart run_ui
```

**External mode:**
```bash
# Ensure the signal-api container is running
docker ps | grep signal-api

# Ensure A0 container is on the same network
docker network connect signal-net <a0-container>

# Start the chat bridge runner (no signal_cli needed)
supervisorctl start signal_bridge

# Restart Agent Zero to load the plugin
supervisorctl restart run_ui
```

Verify services are running:
```bash
supervisorctl status
# Integrated expected:
#   run_ui          RUNNING   pid ...
#   signal_cli      RUNNING   pid ...
#   signal_bridge   RUNNING   pid ...

# External expected:
#   run_ui          RUNNING   pid ...
#   signal_bridge   RUNNING   pid ...
#   (signal_cli is not needed)
```

Check the bridge logs:
```bash
tail -20 /var/log/signal-bridge.log
# Integrated: "Mode: integrated, API: http://127.0.0.1:8080"
# External:   "Mode: external, API: http://signal-api:8080"
# Both: "Connected to signal-cli backend (...)" and "Bridge running"
```

---

## 7. Test Basic Messaging

### Via Agent Zero WebUI

Start a conversation and try:

```
Send a Signal message to +1234567890 saying "Hello from Agent Zero!"
```

```
Check for new Signal messages
```

```
List my Signal groups
```

### Via Signal App

Send a message from your phone to the linked number. If the bridge is running, you should get a response back.

---

## 8. Set Up the Chat Bridge

The chat bridge allows real-time, bidirectional Signal messaging. Send a message to the Signal number and get an AI response.

### How It Works

1. The `signal_bridge` supervisor service runs `run_signal_bridge.py`
2. It polls the signal-cli daemon every N seconds for new messages
3. Messages from allowed contacts are routed through Agent Zero's LLM
4. In **restricted mode** (default): LLM responds conversationally — no tools, no code, no file access
5. In **elevated mode** (opt-in): Full agent loop with tools, code execution, web browsing, etc.
6. Response is sent back via Signal

### Configure Allowed Contacts

Only contacts in the `allowed_numbers` list will get responses. Set this in:
- WebUI: Signal plugin settings > Chat Bridge > Allowed Numbers
- Config file: `chat_bridge.allowed_numbers` array
- Leave empty to allow all contacts (not recommended for production)

### Verify the Bridge

```bash
# Check bridge status
supervisorctl status signal_bridge
# Expected: RUNNING

# Watch logs in real-time
tail -f /var/log/signal-bridge.log

# Send a message from your phone — you should see it in the logs
```

---

## 9. Enable Elevated Mode (Optional)

Elevated mode gives authenticated Signal users full Agent Zero access — tools, code execution, file operations, web browsing, etc.

### Enable in Config

Set in `/a0/usr/plugins/signal/config.json` (or via WebUI):

```json
{
  "chat_bridge": {
    "allow_elevated": true,
    "auth_key": "",
    "session_timeout": 3600
  }
}
```

If `auth_key` is empty, one is auto-generated on first `!auth` attempt and saved to the config file.

### Generate an RFC Password

Elevated mode code execution requires an RFC password. If not already set:

```bash
# Inside the container, generate and save an RFC password
python3 -c "
import secrets
pwd = secrets.token_urlsafe(32)
print(f'RFC_PASSWORD={pwd}')
" >> /a0/usr/.env
```

### Using Elevated Mode via Signal

1. Send `!auth <key>` in the Signal chat (the auth key from your config)
2. You now have full agent access for the configured session timeout (default: 1 hour)
3. Try: "Create a file called test.txt in the working directory"
4. Try: "Run a Python script that prints the current date"
5. Try: "Search the web for the latest news about AI"
6. Send `!status` to check session info
7. Send `!deauth` to end the elevated session and return to restricted mode

### Copying the Auth Key to Signal

The auth key is a long random string that's hard to type manually. Options:
- Copy from WebUI settings, email it to yourself, paste into Signal
- Use a password manager to transfer the key
- Use `!status` in Signal to check if you're currently elevated

### Security Considerations

- Elevated sessions expire after `session_timeout` seconds (default: 1 hour)
- Auth uses constant-time HMAC comparison (timing-attack resistant)
- 5 failed auth attempts in 5 minutes triggers a lockout
- The auth key is never logged or exposed in API responses
- All code execution runs inside the A0 container (sandboxed)

---

## 10. Verify Everything Works

### Restricted Mode Checklist

- [ ] Send a conversational message from phone → get AI response
- [ ] Ask a knowledge question → get accurate answer
- [ ] Ask to run a command → get "I don't have tool access" response
- [ ] Send 10+ messages quickly → rate limiting kicks in

### Elevated Mode Checklist

- [ ] `!auth <key>` → "Elevated session active"
- [ ] "Create a file" → file created on disk
- [ ] "Run a Python script" → code executed, output returned
- [ ] "Read a file" → file contents returned
- [ ] "Search the web" → web results returned
- [ ] `!status` → shows session info and time remaining
- [ ] `!deauth` → "Back to restricted mode"
- [ ] After deauth, tool requests fail → confirmed restricted

### Long Response Behavior

Signal handles long messages with a "Show More" link. Messages over ~2000 characters are truncated with a clickable expansion. This is native Signal behavior and works well for detailed agent responses.

### Response Time Expectations

| Request Type | Expected Time |
|-------------|--------------|
| Conversational chat (restricted) | 2-5 seconds |
| File operations (elevated) | 3-8 seconds |
| Code execution (elevated) | 5-15 seconds |
| Web search (elevated) | 10-30 seconds |
| Browser/web fetch (elevated) | 30-120 seconds |

---

## 11. Persistent Services (Survive Container Restart)

To make the Signal services start automatically when the container starts:

### Edit the Supervisor Config

The supervisor configs are in `/etc/supervisor/conf.d/supervisord.conf`. Ensure both Signal sections have `autostart=true`:

```ini
[program:signal_cli]
command=/opt/signal-cli-native/bin/signal-cli --config /opt/signal-cli-data daemon --http 127.0.0.1:8080 --receive-mode=on-connection
directory=/opt/signal-cli-native
autostart=true
autorestart=true
startretries=3
startsecs=5
stopwaitsecs=10
stdout_logfile=/var/log/signal-cli.log
stderr_logfile=/var/log/signal-cli-error.log
stdout_logfile_maxbytes=10MB
stderr_logfile_maxbytes=10MB

[program:signal_bridge]
command=/opt/venv-a0/bin/python /a0/run_signal_bridge.py --dockerized=true
directory=/a0
autostart=true
autorestart=true
startretries=20
startsecs=5
stopwaitsecs=10
stdout_logfile=/var/log/signal-bridge.log
stderr_logfile=/var/log/signal-bridge-error.log
stdout_logfile_maxbytes=10MB
stderr_logfile_maxbytes=10MB
```

Then reload:
```bash
supervisorctl reread
supervisorctl update
```

### Persisting Across Container Rebuilds

The following directories contain state that must be preserved:

| Path | Contains | Mount As |
|------|----------|----------|
| `/opt/signal-cli-data/` | Signal identity keys, contacts, groups | Docker volume |
| `/a0/usr/plugins/signal/` | Plugin code + config.json | Docker volume (via `/a0/usr/`) |
| `/a0/usr/.env` | API keys, RFC password | Docker volume (via `/a0/usr/`) |

Agent Zero's standard volume mount (`/a0/usr/`) covers the plugin config and `.env`. You need to add a separate volume for signal-cli data:

```yaml
volumes:
  - agent-zero-usr:/a0/usr
  - signal-cli-data:/opt/signal-cli-data
```

**Warning:** `/opt/signal-cli-native/` (the binary) and `/etc/supervisor/conf.d/` (supervisor configs) are NOT in persistent volumes. After a container rebuild, you must re-run `./install.sh --integrated` (or `--external`) to reinstall the binary and recreate supervisor configs. Your linked phone number and contacts are preserved in the data volume.

### External Mode Volumes

In external mode, Signal account data lives in the `signal-cli-config` Docker volume managed by the signal-api container. The A0 container only needs:

| Path | Contains | Mount As |
|------|----------|----------|
| `/a0/usr/plugins/signal/` | Plugin code + config.json | Docker volume (via `/a0/usr/`) |
| `/a0/usr/.env` | API keys, RFC password | Docker volume (via `/a0/usr/`) |

The signal-api container:

| Path | Contains | Mount As |
|------|----------|----------|
| `/home/.local/share/signal-cli` | Signal identity keys, contacts, groups | `signal-cli-config` Docker volume |

---

## Quick Reference

### Useful Commands

```bash
# Service management
supervisorctl status                      # All services
supervisorctl start signal_cli            # Start daemon
supervisorctl start signal_bridge         # Start bridge
supervisorctl restart signal_bridge       # Restart bridge (picks up config changes)
supervisorctl tail -f signal_bridge       # Follow bridge logs

# Logs
tail -f /var/log/signal-bridge.log        # Bridge activity
tail -f /var/log/signal-cli.log           # Daemon activity
tail -f /var/log/signal-bridge-error.log  # Bridge errors

# Health checks
curl -s http://127.0.0.1:8080/api/v1/check   # Daemon health
supervisorctl status signal_bridge             # Bridge status

# Signal commands (via signal-cli directly)
/opt/signal-cli-native/bin/signal-cli --config /opt/signal-cli-data listAccounts
/opt/signal-cli-native/bin/signal-cli --config /opt/signal-cli-data listContacts
/opt/signal-cli-native/bin/signal-cli --config /opt/signal-cli-data listGroups
```

### Signal Chat Commands

| Command | Effect |
|---------|--------|
| `!auth <key>` | Authenticate for elevated mode |
| `!deauth` | End elevated session |
| `!status` | Show current mode and session info |

### Config File Locations

| File | Purpose |
|------|---------|
| `/a0/usr/plugins/signal/config.json` | Plugin settings (phone, contacts, bridge) |
| `/a0/usr/.env` | API keys, RFC password |
| `/etc/supervisor/conf.d/supervisord.conf` | Supervisor service definitions |
| `/opt/signal-cli-data/` | Signal account data (keys, contacts) |
| `/a0/run_signal_bridge.py` | Standalone bridge runner |
| `/var/log/signal-bridge.log` | Bridge logs |
| `/var/log/signal-cli.log` | Daemon logs |
