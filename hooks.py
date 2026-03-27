"""Plugin lifecycle hooks for the Signal Integration plugin.

Called by Agent Zero's plugin system during install, uninstall, and update.
See: helpers/plugins.py -> call_plugin_hook()
"""
import os
import subprocess
import sys
from pathlib import Path


def _get_plugin_dir() -> Path:
    """Return the directory this hooks.py lives in."""
    return Path(__file__).parent.resolve()


def _get_a0_root() -> Path:
    """Detect A0 root directory."""
    if Path("/a0/plugins").is_dir():
        return Path("/a0")
    if Path("/git/agent-zero/plugins").is_dir():
        return Path("/git/agent-zero")
    return Path("/a0")


def _find_python() -> str:
    """Find the appropriate Python interpreter."""
    candidates = ["/opt/venv-a0/bin/python3", sys.executable, "python3"]
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return "python3"


def install(**kwargs):
    """Post-install hook: set up symlink, data dir, deps, skills, toggle, bridge runner, extension symlink."""
    plugin_dir = _get_plugin_dir()
    a0_root = _get_a0_root()
    plugin_name = "signal"

    print(f"[{plugin_name}] Running post-install hook...")

    # 1. Enable plugin
    toggle = plugin_dir / ".toggle-1"
    if not toggle.exists():
        toggle.touch()
        print(f"[{plugin_name}] Created {toggle}")

    # 2. Create data directory with restrictive permissions
    data_dir = plugin_dir / "data"
    data_dir.mkdir(exist_ok=True)
    os.chmod(str(data_dir), 0o700)

    # 3. Create symlink so 'from plugins.signal.helpers...' imports work
    symlink = a0_root / "plugins" / plugin_name
    if not symlink.exists():
        symlink.symlink_to(plugin_dir)
        print(f"[{plugin_name}] Created symlink: {symlink} -> {plugin_dir}")
    elif symlink.is_symlink() and symlink.resolve() != plugin_dir:
        symlink.unlink()
        symlink.symlink_to(plugin_dir)
        print(f"[{plugin_name}] Updated symlink: {symlink} -> {plugin_dir}")
    elif symlink.is_dir() and not symlink.is_symlink():
        import shutil
        shutil.rmtree(str(symlink))
        symlink.symlink_to(plugin_dir)
        print(f"[{plugin_name}] Replaced directory with symlink: {symlink} -> {plugin_dir}")

    # 4. Copy bridge runner to A0 root (must live at /a0/ to avoid import shadowing)
    bridge_runner = plugin_dir / "run_signal_bridge.py"
    bridge_dest = a0_root / "run_signal_bridge.py"
    if bridge_runner.is_file() and not bridge_dest.exists():
        bridge_dest.write_bytes(bridge_runner.read_bytes())
        bridge_dest.chmod(0o755)
        print(f"[{plugin_name}] Bridge runner installed at: {bridge_dest}")

    # 5. Create extension symlink for agent_init
    # A0 only scans /a0/extensions/python/agent_init/, not plugin subdirectories
    init_ext_src = plugin_dir / "extensions" / "python" / "agent_init" / "_10_signal_chat.py"
    init_ext_dst = a0_root / "extensions" / "python" / "agent_init" / "_10_signal_chat.py"
    if init_ext_src.is_file() and not init_ext_dst.exists():
        init_ext_dst.parent.mkdir(parents=True, exist_ok=True)
        init_ext_dst.symlink_to(init_ext_src)
        print(f"[{plugin_name}] Created extension symlink: {init_ext_dst}")

    # 6. Install skills
    skills_src = plugin_dir / "skills"
    skills_dst = a0_root / "usr" / "skills"
    if skills_src.is_dir():
        for skill_dir in skills_src.iterdir():
            if skill_dir.is_dir():
                target = skills_dst / skill_dir.name
                target.mkdir(parents=True, exist_ok=True)
                for f in skill_dir.iterdir():
                    dest = target / f.name
                    if f.is_file():
                        dest.write_bytes(f.read_bytes())
                print(f"[{plugin_name}] Installed skill: {skill_dir.name}")

    # 7. Install Python dependencies via initialize.py
    init_script = plugin_dir / "initialize.py"
    if init_script.is_file():
        python = _find_python()
        try:
            subprocess.run(
                [python, str(init_script)],
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
            print(f"[{plugin_name}] Dependencies installed")
        except subprocess.CalledProcessError as e:
            print(f"[{plugin_name}] Warning: dependency install failed: {e.stderr[:200]}")
        except subprocess.TimeoutExpired:
            print(f"[{plugin_name}] Warning: dependency install timed out")

    # 8. Mirror to /git/agent-zero if running in /a0 runtime
    if str(a0_root) == "/a0" and Path("/git/agent-zero/usr").is_dir():
        git_plugin = Path("/git/agent-zero/usr/plugins") / plugin_name
        if not git_plugin.exists():
            try:
                import shutil
                shutil.copytree(str(plugin_dir), str(git_plugin))
            except Exception:
                pass

    print(f"[{plugin_name}] Post-install hook complete")


def uninstall(**kwargs):
    """Pre-uninstall hook: clean up symlink, skills, bridge runner, extension symlink."""
    a0_root = _get_a0_root()
    plugin_name = "signal"

    print(f"[{plugin_name}] Running uninstall hook...")

    # Remove symlink
    symlink = a0_root / "plugins" / plugin_name
    if symlink.is_symlink():
        symlink.unlink()
        print(f"[{plugin_name}] Removed symlink: {symlink}")
    elif symlink.is_dir():
        import shutil
        shutil.rmtree(str(symlink))
        print(f"[{plugin_name}] Removed directory: {symlink}")

    # Remove bridge runner
    bridge_dest = a0_root / "run_signal_bridge.py"
    if bridge_dest.is_file():
        bridge_dest.unlink()
        print(f"[{plugin_name}] Removed bridge runner: {bridge_dest}")

    # Remove extension symlink
    init_ext = a0_root / "extensions" / "python" / "agent_init" / "_10_signal_chat.py"
    if init_ext.is_symlink():
        init_ext.unlink()
        print(f"[{plugin_name}] Removed extension symlink: {init_ext}")

    # Remove skills
    skills_dst = a0_root / "usr" / "skills"
    for skill_name in ["signal-chat", "signal-communicate", "signal-secure"]:
        skill_path = skills_dst / skill_name
        if skill_path.is_dir():
            import shutil
            shutil.rmtree(str(skill_path))
            print(f"[{plugin_name}] Removed skill: {skill_name}")

    print(f"[{plugin_name}] Uninstall hook complete")
