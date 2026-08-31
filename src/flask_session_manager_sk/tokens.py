"""Token hashing and verification utilities."""

import hashlib
import hmac
from datetime import UTC

_EMPTY_TOKEN_HASH = hashlib.sha256(b"").hexdigest()


def create_token_hash(token):
    """Return the SHA-256 hex digest of a token string."""
    return hashlib.sha256(token.encode()).hexdigest()


def token_hint(token, length=8):
    """Return the last `length` characters of a token for display identification."""
    return token[-length:] if len(token) >= length else token


def verify_token_hash(candidate, stored_hash):
    """Return True if the candidate token's hash matches an active stored hash.

    Missing values fail closed. The historical SHA-256 digest of an empty string
    is treated as revoked compatibility state and never verifies.
    """
    if not candidate or not stored_hash or stored_hash == _EMPTY_TOKEN_HASH:
        return False
    return hmac.compare_digest(create_token_hash(candidate), stored_hash)


def verify_session_token_record(candidate, record):
    """Return True when a presented token matches a record's active token hash."""
    if record is None:
        return False
    return verify_token_hash(candidate, getattr(record, "token_hash", None))


def update_token_record(record, token):
    """Atomically update a token record with a new hashed token value.

    Mutates the provided record-like object in place. Does not commit.
    """
    from datetime import datetime

    record.token_hash = create_token_hash(token)
    record.hint = token_hint(token)
    record.last_modified = datetime.now(UTC)


def clear_session_token_value(token_record):
    """Invalidate a session token while preserving its registered-device row.

    The active token state is represented explicitly as absent instead of as the
    deterministic SHA-256 digest of an empty string. Consumers with non-nullable
    legacy schemas can keep existing rows during migration; verify helpers treat
    both None and the legacy empty-string digest as revoked.
    """
    from datetime import datetime

    token_record.token_hash = None
    token_record.hint = ""
    token_record.token = None
    token_record.last_modified = datetime.now(UTC)
