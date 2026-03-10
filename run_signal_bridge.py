"""Standalone Signal chat bridge runner for Agent Zero.

This script runs the Signal chat bridge as an independent supervisor service,
outside the Agent Zero web process. It handles:
  - Loading environment variables from /a0/usr/.env
  - Resolving the Python import shadowing issue (plugins/signal/helpers/ vs /a0/helpers/)
  - Initializing the A0 runtime with --dockerized=true for local code execution
  - Polling the signal-cli JSON-RPC daemon for incoming messages
  - Routing messages through the SignalChatBridge (restricted or elevated mode)

WHY THIS EXISTS:
  The agent_init extension (_10_signal_chat.py) only fires when a user starts a
  WebUI conversation. For a persistent, always-on chat bridge we need an
  independent process managed by supervisord. Additionally, Python's module
  resolution causes the plugin's helpers/ package to shadow A0's core helpers/
  package. This script solves that by force-loading A0's helpers into
  sys.modules before any plugin code is imported.

USAGE:
  /opt/venv-a0/bin/python /a0/run_signal_bridge.py --dockerized=true

SUPERVISOR CONFIG:
  See signal_daemon.py:create_bridge_supervisor_config() for the supervisord
  program definition that runs this script.
"""

import asyncio
import json
import logging
import os
import sys

# Must run from A0 root so all imports resolve correctly
os.chdir("/a0")

# ---------------------------------------------------------------------------
# Load environment variables from .env files
# ---------------------------------------------------------------------------
# The bridge runs as a supervisor service and does NOT inherit the web UI's
# environment. We need API keys (e.g. API_KEY_VENICE) and Signal config.
for env_path in ["/a0/usr/.env", "/a0/.env"]:
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    # Don't override values already set (e.g. by supervisor env)
                    if value and key not in os.environ:
                        os.environ[key] = value

# ---------------------------------------------------------------------------
# Fix Python import shadowing
# ---------------------------------------------------------------------------
# PROBLEM: plugins/signal/helpers/__init__.py registers as the "helpers"
# namespace in sys.modules. When agent.py -> models.py tries
# "from helpers import dotenv", Python finds the plugin's helpers/ instead
# of /a0/helpers/. This causes ImportError crashes.
#
# SOLUTION: Force-load A0's core helpers module and all its submodules into
# sys.modules BEFORE any plugin code is imported. This ensures Python's
# module cache has the correct references.
import importlib

_a0_helpers = importlib.import_module("helpers")
sys.modules["helpers"] = _a0_helpers

# Pre-load all submodules that agent.py, models.py, and other A0 core code need
for _submodule in [
    "dotenv", "files", "plugins", "print_style", "yaml", "cache",
    "errors", "extension", "crypto", "defer", "dirty_json",
]:
    try:
        _mod = importlib.import_module(f"helpers.{_submodule}")
        sys.modules[f"helpers.{_submodule}"] = _mod
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Initialize Agent Zero runtime
# ---------------------------------------------------------------------------
# --dockerized=true is CRITICAL: without it, A0 thinks it's in development
# mode and tries to make RFC (Remote Function Call) HTTP requests to port
# 55080 instead of executing code locally. This causes "Cannot connect to
# host localhost:55080" errors in elevated mode.
sys.argv = ["run_signal_bridge.py", "--dockerized=true"]
from helpers import runtime
runtime.initialize()

from agent import AgentContext, AgentContextType
from initialize import initialize_agent

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("signal_bridge_runner")


def _load_config() -> dict:
    """Load bridge configuration from env vars + config.json.

    Priority: config.json values override env var defaults (env vars are
    the fallback for when config.json doesn't exist yet).
    """
    config = {
        "phone_number": os.environ.get("SIGNAL_PHONE_NUMBER", ""),
        "api": {
            "mode": os.environ.get("SIGNAL_MODE", "integrated"),
            "base_url": os.environ.get("SIGNAL_API_URL", "http://127.0.0.1:8080"),
        },
        "chat_bridge": {"allowed_numbers": []},
        "polling": {"interval_seconds": 10},
    }

    # Overlay with config.json (saved by WebUI)
    for config_path in [
        "/a0/usr/plugins/signal/config.json",
        "/a0/plugins/signal/config.json",
    ]:
        try:
            with open(config_path) as f:
                file_config = json.load(f)
            config["phone_number"] = file_config.get("phone_number", config["phone_number"])
            config["chat_bridge"] = file_config.get("chat_bridge", config["chat_bridge"])
            config["polling"] = file_config.get("polling", config["polling"])
            config["api"] = file_config.get("api", config["api"])
            break
        except Exception:
            pass

    return config


async def main():
    config = _load_config()
    phone = config["phone_number"]
    if not phone:
        logger.error("SIGNAL_PHONE_NUMBER not set and not found in config.json. Exiting.")
        sys.exit(1)

    allowed = config.get("chat_bridge", {}).get("allowed_numbers", [])
    poll_interval = config.get("polling", {}).get("interval_seconds", 10)

    # Connect to the signal-cli backend (integrated or external)
    mode = config.get("api", {}).get("mode", "integrated")
    from plugins.signal.helpers.signal_client import create_signal_client

    client = create_signal_client(config)
    base_url = config["api"].get("base_url", "http://127.0.0.1:8080")
    logger.info(f"Mode: {mode}, API: {base_url}")

    # Wait for backend to become healthy (up to 60 seconds)
    for attempt in range(30):
        try:
            if hasattr(client, "health_check"):
                if await client.health_check():
                    break
            else:
                # External mode: try get_about() as health check
                await client.get_about()
                break
        except Exception:
            pass
        logger.info(f"Waiting for signal-cli backend... (attempt {attempt + 1}/30)")
        await asyncio.sleep(2)
    else:
        logger.error(
            f"signal-cli backend at {base_url} did not become healthy after 60 seconds. "
            f"Mode: {mode}. "
            + ("Check: supervisorctl status signal_cli" if mode == "integrated"
               else "Check: docker ps | grep signal-api")
        )
        await client.close()
        sys.exit(1)

    logger.info(f"Connected to signal-cli backend ({mode} mode)")

    # Initialize the chat bridge
    from plugins.signal.helpers.signal_bridge import SignalChatBridge

    bridge = SignalChatBridge()
    logger.info(
        f"Bridge running. Phone: {phone}, "
        f"Allowed: {allowed or ['(all)']}, "
        f"Poll interval: {poll_interval}s"
    )

    # Main polling loop
    while True:
        try:
            messages = await client.receive_messages(timeout_seconds=1) or []
            for msg in messages:
                envelope = msg.get("envelope", msg)
                source = envelope.get("sourceNumber") or envelope.get("source", "")
                data_msg = envelope.get("dataMessage", {})
                text = data_msg.get("message", "")

                if not text or not source:
                    continue

                # Contact allowlist filter
                if allowed and source not in allowed:
                    continue

                logger.info(f"From {source}: {text[:50]}")

                try:
                    response = await bridge.process_message(
                        source, text, envelope.get("sourceName", "")
                    )
                    await client.send_message([source], response)
                    logger.info(f"Replied ({len(response)} chars)")
                except Exception as e:
                    logger.error(f"Response error: {e}", exc_info=True)

        except Exception as e:
            logger.error(f"Poll error: {e}")

        await asyncio.sleep(poll_interval)


if __name__ == "__main__":
    asyncio.run(main())
