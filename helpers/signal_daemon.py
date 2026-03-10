"""Manage the local signal-cli daemon (integrated mode).

When the plugin runs in *integrated* mode, signal-cli is installed as a native
binary inside the Agent Zero container and runs as a supervisord service.

This module provides helpers to:
  - detect whether signal-cli is installed
  - install the native binary (download + extract)
  - create / remove the supervisord program config
  - start / stop / restart the daemon
  - check daemon health
"""

import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Optional

import httpx

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SIGNAL_CLI_VERSION = "0.14.1"
SIGNAL_CLI_DIR = Path("/opt/signal-cli-native")
SIGNAL_CLI_BIN = SIGNAL_CLI_DIR / "bin" / "signal-cli"
SIGNAL_CLI_DATA = Path("/opt/signal-cli-data")
SUPERVISOR_CONF_DIR = Path("/etc/supervisor/conf.d")
SUPERVISOR_CONF = SUPERVISOR_CONF_DIR / "signal_cli.conf"
SUPERVISOR_MAIN_CONF = SUPERVISOR_CONF_DIR / "supervisord.conf"
BRIDGE_RUNNER = Path("/a0/run_signal_bridge.py")
DAEMON_URL = "http://127.0.0.1:8080"

_DOWNLOAD_URL = (
    "https://github.com/AsamK/signal-cli/releases/download/"
    f"v{SIGNAL_CLI_VERSION}/signal-cli-{SIGNAL_CLI_VERSION}-Linux-native.tar.gz"
)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def is_installed() -> bool:
    """Check if signal-cli native binary is installed."""
    return SIGNAL_CLI_BIN.exists() and os.access(SIGNAL_CLI_BIN, os.X_OK)


def is_daemon_configured() -> bool:
    """Check if the supervisord program config exists."""
    return SUPERVISOR_CONF.exists()


def get_daemon_status() -> str:
    """Get daemon status from supervisord. Returns RUNNING, STOPPED, etc."""
    try:
        result = subprocess.run(
            ["supervisorctl", "status", "signal_cli"],
            capture_output=True, text=True, timeout=5,
        )
        # Output format: "signal_cli   RUNNING   pid 1234, uptime 0:01:23"
        parts = result.stdout.strip().split()
        if len(parts) >= 2:
            return parts[1]
        return "UNKNOWN"
    except Exception:
        return "UNKNOWN"


def is_daemon_healthy() -> bool:
    """Check if the daemon HTTP endpoint is responding."""
    try:
        resp = httpx.get(f"{DAEMON_URL}/api/v1/check", timeout=3.0)
        return resp.status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Installation
# ---------------------------------------------------------------------------

def install_signal_cli(version: str = SIGNAL_CLI_VERSION) -> bool:
    """Download and install the signal-cli native binary.

    Returns True on success, False on failure.
    """
    download_url = (
        "https://github.com/AsamK/signal-cli/releases/download/"
        f"v{version}/signal-cli-{version}-Linux-native.tar.gz"
    )

    print(f"[Signal Plugin] Downloading signal-cli v{version} native binary...")
    try:
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
            tmp_path = tmp.name

        # Download
        with httpx.stream("GET", download_url, follow_redirects=True, timeout=300.0) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            with open(tmp_path, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=1024 * 256):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        pct = int(downloaded / total * 100)
                        print(f"\r[Signal Plugin] Downloading... {pct}%", end="", flush=True)
            print()

        # Extract
        print(f"[Signal Plugin] Extracting to {SIGNAL_CLI_DIR}...")
        if SIGNAL_CLI_DIR.exists():
            shutil.rmtree(SIGNAL_CLI_DIR)
        SIGNAL_CLI_DIR.mkdir(parents=True, exist_ok=True)

        with tarfile.open(tmp_path, "r:gz") as tar:
            # Strip top-level directory (signal-cli-0.14.1/)
            members = tar.getmembers()
            prefix = ""
            if members and "/" in members[0].name:
                prefix = members[0].name.split("/")[0] + "/"

            for member in members:
                if member.name.startswith(prefix):
                    member.name = member.name[len(prefix):]
                if member.name:  # skip empty after stripping
                    tar.extract(member, SIGNAL_CLI_DIR)

        # Ensure binary is executable
        if SIGNAL_CLI_BIN.exists():
            os.chmod(SIGNAL_CLI_BIN, 0o755)
        else:
            # Try to find it
            for p in SIGNAL_CLI_DIR.rglob("signal-cli"):
                if p.is_file():
                    os.chmod(p, 0o755)
                    # Create expected path
                    SIGNAL_CLI_BIN.parent.mkdir(parents=True, exist_ok=True)
                    if p != SIGNAL_CLI_BIN:
                        shutil.move(str(p), str(SIGNAL_CLI_BIN))
                    break

        # Create data directory
        SIGNAL_CLI_DATA.mkdir(parents=True, exist_ok=True)
        os.chmod(SIGNAL_CLI_DATA, 0o700)

        print(f"[Signal Plugin] signal-cli v{version} installed at {SIGNAL_CLI_BIN}")
        return True

    except Exception as e:
        print(f"[Signal Plugin] ERROR: Failed to install signal-cli: {e}")
        return False
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def uninstall_signal_cli():
    """Remove signal-cli binary and supervisord config."""
    remove_supervisor_config()
    if SIGNAL_CLI_DIR.exists():
        shutil.rmtree(SIGNAL_CLI_DIR)
    print("[Signal Plugin] signal-cli removed.")


