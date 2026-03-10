"""Prompt injection defense and input validation for the Signal plugin.

Sanitizes all untrusted external content (messages, usernames, group names)
before it reaches the LLM agent context. Adapted from the Discord plugin's
proven sanitization layer with Signal-specific additions.
"""

import os
import re
import unicodedata

# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------
MAX_MESSAGE_CONTENT = 4000
MAX_USERNAME = 100
MAX_GROUP_NAME = 100
MAX_FILENAME = 255
MAX_BULK_INPUT_CHARS = 200_000
MAX_MESSAGE_LIMIT = 500

# ---------------------------------------------------------------------------
# Zero-width and invisible characters to strip
# ---------------------------------------------------------------------------
_INVISIBLE_CHARS = re.compile(
    "["
    "\u200b"  # zero-width space
    "\u200c"  # zero-width non-joiner
    "\u200d"  # zero-width joiner
    "\u200e"  # left-to-right mark
    "\u200f"  # right-to-left mark
    "\u2060"  # word joiner
    "\u2061"  # function application
    "\u2062"  # invisible times
    "\u2063"  # invisible separator
    "\u2064"  # invisible plus
    "\ufeff"  # zero-width no-break space / BOM
    "\u00ad"  # soft hyphen
    "\u034f"  # combining grapheme joiner
    "\u061c"  # arabic letter mark
    "\u115f"  # hangul choseong filler
    "\u1160"  # hangul jungseong filler
    "\u17b4"  # khmer vowel inherent aq
    "\u17b5"  # khmer vowel inherent aa
    "\u180e"  # mongolian vowel separator
    "\u2028"  # line separator
    "\u2029"  # paragraph separator
    "\u202a"  # left-to-right embedding
    "\u202b"  # right-to-left embedding
    "\u202c"  # pop directional formatting
    "\u202d"  # left-to-right override
    "\u202e"  # right-to-left override
    "\u202f"  # narrow no-break space
    "\ufff9"  # interlinear annotation anchor
    "\ufffa"  # interlinear annotation separator
    "\ufffb"  # interlinear annotation terminator
    "]+"
)

# ---------------------------------------------------------------------------
# Injection patterns (compiled once at module load)
# ---------------------------------------------------------------------------
_INJECTION_PHRASES = [
    # Classic instruction override
    r"ignore all previous instructions",
    r"ignore prior instructions",
    r"ignore above instructions",
    r"ignore the above",
    r"disregard all previous",
    r"disregard prior instructions",
    r"forget all previous",
    r"forget your instructions",
    # Role hijacking
    r"you are now",
    r"you must now",
    r"you will now",
    r"you should now",
    r"from now on",
    r"pretend you are",
    r"act as if",
    r"roleplay as",
    # Instruction injection
    r"new instructions:",
    r"override:",
    r"system:",
    r"SYSTEM:",
    r"reminder:",
    r"important:",
    r"attention:",
    r"actually,? (?:the user|i) (?:want|meant|need)",
    # Model-specific tokens
    r"\[INST\]",
    r"\[/INST\]",
    r"<\|im_start\|>",
    r"<\|im_end\|>",
    r"<<SYS>>",
    r"<</SYS>>",
    r"</s>",
    r"<\|endoftext\|>",
    r"<\|system\|>",
    r"<\|user\|>",
    r"<\|assistant\|>",
    # Chat role markers
    r"Human:",
    r"Assistant:",
    r"### Instruction",
    r"### System",
    r"## System",
    # Meta-manipulation
    r"the (?:previous|above|preceding) instructions (?:are|were)",
    r"do not follow (?:the|your) (?:previous|original)",
]

_INJECTION_RE = re.compile(
    r"^\s*(?:" + "|".join(_INJECTION_PHRASES) + r")",
    re.IGNORECASE | re.MULTILINE,
)

# Delimiter tags — must be escaped inside user data
_DELIMITER_TAGS = [
    "<signal_user_content>",
    "</signal_user_content>",
    "<signal_messages>",
    "</signal_messages>",
]

_DELIMITER_RE = re.compile(
    "|".join(re.escape(tag) for tag in _DELIMITER_TAGS),
    re.IGNORECASE,
)

# E.164 phone number pattern
_PHONE_RE = re.compile(r"^\+[1-9]\d{1,14}$")

# Signal group ID pattern (base64-encoded, typically 24-44 chars)
_GROUP_ID_RE = re.compile(r"^[A-Za-z0-9+/=]{16,64}$")


# ---------------------------------------------------------------------------
# Text normalization (Unicode defense)
# ---------------------------------------------------------------------------

def _normalize_text(text: str) -> str:
    """Normalize Unicode to defeat homoglyph and invisible-char attacks."""
    text = unicodedata.normalize("NFKC", text)
    text = _INVISIBLE_CHARS.sub("", text)
    return text


# ---------------------------------------------------------------------------
# Sanitization functions
# ---------------------------------------------------------------------------

