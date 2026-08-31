"""Tests for the SessionManager extension with callback wiring."""

from datetime import timedelta

import pytest
from flask import Flask, jsonify
from flask_jwt_extended import create_access_token, jwt_required

from flask_session_manager_sk import SessionManager, SessionManagerCallbacks
from flask_session_manager_sk.tokens import (
    clear_session_token_value,
    create_token_hash,
    token_hint,
    verify_session_token_record,
)

UA = "Mozilla/5.0 (X11; Linux x86_64) Chrome/130.0.0.0"
AGENT = "Linux Chrome"


class TokenRecord:
    def __init__(self, raw_token, agent, device_uid, persistent=False):
        self.agent = agent
        self.device_uid = device_uid
        self.token_hash = create_token_hash(raw_token)
        self.hint = token_hint(raw_token)
        self.token = None
        self.persistent = persistent


class FakeUser:
    def __init__(self, user_id, active=True):
        self.id = user_id
        self.active = active
        self.tokens = []

    def check_token(self, agent, device_uid, token_str):
        for record in self.tokens:
            if record.agent == agent and record.device_uid == device_uid:
                if verify_session_token_record(token_str, record):
                    return record
        return None

    def add_token(self, raw_token, agent, device_uid, persistent=False):
        record = TokenRecord(raw_token, agent, device_uid, persistent=persistent)
        self.tokens.append(record)
        return record

    def persistent_for(self, agent, device_uid, token_record=None):
        if token_record is not None:
            return bool(token_record.persistent)
        return any(
            record.agent == agent and record.device_uid == device_uid and record.persistent
            for record in self.tokens
        )


def _build_app_and_token(
    extra_config=None,
    callbacks_overrides=None,
    active=True,
    persistent=False,
):
    """Build an app with SessionManager, return (app, token, dev_uid, user)."""
    app = Flask(__name__)
    app.config.update(
        {
            "SECRET_KEY": "a-secret-key-long-enough-for-hmac-sha256-signing",
            "JWT_TOKEN_LOCATION": ["cookies"],
            "JWT_ACCESS_COOKIE_NAME": "access_token",
            "JWT_COOKIE_CSRF_PROTECT": True,
            "JWT_COOKIE_SECURE": False,
            "FRONTEND_URL": "http://localhost:3000",
            "CORS_ORIGINS": ["http://localhost:3000"],
        }
    )
    if extra_config:
        app.config.update(extra_config)

    user = FakeUser("user-1", active=active)

    def make_token(u, agent, dev):
        with app.app_context():
            tok = create_access_token(identity=str(u.id))
        u.add_token(tok, agent, dev, persistent=u.persistent_for(agent, dev))
        return tok

    cb = {
        "user_lookup": lambda i: user if i == user.id else None,
        "refresh_user_token": lambda u, agent, dev: make_token(u, agent, dev),
        "verify_user_token": lambda u, agent, dev, tok: u.check_token(agent, dev, tok),
        "is_user_active": lambda u: u.active,
        "is_session_persistent": lambda u, agent, dev, record=None: u.persistent_for(
            agent, dev, record
        ),
    }
    if callbacks_overrides:
        cb.update(callbacks_overrides)

    _manager = SessionManager(app, callbacks=SessionManagerCallbacks(**cb))

    @app.route("/protected")
    @jwt_required()
    def protected():
        return jsonify(status="ok")

    @app.route("/mutate", methods=["POST"])
    def mutate():
        return jsonify(status="mutated")

    dev_uid = "dev-test"
    with app.app_context():
        raw = create_access_token(identity="user-1")
    user.add_token(raw, AGENT, dev_uid, persistent=persistent)

    return app, raw, dev_uid, user


def _make_client_with_token(app, token):
    """Create test client with auth cookies already set."""
    client = app.test_client()
    client.set_cookie("access_token", token)
    client.set_cookie("csrf_access_token", "fake-csrf")
    return client


def _auth_headers(dev_uid, csrf="fake-csrf", origin=None):
    headers = {
        "X-CSRF-TOKEN": csrf,
        "deviceUID": dev_uid,
        "User-Agent": UA,
    }
    if origin:
        headers["Origin"] = origin
    return headers