# ---------------------------------------------------------------------------
# Supervisord integration
# ---------------------------------------------------------------------------

def _append_to_supervisor_conf(section_name: str, config_block: str):
    """Append a program config to the main supervisord.conf if not already present.

    IMPORTANT: Some A0 container builds only read from the main supervisord.conf
    file and ignore separate .conf files in conf.d/. To ensure compatibility,
    we append directly to the main config file. The section marker comment is
    used to detect if the config was already added.
    """
    marker = f"# --- Signal Plugin: {section_name} ---"

    # Check the main supervisord.conf first
    if SUPERVISOR_MAIN_CONF.exists():
        existing = SUPERVISOR_MAIN_CONF.read_text()
        if marker in existing:
            return  # Already configured
        with open(SUPERVISOR_MAIN_CONF, "a") as f:
            f.write(f"\n{marker}\n{config_block}\n")
        return

    # Fallback: write a separate .conf file
    SUPERVISOR_CONF_DIR.mkdir(parents=True, exist_ok=True)
    conf_file = SUPERVISOR_CONF_DIR / f"{section_name}.conf"
    conf_file.write_text(f"{marker}\n{config_block}\n")


def create_supervisor_config(autostart: bool = True):
    """Create a supervisord program config for the signal-cli daemon.

    Args:
        autostart: If True, daemon starts automatically with supervisor.
                   Set to False if you want to start it manually after linking.
    """
    autostart_str = "true" if autostart else "false"
    config = f"""\
[program:signal_cli]
command={SIGNAL_CLI_BIN} --config {SIGNAL_CLI_DATA} daemon --http 127.0.0.1:8080 --receive-mode=on-connection
directory={SIGNAL_CLI_DIR}
autostart={autostart_str}
autorestart=true
startretries=3
startsecs=5
stopwaitsecs=10
stdout_logfile=/var/log/signal-cli.log
stderr_logfile=/var/log/signal-cli-error.log
stdout_logfile_maxbytes=10MB
stderr_logfile_maxbytes=10MB
"""
    _append_to_supervisor_conf("signal_cli", config)

    # Also write to the standalone .conf file for compatibility
    SUPERVISOR_CONF_DIR.mkdir(parents=True, exist_ok=True)
    SUPERVISOR_CONF.write_text(config)

    # Reload supervisord to pick up the new config
    try:
        subprocess.run(["supervisorctl", "reread"], capture_output=True, timeout=10)
        subprocess.run(["supervisorctl", "update"], capture_output=True, timeout=10)
    except Exception:
        pass

    print("[Signal Plugin] Supervisord config created for signal-cli daemon.")


