"""One-time setup script for the Signal plugin.
Installs required Python dependencies and optionally installs
signal-cli natively for integrated mode.

Called by the Init button in Agent Zero's Plugin List UI.
Must define main() returning 0 on success, non-zero on failure.

Usage:
  python initialize.py              # Python deps only (external mode)
  python initialize.py --integrated # Python deps + signal-cli native binary
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path


def _find_python():
    """Find the correct Python interpreter (prefer A0 venv)."""
    venv_python = Path("/opt/venv-a0/bin/python")
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def _install(pip_name: str, python: str):
    """Install a package using uv (preferred) or pip as fallback."""
    uv = shutil.which("uv")
    if uv:
        subprocess.check_call([uv, "pip", "install", pip_name, "--python", python])
    else:
        subprocess.check_call([python, "-m", "pip", "install", pip_name])


def _install_python_deps() -> list:
    """Install Python dependencies. Returns list of failed packages."""
    python = _find_python()
    deps = {
        "httpx": "httpx>=0.27,<1",
        "yaml": "pyyaml>=6.0,<7",
    }
    failed = []
    for import_name, pip_name in deps.items():
        try:
            result = subprocess.run(
                [python, "-c", f"import {import_name}"],
                capture_output=True,
            )
            if result.returncode == 0:
                print(f"[Signal Plugin] {pip_name} already installed.")
                continue
        except Exception:
            pass
        print(f"[Signal Plugin] Installing {pip_name}...")
        try:
            _install(pip_name, python)
        except subprocess.CalledProcessError as e:
            print(f"ERROR: Failed to install {pip_name}: {e}")
            failed.append(pip_name)
    return failed


def _install_signal_cli() -> bool:
    """Install signal-cli native binary for integrated mode.

    Downloads the GraalVM native image (~92MB) — no JVM required.
    Creates supervisord configs for both the daemon and the bridge runner.
    """
    # Import here to avoid requiring httpx before it's installed
    try:
        from plugins.signal.helpers.signal_daemon import (
            is_installed,
            install_signal_cli,
            create_supervisor_config,
            create_bridge_supervisor_config,
        )
    except ImportError:
        # If running outside A0, try relative import
        sys.path.insert(0, str(Path(__file__).parent))
        from helpers.signal_daemon import (
            is_installed,
            install_signal_cli,
            create_supervisor_config,
            create_bridge_supervisor_config,
        )

    if is_installed():
        print("[Signal Plugin] signal-cli native binary already installed.")
        # Daemon autostart=false until phone number is linked
        create_supervisor_config(autostart=False)
        create_bridge_supervisor_config(autostart=False)
        return True

    success = install_signal_cli()
    if success:
        # Daemon autostart=false — user must link a phone number first
        create_supervisor_config(autostart=False)
        # Bridge autostart=false — enable after config is complete
        create_bridge_supervisor_config(autostart=False)
        print("[Signal Plugin] signal-cli integrated mode ready.")
        print("[Signal Plugin] Next: link a phone number, then start services.")
        print("[Signal Plugin]   supervisorctl start signal_cli")
        print("[Signal Plugin]   supervisorctl start signal_bridge")
    return success


def _install_bridge_runner() -> bool:
    """Copy the standalone bridge runner to /a0/run_signal_bridge.py.

    The bridge runner must live at /a0/ root (not inside the plugin directory)
    to avoid Python import shadowing — plugins/signal/helpers/ would shadow
    A0's core /a0/helpers/ package if the script ran from within the plugin.
    """
    src = Path(__file__).parent / "run_signal_bridge.py"
    dst = Path("/a0/run_signal_bridge.py")

    if not src.exists():
        print("[Signal Plugin] WARNING: run_signal_bridge.py not found in plugin source.")
        return False

    try:
        shutil.copy2(str(src), str(dst))
        os.chmod(dst, 0o755)
        print(f"[Signal Plugin] Bridge runner installed at {dst}")
        return True
    except Exception as e:
        print(f"[Signal Plugin] WARNING: Could not install bridge runner: {e}")
        return False


def main():
    integrated = "--integrated" in sys.argv

    # Step 1: Python dependencies (always needed)
    failed = _install_python_deps()
    if failed:
        print(f"[Signal Plugin] Failed to install: {', '.join(failed)}")
        return 1
    # Ensure symlink exists for plugin namespace imports
    plugin_dir = Path(__file__).resolve().parent
    for root in [Path("/a0"), Path("/git/agent-zero")]:
        plugins_dir = root / "plugins"
        if plugins_dir.is_dir():
            symlink = plugins_dir / "signal"
            if not symlink.exists():
                try:
                    symlink.symlink_to(plugin_dir)
                    print(f"[Signal Plugin] Created symlink: {symlink} -> {plugin_dir}")
                except OSError as e:
                    print(f"[Signal Plugin] WARNING: Could not create symlink: {e}")
            break

    print("[Signal Plugin] All Python dependencies ready.")

    # Step 2: signal-cli native binary (only for integrated mode)
    if integrated:
        print()
        print("[Signal Plugin] Installing signal-cli for integrated mode...")
        if not _install_signal_cli():
            print("[Signal Plugin] signal-cli installation failed.")
            print("[Signal Plugin] You can still use external mode with a separate")
            print("[Signal Plugin] signal-cli-rest-api Docker container.")
            return 1

        # Step 3: Install the standalone bridge runner
        _install_bridge_runner()

        print("[Signal Plugin] Integrated mode setup complete.")
    else:
        print("[Signal Plugin] Tip: Run with --integrated to install signal-cli")
        print("[Signal Plugin] natively (no separate Docker container needed).")

    return 0


if __name__ == "__main__":
    sys.exit(main())
