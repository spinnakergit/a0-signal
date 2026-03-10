"""Persistent state tracking for Signal message polling."""
import json
import time
from pathlib import Path
from typing import Optional

STATE_FILENAME = "poll_state.json"


def _get_state_path() -> Path:
    candidates = [
        Path(__file__).parent.parent / "data" / STATE_FILENAME,
        Path("/a0/usr/plugins/signal/data") / STATE_FILENAME,
        Path("/a0/plugins/signal/data") / STATE_FILENAME,
        Path("/git/agent-zero/usr/plugins/signal/data") / STATE_FILENAME,
    ]
    for path in candidates:
        if path.exists():
            return path
    path = candidates[0]
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_state() -> dict:
    path = _get_state_path()
    if path.exists():
        with open(path, "r") as f:
            return json.load(f)
    return {"contacts": {}, "alerts": []}


def save_state(state: dict):
    from plugins.signal.helpers.sanitize import secure_write_json
    secure_write_json(_get_state_path(), state)


def get_last_timestamp(contact: str) -> Optional[int]:
    """Get the last seen message timestamp for a contact."""
    state = load_state()
    return state.get("contacts", {}).get(contact, {}).get("last_timestamp")


def set_last_timestamp(contact: str, timestamp: int):
    """Update the last seen message timestamp for a contact."""
    state = load_state()
    contacts = state.setdefault("contacts", {})
    c_state = contacts.setdefault(contact, {})
    c_state["last_timestamp"] = timestamp
    c_state["last_poll"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    save_state(state)


def record_alert(
    contact: str, author: str, content: str, timestamp: int, has_attachment: bool
):
    """Record an alert for a new message."""
    state = load_state()
    alerts = state.setdefault("alerts", [])
    alerts.append({
        "contact": contact,
        "author": author,
        "content": content[:500],
        "timestamp": timestamp,
        "has_attachment": has_attachment,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    # Keep last 100 alerts
    state["alerts"] = alerts[-100:]
    save_state(state)


def add_watch_contact(contact: str, label: str = ""):
    """Add a contact/group to the watch list for polling."""
    state = load_state()
    contacts = state.setdefault("contacts", {})
    c_state = contacts.setdefault(contact, {})
    if label:
        c_state["label"] = label
    save_state(state)


def get_watch_contacts() -> dict:
    state = load_state()
    return state.get("contacts", {})


def remove_watch_contact(contact: str):
    state = load_state()
    state.get("contacts", {}).pop(contact, None)
    save_state(state)
