from __future__ import annotations

import hashlib
import hmac
import secrets

import bcrypt

# bcrypt silently ignores anything past 72 bytes; truncate explicitly so a long
# password can't be confused with its own prefix.
_BCRYPT_MAX_BYTES = 72


def _prepare(password: str) -> bytes:
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def get_password_hash(password: str) -> str:
    return bcrypt.hashpw(_prepare(password), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(_prepare(password), password_hash.encode("ascii"))
    except (ValueError, TypeError):
        # Malformed or legacy hash - treat as a failed login, never a crash.
        return False


def generate_token() -> str:
    """URL-safe secret handed to the user in an email link."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """What we persist. The raw token only ever lives in the email."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


def password_problems(password: str) -> list[str]:
    """Minimum bar for a new password. Returns human-readable problems."""
    problems: list[str] = []
    if len(password) < 8:
        problems.append("Password must be at least 8 characters.")
    if password.isdigit():
        problems.append("Password can't be only numbers.")
    if password.lower() in {"password", "12345678", "applylist", "qwertyui"}:
        problems.append("That password is too common.")
    return problems
