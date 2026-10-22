import hashlib
import secrets


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def new_session_token() -> str:
    """Opaque bearer token. Only its hash is stored, so a DB leak does not leak live sessions."""
    return secrets.token_urlsafe(32)


def sha256_hex(value: str | bytes) -> str:
    data = value.encode() if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()