# ---------------------------------------------------------------------------
# SessionManager init patterns
# ---------------------------------------------------------------------------
def test_session_manager_direct_init():
    """SessionManager can be initialized with app + callbacks in one call."""
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "enough-bytes-for-hmac-sha256"
    app.config["JWT_TOKEN_LOCATION"] = ["cookies"]
    app.config["JWT_COOKIE_CSRF_PROTECT"] = False
    app.config["FSM_CSRF_ORIGIN_CHECK"] = False

    user = FakeUser("u1")
    manager = SessionManager(
        app,
        callbacks=SessionManagerCallbacks(
            user_lookup=lambda i: user,
            refresh_user_token=lambda u, a, d: "stub",
        ),
    )
    with app.app_context():
        tok = create_access_token(identity=str(user.id))
    user.add_token(tok, "agent", "dev")
    assert manager.jwt is not None


def test_cookie_csrf_origin_guard_is_registered_by_default():
    app, raw_token, dev_uid, _ = _build_app_and_token()
    client = _make_client_with_token(app, raw_token)

    resp = client.post(
        "/mutate",
        headers=_auth_headers(dev_uid, origin="http://attacker.example"),
    )

    assert resp.status_code == 403
    assert resp.get_json()["message"] == "CSRF origin check failed"


def test_cookie_csrf_origin_guard_allows_configured_origin():
    app, raw_token, dev_uid, _ = _build_app_and_token()
    client = _make_client_with_token(app, raw_token)

    resp = client.post(
        "/mutate",
        headers=_auth_headers(dev_uid, origin="http://localhost:3000"),
    )

    assert resp.status_code == 200
    assert resp.get_json() == {"status": "mutated"}


def test_cookie_csrf_origin_guard_fails_closed_when_origin_missing():
    app, raw_token, dev_uid, _ = _build_app_and_token()
    client = _make_client_with_token(app, raw_token)

    resp = client.post("/mutate", headers=_auth_headers(dev_uid))

    assert resp.status_code == 403


def test_cookie_csrf_origin_guard_skips_bearer_auth():
    app, raw_token, dev_uid, _ = _build_app_and_token(
        extra_config={"JWT_TOKEN_LOCATION": ["cookies", "headers"]}
    )
    client = _make_client_with_token(app, raw_token)

    resp = client.post(
        "/mutate",
        headers={
            **_auth_headers(dev_uid, origin="http://attacker.example"),
            "Authorization": f"Bearer {raw_token}",
        },
    )

    assert resp.status_code == 200


def test_cookie_csrf_origin_guard_can_be_explicitly_disabled():
    app, raw_token, dev_uid, _ = _build_app_and_token(
        extra_config={"FSM_CSRF_ORIGIN_CHECK": False}
    )
    client = _make_client_with_token(app, raw_token)

    resp = client.post(
        "/mutate",
        headers=_auth_headers(dev_uid, origin="http://attacker.example"),
    )

    assert resp.status_code == 200


def test_cookie_csrf_origin_guard_requires_configured_origins():
    with pytest.raises(RuntimeError, match="FRONTEND_URL or CORS_ORIGINS"):
        _build_app_and_token(extra_config={"FRONTEND_URL": None, "CORS_ORIGINS": []})


def test_samesite_none_requires_secure_for_cookie_auth():
    with pytest.raises(RuntimeError, match="JWT_COOKIE_SECURE=True"):
        _build_app_and_token(
            extra_config={"JWT_COOKIE_SAMESITE": "None", "JWT_COOKIE_SECURE": False}
        )


# ---------------------------------------------------------------------------
# Login → protected route flow
# ---------------------------------------------------------------------------
def test_pre_registered_token_accesses_protected_route():
    app, raw_token, dev_uid, _ = _build_app_and_token()
    client = _make_client_with_token(app, raw_token)

    resp = client.get("/protected", headers=_auth_headers(dev_uid))
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Invalid token
# ---------------------------------------------------------------------------
def test_invalid_token_returns_error():
    app, _, _, _ = _build_app_and_token()
    client = app.test_client()
    client.set_cookie("access_token", "not-a-real-jwt")

    resp = client.get("/protected")
    assert resp.status_code != 200
    data = resp.get_json()
    assert data is not None


def test_registered_device_with_wrong_token_is_rejected():
    app, _raw_token, dev_uid, _ = _build_app_and_token()
    with app.app_context():
        wrong_token = create_access_token(identity="user-1")
    client = _make_client_with_token(app, wrong_token)

    resp = client.get("/protected", headers=_auth_headers(dev_uid))

    assert resp.status_code == 455
    assert resp.get_json()["msg"] == "Invalid Token"


def test_revoked_token_record_is_rejected():
    app, raw_token, dev_uid, user = _build_app_and_token()
    clear_session_token_value(user.tokens[0])
    client = _make_client_with_token(app, raw_token)

    resp = client.get("/protected", headers=_auth_headers(dev_uid))

    assert resp.status_code == 455


