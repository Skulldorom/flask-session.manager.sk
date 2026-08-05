"""Token hashing and verification utilities."""

import hashlib
from datetime import UTC


def create_token_hash(token):
    """Return the SHA-256 hex digest of a token string."""
    return hashlib.sha256(token.encode()).hexdigest()


def token_hint(token, length=8):
    """Return the last `length` characters of a token for display identification."""
    return token[-length:] if len(token) >= length else token


def verify_token_hash(candidate, stored_hash):
    """Return True if the candidate token's hash matches the stored hash."""
    if not candidate or not stored_hash:
        return False
    return create_token_hash(candidate) == stored_hash


def update_token_record(record, token):
    """Atomically update a token record with a new hashed token value.

    Mutates the provided record-like object in place. Does not commit.
    """
    from datetime import datetime

    record.token_hash = create_token_hash(token)
    record.hint = token_hint(token)
    record.last_modified = datetime.now(UTC)


def clear_session_token_value(token_record):
    """Invalidate a session token while preserving its registered-device row."""
    from datetime import datetime

    token_record.token_hash = create_token_hash("")
    token_record.hint = ""
    token_record.token = None
    token_record.last_modified = datetime.now(UTC)
