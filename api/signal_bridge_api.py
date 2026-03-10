"""API endpoint: Signal chat bridge start/stop/status.
URL: POST /api/plugins/signal/signal_bridge_api
"""
from helpers.api import ApiHandler, Request, Response


class SignalBridgeApi(ApiHandler):

    @classmethod
    def get_methods(cls) -> list[str]:
        return ["POST"]

    @classmethod
    def requires_csrf(cls) -> bool:
        return True

    async def process(self, input: dict, request: Request) -> dict | Response:
        action = input.get("action", "status")

        try:
            if action == "status":
                return self._status()
            elif action == "start":
                return await self._start()
            elif action == "stop":
                return await self._stop()
            else:
                return {"ok": False, "error": f"Unknown action: {action}"}
        except Exception as e:
            return {"ok": False, "error": f"Bridge error: {type(e).__name__}: {e}"}

    def _status(self) -> dict:
        from plugins.signal.helpers.signal_bridge import get_bridge_status
        status = get_bridge_status()
        return {"ok": True, **status}

    async def _start(self) -> dict:
        from plugins.signal.helpers.signal_bridge import get_bridge_status, start_chat_bridge
        from plugins.signal.helpers.signal_client import get_signal_config
        from plugins.signal.helpers.sanitize import require_auth

        status = get_bridge_status()
        if status.get("running"):
            return {"ok": True, "message": "Bridge is already running", **status}

        config = get_signal_config()
        try:
            require_auth(config)
        except ValueError as e:
            return {"ok": False, "error": str(e)}

        await start_chat_bridge()
        return {"ok": True, "message": "Bridge started", **get_bridge_status()}

    async def _stop(self) -> dict:
        from plugins.signal.helpers.signal_bridge import get_bridge_status, stop_chat_bridge

        await stop_chat_bridge()
        return {"ok": True, "message": "Bridge stopped", **get_bridge_status()}
