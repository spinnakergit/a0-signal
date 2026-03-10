"""API endpoint: Get/set Signal plugin configuration.
URL: POST /api/plugins/signal/signal_config_api
"""
import json
import yaml
from pathlib import Path
from helpers.api import ApiHandler, Request, Response


def _get_config_path() -> Path:
    """Find the writable config path for the signal plugin."""
    candidates = [
        Path(__file__).parent.parent / "config.json",
        Path("/a0/usr/plugins/signal/config.json"),
        Path("/a0/plugins/signal/config.json"),
        Path("/git/agent-zero/usr/plugins/signal/config.json"),
    ]
    for p in candidates:
        if p.parent.exists():
            return p
    return candidates[-1]


def _mask_sensitive(value: str) -> str:
    """Mask a sensitive string for display — show first 2 and last 2 chars."""
    if not value:
        return ""
    if len(value) > 6:
        return value[:2] + "*" * 8 + value[-2:]
    return "********"


class SignalConfigApi(ApiHandler):

    @classmethod
    def get_methods(cls) -> list[str]:
        return ["GET", "POST"]

    @classmethod
    def requires_csrf(cls) -> bool:
        return True

    async def process(self, input: dict, request: Request) -> dict | Response:
        action = input.get("action", "get")
        if request.method == "GET" or action == "get":
            return self._get_config()
        elif action == "generate_auth_key":
            return self._generate_auth_key()
        else:
            return self._set_config(input)

    def _generate_auth_key(self) -> dict:
        """Generate a new auth key (does not save — user must click Save)."""
        try:
            from plugins.signal.helpers.sanitize import generate_auth_key
            return {"auth_key": generate_auth_key()}
        except Exception:
            return {"error": "Failed to generate auth key."}

    def _get_config(self) -> dict:
        try:
            config_path = _get_config_path()
            if config_path.exists():
                with open(config_path, "r") as f:
                    config = json.load(f)
            else:
                default_path = config_path.parent / "default_config.yaml"
                if default_path.exists():
                    with open(default_path, "r") as f:
                        config = yaml.safe_load(f) or {}
                else:
                    config = {}

            # Mask sensitive values for display
            masked = json.loads(json.dumps(config))

            # Mask phone number (show last 4 digits)
            phone = masked.get("phone_number", "")
            if phone and len(phone) > 4:
                masked["phone_number"] = "*" * (len(phone) - 4) + phone[-4:]

            # Mask auth token
            api_config = masked.get("api", {})
            if api_config.get("auth_token"):
                api_config["auth_token"] = _mask_sensitive(api_config["auth_token"])

            return masked
        except Exception:
            return {"error": "Failed to read configuration."}

    def _set_config(self, input: dict) -> dict:
        try:
            config = input.get("config", input)
            if not config or config == {"action": "set"}:
                return {"error": "No config provided"}

            config.pop("action", None)

            config_path = _get_config_path()
            config_path.parent.mkdir(parents=True, exist_ok=True)

            # Merge with existing config (preserve masked values)
            existing = {}
            if config_path.exists():
                with open(config_path, "r") as f:
                    existing = json.load(f)

            # Preserve phone number if masked
            new_phone = config.get("phone_number", "")
            if new_phone and "*" * 4 in new_phone:
                config["phone_number"] = existing.get("phone_number", "")

            # Preserve auth token if masked
            new_token = config.get("api", {}).get("auth_token", "")
            if new_token and "*" * 4 in new_token:
                config.setdefault("api", {})["auth_token"] = (
                    existing.get("api", {}).get("auth_token", "")
                )

            # Preserve existing auth_key if not provided
            new_auth_key = config.get("chat_bridge", {}).get("auth_key", "")
            existing_auth_key = existing.get("chat_bridge", {}).get("auth_key", "")
            if not new_auth_key and existing_auth_key:
                config.setdefault("chat_bridge", {})["auth_key"] = existing_auth_key

            from plugins.signal.helpers.sanitize import secure_write_json
            secure_write_json(config_path, config)

            return {"ok": True}
        except Exception:
            return {"error": "Failed to save configuration."}
