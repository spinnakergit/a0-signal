"""API endpoint: Test Signal API connection.
URL: POST /api/plugins/signal/signal_test
"""
from helpers.api import ApiHandler, Request, Response


class SignalTest(ApiHandler):

    @classmethod
    def get_methods(cls) -> list[str]:
        return ["GET", "POST"]

    @classmethod
    def requires_csrf(cls) -> bool:
        return True

    async def process(self, input: dict, request: Request) -> dict | Response:
        try:
            # Self-heal: ensure symlink exists for plugin namespace imports
            from pathlib import Path
            plugin_dir = Path(__file__).resolve().parent.parent
            for root in [Path("/a0"), Path("/git/agent-zero")]:
                plugins_dir = root / "plugins"
                if plugins_dir.is_dir():
                    symlink = plugins_dir / "signal"
                    if not symlink.exists():
                        symlink.symlink_to(plugin_dir)
                    break

            from plugins.signal.helpers.signal_client import (
                create_signal_client,
                get_signal_config,
            )
            from plugins.signal.helpers.sanitize import require_auth

            config = get_signal_config()
            mode = config.get("api", {}).get("mode", "integrated")

            # In integrated mode, check daemon status first
            if mode == "integrated":
                try:
                    from plugins.signal.helpers.signal_daemon import get_status
                    daemon = get_status()
                    if not daemon["installed"]:
                        return {
                            "ok": False,
                            "mode": mode,
                            "error": (
                                "signal-cli not installed. Run: "
                                "python initialize.py --integrated"
                            ),
                            "daemon": daemon,
                        }
                    if not daemon["daemon_healthy"]:
                        return {
                            "ok": False,
                            "mode": mode,
                            "error": (
                                "signal-cli daemon not running. Start with: "
                                "supervisorctl start signal_cli"
                            ),
                            "daemon": daemon,
                        }
                except ImportError:
                    pass

            try:
                require_auth(config)
            except ValueError as e:
                return {"ok": False, "mode": mode, "error": str(e)}

            client = create_signal_client(config)

            # Test connection by getting API version info
            about = await client.get_about()
            await client.close()

            version = "unknown"
            if isinstance(about, dict):
                version = about.get("version", about.get("versions", "unknown"))

            return {
                "ok": True,
                "mode": mode,
                "phone_number": config.get("phone_number", ""),
                "api_version": str(version),
                "api_url": config.get("api", {}).get("base_url", "")
                    or ("http://127.0.0.1:8080" if mode == "integrated" else ""),
            }
        except Exception as e:
            return {"ok": False, "error": f"Connection failed: {type(e).__name__}: {e}"}
