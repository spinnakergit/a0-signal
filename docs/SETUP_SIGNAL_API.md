# Setting Up signal-cli-rest-api (External Mode)

This guide covers setting up the `signal-cli-rest-api` Docker container as the Signal backend for the plugin. External mode runs signal-cli in a separate Docker container, communicating with Agent Zero via REST API over a private Docker network.

## Overview

```
Agent Zero container
  └── signal_bridge (supervisor service)
        ↓ REST API (http://signal-api:8080)
signal-api container (bbernhard/signal-cli-rest-api)
  ├── signal-cli (handles Signal Protocol encryption)
  ├── libsignal-client (official Signal cryptographic library)
  └── REST API (exposes HTTP endpoints on :8080)
        ↓ Signal Protocol (E2E encrypted)
Signal Servers
```

---

## Quick Start

```bash
# 1. Create Docker network
docker network create signal-net

# 2. Connect your A0 container to it
docker network connect signal-net <your-a0-container>

# 3. Start signal-api container
docker run -d \
  --name signal-api \
  --network signal-net \
  -e MODE=native \
  -v signal-cli-config:/home/.local/share/signal-cli \
  -p 127.0.0.1:8080:8080 \
  bbernhard/signal-cli-rest-api:latest

# 4. Link your phone number (see Step 3 below)

# 5. Update plugin config to external mode
```

---

## Step 1: Create Docker Network

Both containers must be on the same Docker network for DNS-based container-to-container communication.

```bash
docker network create signal-net
docker network connect signal-net <your-a0-container>
```

