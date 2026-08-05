"""Tests for token hashing utilities."""

from datetime import datetime

from flask_session_manager_sk.tokens import (
    clear_session_token_value,
    create_token_hash,
    token_hint,
    update_token_record,
    verify_token_hash,
)


class FakeToken:
    token_hash = "initial-hash"
    hint = "initial-hint"
    token = "initial-token"
    last_modified: datetime | None = None


# ---------------------------------------------------------------------------
# create_token_hash
# ---------------------------------------------------------------------------
def test_create_token_hash_is_stable():
    assert create_token_hash("abc") == create_token_hash("abc")


def test_create_token_hash_different_inputs():
    assert create_token_hash("abc") != create_token_hash("xyz")


def test_create_token_hash_returns_hex():
    result = create_token_hash("test")
    assert len(result) == 64
    assert all(c in "0123456789abcdef" for c in result)


# ---------------------------------------------------------------------------
# token_hint
# ---------------------------------------------------------------------------
def test_token_hint_returns_last_n_chars():
    assert token_hint("1234567890", 4) == "7890"


def test_token_hint_shorter_than_length():
    assert token_hint("ab", 8) == "ab"


def test_token_hint_default_length():
    assert token_hint("1234567890") == "34567890"


# ---------------------------------------------------------------------------
# verify_token_hash
# ---------------------------------------------------------------------------
def test_verify_matching_hash():
    h = create_token_hash("secret")
    assert verify_token_hash("secret", h) is True


def test_verify_mismatched_hash():
    h = create_token_hash("secret")
    assert verify_token_hash("wrong", h) is False


def test_verify_none_inputs():
    assert verify_token_hash(None, "hash") is False
    assert verify_token_hash("candidate", None) is False
    assert verify_token_hash(None, None) is False


# ---------------------------------------------------------------------------
# update_token_record
# ---------------------------------------------------------------------------
def test_update_token_record():
    record = FakeToken()
    update_token_record(record, "new-token")

    assert record.token_hash == create_token_hash("new-token")
    assert record.hint == token_hint("new-token")
    assert isinstance(record.last_modified, datetime)


def test_update_token_record_changes_existing():
    record = FakeToken()
    update_token_record(record, "first")
    first_hash = record.token_hash
    update_token_record(record, "second")
    assert record.token_hash != first_hash
    assert record.token_hash == create_token_hash("second")


# ---------------------------------------------------------------------------
# clear_session_token_value
# ---------------------------------------------------------------------------
def test_clear_session_token_value():
    record = FakeToken()
    clear_session_token_value(record)

    assert record.token_hash == create_token_hash("")
    assert record.hint == ""
    assert record.token is None
    assert isinstance(record.last_modified, datetime)