def create_bridge_supervisor_config(autostart: bool = True):
    """Create a supervisord program config for the Signal bridge runner.

    The bridge runner (run_signal_bridge.py) is an independent process that
    polls for incoming Signal messages and routes them through Agent Zero.
    It must run as a separate supervisor service because:
      1. The agent_init extension only fires on WebUI conversation start
      2. The bridge needs to be always-on for persistent Signal chat
      3. It requires the import shadowing fix (sys.modules pre-loading)
      4. It needs --dockerized=true for elevated mode code execution

    Environment variables are passed via the supervisor config. The LLM API
    key (e.g. API_KEY_VENICE) is loaded from /a0/usr/.env by the bridge
    runner itself, so it does not need to be in the supervisor env block.
    Signal-specific env vars ARE set here as defaults; config.json overrides.
    """
    autostart_str = "true" if autostart else "false"
    config = f"""\
[program:signal_bridge]
command=/opt/venv-a0/bin/python /a0/run_signal_bridge.py --dockerized=true
directory=/a0
autostart={autostart_str}
autorestart=true
startretries=20
startsecs=5
stopwaitsecs=10
stdout_logfile=/var/log/signal-bridge.log
stderr_logfile=/var/log/signal-bridge-error.log
stdout_logfile_maxbytes=10MB
stderr_logfile_maxbytes=10MB
"""
    _append_to_supervisor_conf("signal_bridge", config)

    # Reload supervisord
    try:
        subprocess.run(["supervisorctl", "reread"], capture_output=True, timeout=10)
        subprocess.run(["supervisorctl", "update"], capture_output=True, timeout=10)
    except Exception:
        pass

    print("[Signal Plugin] Supervisord config created for signal bridge runner.")


def remove_supervisor_config():
    """Remove the supervisord program config and stop the daemon."""
    stop_daemon()
    if SUPERVISOR_CONF.exists():
        SUPERVISOR_CONF.unlink()
        try:
            subprocess.run(["supervisorctl", "reread"], capture_output=True, timeout=10)
            subprocess.run(["supervisorctl", "update"], capture_output=True, timeout=10)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Daemon lifecycle
# ---------------------------------------------------------------------------

def start_daemon() -> bool:
    """Start the signal-cli daemon via supervisord."""
    if not is_installed():
        print("[Signal Plugin] signal-cli not installed. Run initialize.py first.")
        return False
    if not is_daemon_configured():
        create_supervisor_config()
    try:
        result = subprocess.run(
            ["supervisorctl", "start", "signal_cli"],
            capture_output=True, text=True, timeout=10,
        )
        return "started" in result.stdout.lower() or "already" in result.stdout.lower()
    except Exception as e:
        print(f"[Signal Plugin] Failed to start daemon: {e}")
        return False


def stop_daemon() -> bool:
    """Stop the signal-cli daemon via supervisord."""
    try:
        result = subprocess.run(
            ["supervisorctl", "stop", "signal_cli"],
            capture_output=True, text=True, timeout=10,
        )
        return "stopped" in result.stdout.lower() or "not running" in result.stdout.lower()
    except Exception:
        return False


def restart_daemon() -> bool:
    """Restart the signal-cli daemon."""
    stop_daemon()
    return start_daemon()


def get_bridge_status() -> str:
    """Get bridge runner status from supervisord."""
    try:
        result = subprocess.run(
            ["supervisorctl", "status", "signal_bridge"],
            capture_output=True, text=True, timeout=5,
        )
        parts = result.stdout.strip().split()
        if len(parts) >= 2:
            return parts[1]
        return "UNKNOWN"
    except Exception:
        return "UNKNOWN"


def start_bridge() -> bool:
    """Start the signal bridge runner via supervisord."""
    try:
        result = subprocess.run(
            ["supervisorctl", "start", "signal_bridge"],
            capture_output=True, text=True, timeout=10,
        )
        return "started" in result.stdout.lower() or "already" in result.stdout.lower()
    except Exception as e:
        print(f"[Signal Plugin] Failed to start bridge: {e}")
        return False


def stop_bridge() -> bool:
    """Stop the signal bridge runner via supervisord."""
    try:
        result = subprocess.run(
            ["supervisorctl", "stop", "signal_bridge"],
            capture_output=True, text=True, timeout=10,
        )
        return "stopped" in result.stdout.lower() or "not running" in result.stdout.lower()
    except Exception:
        return False


def get_status() -> dict:
    """Get comprehensive daemon and bridge status."""
    installed = is_installed()
    return {
        "installed": installed,
        "version": SIGNAL_CLI_VERSION if installed else None,
        "daemon_configured": is_daemon_configured(),
        "daemon_status": get_daemon_status() if installed else "NOT_INSTALLED",
        "daemon_healthy": is_daemon_healthy() if installed else False,
        "daemon_url": DAEMON_URL,
        "binary_path": str(SIGNAL_CLI_BIN),
        "data_path": str(SIGNAL_CLI_DATA),
        "bridge_status": get_bridge_status(),
        "bridge_runner": str(BRIDGE_RUNNER),
    }