def sanitize_content(text: str, max_length: int = MAX_MESSAGE_CONTENT) -> str:
    """Sanitize a Signal message body for safe LLM consumption.

    - Normalizes Unicode (homoglyph / invisible char defense)
    - Neutralises known injection patterns
    - Escapes our own delimiter tags so they can't be spoofed
    - Truncates to *max_length* AFTER sanitization (prevents boundary attacks)
    """
    if not text:
        return ""
    text = _normalize_text(text)
    text = _DELIMITER_RE.sub(_escape_tag, text)
    text = _INJECTION_RE.sub("[blocked: suspected prompt injection]", text)
    text = text[:max_length]
    return text


def sanitize_username(name: str, max_length: int = MAX_USERNAME) -> str:
    """Sanitize a Signal contact name / profile name."""
    if not name:
        return "Unknown"
    name = _normalize_text(name)
    name = name[:max_length]
    name = name.replace("\n", " ").replace("\r", " ")
    name = _DELIMITER_RE.sub(_escape_tag, name)
    name = _INJECTION_RE.sub("[blocked]", name)
    return name


def sanitize_group_name(name: str, max_length: int = MAX_GROUP_NAME) -> str:
    """Sanitize a Signal group name."""
    if not name:
        return "unknown-group"
    name = _normalize_text(name)
    name = name[:max_length]
    name = name.replace("\n", " ").replace("\r", " ")
    name = _DELIMITER_RE.sub(_escape_tag, name)
    name = _INJECTION_RE.sub("[blocked]", name)
    return name


def sanitize_filename(name: str, max_length: int = MAX_FILENAME) -> str:
    """Sanitize an attachment filename."""
    if not name:
        return "file"
    name = name[:max_length]
    name = name.replace("/", "_").replace("\\", "_").replace("..", "_")
    name = name.replace("\n", "").replace("\r", "")
    return name


def truncate_bulk(text: str, max_length: int = MAX_BULK_INPUT_CHARS) -> str:
    """Truncate large message batches."""
    if len(text) <= max_length:
        return text
    suffix = "\n[... truncated for safety ...]"
    return text[: max_length - len(suffix)] + suffix


def clamp_limit(limit: int, default: int = 100, maximum: int = MAX_MESSAGE_LIMIT) -> int:
    """Clamp a user-provided message limit to a safe range."""
    if limit < 1:
        return default
    return min(limit, maximum)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def validate_phone_number(value: str, name: str = "phone number") -> str:
    """Validate an E.164 phone number format.

    Returns the validated string or raises ValueError.
    """
    if not value:
        raise ValueError(f"{name} is required.")
    value = value.strip()
    if not _PHONE_RE.match(value):
        raise ValueError(
            f"Invalid {name}: must be in E.164 format (e.g. +1234567890)."
        )
    return value


def validate_group_id(value: str, name: str = "group ID") -> str:
    """Validate a Signal group ID (base64-encoded string).

    Returns the validated string or raises ValueError.
    """
    if not value:
        raise ValueError(f"{name} is required.")
    value = value.strip()
    if not _GROUP_ID_RE.match(value):
        raise ValueError(
            f"Invalid {name}: expected a base64-encoded group identifier."
        )
    return value


def validate_recipient(value: str, name: str = "recipient") -> str:
    """Validate a recipient — either a phone number or group ID.

    Returns the validated string or raises ValueError.
    """
    if not value:
        raise ValueError(f"{name} is required.")
    value = value.strip()
    # Phone number
    if value.startswith("+"):
        return validate_phone_number(value, name)
    # Group ID (base64)
    return validate_group_id(value, name)


# ---------------------------------------------------------------------------
# Auth key generation
# ---------------------------------------------------------------------------

def generate_auth_key(length: int = 32) -> str:
    """Generate a cryptographically secure URL-safe auth key."""
    import secrets
    return secrets.token_urlsafe(length)


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------

def require_auth(config: dict) -> None:
    """Raise ValueError if the Signal API is not configured."""
    phone = (config.get("phone_number", "") or "").strip()
    base_url = (config.get("api", {}).get("base_url", "") or "").strip()
    if not phone:
        raise ValueError(
            "Signal phone number not configured. Set SIGNAL_PHONE_NUMBER env var "
            "or configure in the Signal plugin settings."
        )
    if not base_url:
        raise ValueError(
            "Signal API URL not configured. Set SIGNAL_API_URL env var "
            "or configure in the Signal plugin settings."
        )


# ---------------------------------------------------------------------------
# Contact allowlist check
# ---------------------------------------------------------------------------

def is_contact_allowed(contact: str, config: dict) -> bool:
    """Check if a contact (phone number or group ID) is in the allowed list.

    Returns True if the contact is allowed or if the allowlist is empty.
    """
    allowed = config.get("allowed_contacts", [])
    if not allowed:
        return True
    return contact in allowed


# ---------------------------------------------------------------------------
# Secure file write helper
# ---------------------------------------------------------------------------

def secure_write_json(path, data, indent: int = 2):
    """Write JSON to a file with restrictive permissions (0o600) and atomic rename."""
    import json
    from pathlib import Path
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    try:
        fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=indent)
        os.replace(str(tmp_path), str(path))
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        with open(path, "w") as f:
            json.dump(data, f, indent=indent)
        try:
            os.chmod(str(path), 0o600)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _escape_tag(match: re.Match) -> str:
    """Replace angle brackets in a matched delimiter tag so it's inert."""
    return match.group(0).replace("<", "&lt;").replace(">", "&gt;")