# ---------------------------------------------------------------------------
# Expired token → refresh
# ---------------------------------------------------------------------------
def test_expired_token_refreshes_with_cookie_without_exposing_token():
    app, raw_token, dev_uid, _ = _build_app_and_token(
        extra_config={"JWT_ACCESS_TOKEN_EXPIRES": 1}
    )
    client = _make_client_with_token(app, raw_token)

    import time

    time.sleep(1.5)

    resp = client.get("/protected", headers=_auth_headers(dev_uid))

    assert resp.status_code == 200
    assert resp.get_json() == {"refreshed": True}
    assert "access_token" not in resp.get_json()
    cookie_str = "; ".join(resp.headers.getlist("Set-Cookie"))
    assert "access_token=" in cookie_str
    assert "csrf_access_token=" in cookie_str

    for cookie_header in resp.headers.getlist("Set-Cookie"):
        name, val = cookie_header.split(";")[0].split("=", 1)
        client.set_cookie(name, val)

    follow_up = client.get("/protected", headers=_auth_headers(dev_uid))
    assert follow_up.status_code == 200


def test_expired_persistent_session_refresh_preserves_max_age():
    app, raw_token, dev_uid, _ = _build_app_and_token(
        extra_config={
            "JWT_ACCESS_TOKEN_EXPIRES": 1,
            "FSM_PERSISTENT_MAX_AGE": timedelta(days=30),
        },
        persistent=True,
    )
    client = _make_client_with_token(app, raw_token)

    import time

    time.sleep(1.5)

    resp = client.get("/protected", headers=_auth_headers(dev_uid))

    assert resp.status_code == 200
    for cookie in resp.headers.getlist("Set-Cookie"):
        assert "Max-Age=2592000" in cookie


def test_expired_partitioned_session_refresh_preserves_partitioned():
    app, raw_token, dev_uid, _ = _build_app_and_token(
        extra_config={
            "JWT_ACCESS_TOKEN_EXPIRES": 1,
            "FSM_COOKIE_PARTITIONED": True,
            "JWT_COOKIE_SAMESITE": "None",
            "JWT_COOKIE_SECURE": True,
        }
    )
    client = _make_client_with_token(app, raw_token)

    import time

    time.sleep(1.5)

    resp = client.get("/protected", headers=_auth_headers(dev_uid))

    assert resp.status_code == 200
    for cookie in resp.headers.getlist("Set-Cookie"):
        assert "Partitioned" in cookie


def test_expired_token_refresh_rejects_revoked_record():
    app, raw_token, dev_uid, user = _build_app_and_token(
        extra_config={"JWT_ACCESS_TOKEN_EXPIRES": 1}
    )
    clear_session_token_value(user.tokens[0])
    client = _make_client_with_token(app, raw_token)

    import time

    time.sleep(1.5)

    resp = client.get("/protected", headers=_auth_headers(dev_uid))

    assert resp.status_code == 455


# ---------------------------------------------------------------------------
# callback edge cases
# ---------------------------------------------------------------------------
def test_user_lookup_returns_none():
    app, raw_token, dev_uid, _ = _build_app_and_token(
        callbacks_overrides={"user_lookup": lambda identity: None},
    )
    client = _make_client_with_token(app, raw_token)

    resp = client.get("/protected", headers=_auth_headers(dev_uid))
    assert resp.status_code in (401, 455)


def test_inactive_user_rejected():
    app, raw_token, dev_uid, _ = _build_app_and_token(active=False)
    client = _make_client_with_token(app, raw_token)

    resp = client.get("/protected", headers=_auth_headers(dev_uid))
    assert resp.status_code in (401, 455)


# ---------------------------------------------------------------------------
# Optional callbacks gracefully absent
# ---------------------------------------------------------------------------
def test_no_verify_token_callback():
    app, raw_token, dev_uid, _ = _build_app_and_token(
        callbacks_overrides={"verify_user_token": None},
    )
    client = _make_client_with_token(app, raw_token)

    resp = client.get("/protected", headers=_auth_headers(dev_uid))
    assert resp.status_code == 200


def test_jwt_required_rejects_unauthenticated():
    """A @jwt_required (non-optional) route returns 4xx without a token."""
    app, _, _, _ = _build_app_and_token()
    client = app.test_client()

    resp = client.get("/protected")
    assert resp.status_code in (401, 422)
    data = resp.get_json()
    assert data is not None