This allows the A0 container to reach the signal-api container at `http://signal-api:8080` (Docker's built-in DNS resolves the container name).

---

## Step 2: Start the signal-api Container

```bash
docker run -d \
  --name signal-api \
  --network signal-net \
  -e MODE=native \
  -v signal-cli-config:/home/.local/share/signal-cli \
  -p 127.0.0.1:8080:8080 \
  bbernhard/signal-cli-rest-api:latest
```

### Critical: Use `MODE=native`

| Mode | Send | Receive (REST) | Notes |
|------|------|-----------------|-------|
| `native` | Yes | **Yes** | **Required for this plugin.** GraalVM binary, low memory, REST receive works. |
| `json-rpc` | Yes | **No** | Requires WebSocket for receive. REST `/v1/receive` fails with upgrade error. |
| `normal` | Yes | Yes | Spawns JVM per request. High memory (~500MB+), slow. Not recommended. |

**Why not `json-rpc`?** The plugin's bridge runner uses REST polling (`GET /v1/receive/{number}`) to fetch messages. In `json-rpc` mode, this endpoint returns a WebSocket upgrade error:

```
"websocket: the client is not using the websocket protocol:
 'upgrade' token not found in 'Connection' header"
```

Sending works in all modes (`POST /v2/send`), but **receiving only works with `native` or `normal`**.

### Do NOT Set `AUTO_RECEIVE_SCHEDULE`

```bash
# WRONG — causes container crash loop in json-rpc mode:
-e AUTO_RECEIVE_SCHEDULE="0 */5 * * * *"

# CORRECT — omit it entirely:
docker run -d --name signal-api -e MODE=native ...
```

`AUTO_RECEIVE_SCHEDULE` is incompatible with `json-rpc` mode and causes the container to exit immediately with:

```
Env variable AUTO_RECEIVE_SCHEDULE can't be used with mode json-rpc
```

In `native` mode, the plugin's bridge runner handles its own polling, so `AUTO_RECEIVE_SCHEDULE` is unnecessary.

---

## Step 3: Link a Phone Number

### Option A: Link as Secondary Device (Recommended)

Link to your existing Signal account. Open the signal-api Swagger UI or use curl:

```bash
# Get QR code for linking
curl "http://127.0.0.1:8080/v1/qrcodelink?device_name=AgentZero"
```

Then scan the QR code with your Signal app:
1. Open Signal on your phone
2. Go to **Settings > Linked Devices**
3. Tap **Link New Device**
4. Scan the QR code

### Option B: Register as Primary Device

Register a new phone number (needs SMS/voice verification):

```bash
# Start registration
curl -X POST "http://127.0.0.1:8080/v1/register/+1234567890"

# Verify with SMS code
curl -X POST "http://127.0.0.1:8080/v1/register/+1234567890/verify/123456"
```

### Verify Registration

```bash
# Check API is responding
curl http://127.0.0.1:8080/v1/about

# List registered accounts
curl http://127.0.0.1:8080/v1/accounts

# Send a test message
curl -X POST http://127.0.0.1:8080/v2/send \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Hello from Agent Zero!",
    "number": "+1YOURNUMBER",
    "recipients": ["+1TESTNUMBER"]
  }'

# Receive messages (only works in native/normal mode)
curl "http://127.0.0.1:8080/v1/receive/+1YOURNUMBER"
```

---

## Step 4: Migrating from Integrated Mode

If you're migrating an existing integrated-mode installation to external mode:

### Copy Signal Data to Docker Volume

```bash
# Create a temporary container to populate the volume
docker run --rm \
  -v signal-cli-config:/data \
  alpine sh -c "mkdir -p /data"

# Copy signal-cli data from the A0 container to the volume
docker cp <a0-container>:/opt/signal-cli-data/. /tmp/signal-data/
docker run --rm \
  -v signal-cli-config:/data \
  -v /tmp/signal-data:/source \
  alpine sh -c "cp -r /source/* /data/"
rm -rf /tmp/signal-data
```

### Stop Integrated Services

```bash
# Inside the A0 container
docker exec <a0-container> supervisorctl stop signal_cli
```

The `signal_cli` supervisor service is no longer needed in external mode. The `signal_bridge` service continues running — it now talks to the external container instead of the local daemon.

### Update Plugin Config

Edit `/a0/usr/plugins/signal/config.json` (or use the WebUI):

```json
{
  "api": {
    "mode": "external",
    "base_url": "http://signal-api:8080",
    "auth_token": ""
  },
  "phone_number": "+1YOURNUMBER"
}
```

Or set environment variables in `/a0/usr/.env`:

```bash
SIGNAL_MODE=external
SIGNAL_API_URL=http://signal-api:8080
SIGNAL_PHONE_NUMBER=+1YOURNUMBER
```

### Restart the Bridge

```bash
docker exec <a0-container> supervisorctl restart signal_bridge
```

Check the bridge logs to confirm external mode:

```bash
docker exec <a0-container> tail -20 /var/log/signal-bridge.log
# Expected:
#   Mode: external, API: http://signal-api:8080
#   Connected to signal-cli backend (external mode)
#   Bridge running. Phone: +1..., Allowed: [...], Poll interval: 10s
```

---

## Docker Compose Setup

For a complete setup with both containers:

```yaml
services:
  agent-zero:
    image: frdel/agent-zero:latest
    container_name: agent-zero
    ports:
      - "50080:80"
    volumes:
      - agent-zero-usr:/a0/usr
    networks:
      - signal-net

  signal-api:
    image: bbernhard/signal-cli-rest-api:latest
    container_name: signal-api
    environment:
      - MODE=native
    volumes:
      - signal-cli-config:/home/.local/share/signal-cli
    networks:
      - signal-net
    # SECURITY: Only expose to localhost for debugging.
    # Remove this ports section in production.
    # ports:
    #   - "127.0.0.1:8080:8080"

volumes:
  agent-zero-usr:
  signal-cli-config:

networks:
  signal-net:
    driver: bridge
```

**SECURITY**: The REST API has NO built-in authentication. It runs on a private Docker network (`signal-net`) with no port exposure in production. The A0 container reaches it via Docker DNS (`http://signal-api:8080`). Never expose port 8080 to the public internet.

---

## Security Considerations

### Protect the Config Volume

The `signal-cli-config` volume contains:
- Signal identity keys (private keys)
- Registration data
- Contact database
- Group membership

**Back this up** and protect it. If someone obtains these files, they can impersonate your Signal account.

```bash
# Backup
docker run --rm -v signal-cli-config:/data -v $(pwd):/backup \
  alpine tar czf /backup/signal-cli-backup.tar.gz -C /data .

# Restore
docker run --rm -v signal-cli-config:/data -v $(pwd):/backup \
  alpine tar xzf /backup/signal-cli-backup.tar.gz -C /data
```

### Network Isolation

```yaml
# docker-compose.yml — private network, no port exposure
services:
  signal-api:
    networks:
      - signal-net
    # NO ports: section — not exposed to host
```

If you must expose the API (not recommended), use a secured proxy:
- [secured-signal-api](https://github.com/CodeShellDev/secured-signal-api) — adds bearer token authentication

### Dedicated Phone Number

Use a dedicated phone number for the Signal API:
- Your message recipients will see this number
- Don't use your personal number for production
- Consider a VoIP number or prepaid SIM

---

## Troubleshooting

### Container won't start / keeps restarting

```bash
# Check logs
docker logs signal-api --tail 50

# Common causes:
# 1. AUTO_RECEIVE_SCHEDULE set with json-rpc mode — remove it
# 2. Invalid MODE value — use "native"
# 3. Volume permissions — ensure /home/.local/share/signal-cli is writable
```

### "Registration failed"
- The phone number may need a CAPTCHA
- Try using voice verification: `"use_voice": true`
- The number may already be registered — try linking instead

### "No messages received" (receive returns empty)
- **Check the mode:** `native` or `normal` required for REST receive
- In `json-rpc` mode, `/v1/receive` returns a WebSocket upgrade error
- Verify the number is registered: `curl http://127.0.0.1:8080/v1/accounts`
- Verify the sender is not blocked

### "websocket: upgrade token not found" error
This means the container is running in `json-rpc` mode. Switch to `native`:

```bash
docker stop signal-api && docker rm signal-api
docker run -d --name signal-api --network signal-net \
  -e MODE=native \
  -v signal-cli-config:/home/.local/share/signal-cli \
  bbernhard/signal-cli-rest-api:latest
```

### "Connection refused" from A0 bridge
- Ensure both containers are on the same Docker network:
  ```bash
  docker network inspect signal-net
  ```
- Verify the signal-api container is running: `docker ps | grep signal-api`
- Test from inside the A0 container:
  ```bash
  docker exec <a0-container> curl -s http://signal-api:8080/v1/about
  ```
- If DNS doesn't resolve, containers aren't on the same network

### Send works but receive doesn't
This confirms `json-rpc` mode. `/v2/send` works in all modes, but `/v1/receive` only works in `native` or `normal`. Switch to `MODE=native`.

### "Out of memory"
- Use `MODE=native` (not `normal`)
- `normal` mode spawns a new JVM per request and can use 500MB+ per call
- `native` uses the GraalVM-compiled binary with much lower memory footprint
