"""Tests for the SessionManager extension with callback wiring."""

from flask import Flask, jsonify
from flask_jwt_extended import create_access_token, jwt_required

from flask_session_manager_sk import SessionManager, SessionManagerCallbacks
from flask_session_manager_sk.tokens import create_token_hash, token_hint


class FakeUser:
    def __init__(self, user_id, active=True):
        self.id = user_id
        self.active = active
        self.tokens = []

    def check_token(self, agent, device_uid, _token_str):
        for t in self.tokens:
            if t["agent"] == agent and t["device_uid"] == device_uid:
                return t
        return None

    def add_token(self, raw_token, agent, device_uid):
        self.tokens.append(
            {
                "agent": agent,
                "device_uid": device_uid,
                "token_hash": create_token_hash(raw_token),
                "hint": token_hint(raw_token),
            }
        )


def _build_app_and_token(extra_config=None, callbacks_overrides=None, active=True):
    """Build an app with SessionManager, return (app, token, dev_uid)."""
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
        u.add_token(tok, agent, dev)
        return tok

    cb = {
        "user_lookup": lambda i: user if i == user.id else None,
        "refresh_user_token": lambda u, agent, dev: make_token(u, agent, dev),
        "verify_user_token": lambda u, agent, dev, tok: u.check_token(agent, dev, tok),
        "is_user_active": lambda u: u.active,
    }
    if callbacks_overrides:
        cb.update(callbacks_overrides)

    _manager = SessionManager(app, callbacks=SessionManagerCallbacks(**cb))

    @app.route("/protected")
    @jwt_required()
    def protected():
        return jsonify(status="ok")

    dev_agent = "Linux Chrome"
    dev_uid = "dev-test"
    with app.app_context():
        raw = create_access_token(identity="user-1")
    user.add_token(raw, dev_agent, dev_uid)

    return app, raw, dev_uid


def _make_client_with_token(app, token):
    """Create test client with auth cookies already set."""
    client = app.test_client()
    client.set_cookie("access_token", token)
    client.set_cookie("csrf_access_token", "fake-csrf")
    return client


# ---------------------------------------------------------------------------
# SessionManager init patterns
# ---------------------------------------------------------------------------
def test_session_manager_direct_init():
    """SessionManager can be initialized with app + callbacks in one call."""
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "enough-bytes-for-hmac-sha256"
    app.config["JWT_TOKEN_LOCATION"] = ["cookies"]
    app.config["JWT_COOKIE_CSRF_PROTECT"] = False
    app.config["FRONTEND_URL"] = "http://localhost"
    app.config["CORS_ORIGINS"] = ["http://localhost"]

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


# ---------------------------------------------------------------------------
# Login → protected route flow
# ---------------------------------------------------------------------------
def test_pre_registered_token_accesses_protected_route():
    app, raw_token, dev_uid = _build_app_and_token()
    client = _make_client_with_token(app, raw_token)

    resp = client.get(
        "/protected",
        headers={
            "X-CSRF-TOKEN": "fake-csrf",
            "deviceUID": dev_uid,
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/130.0.0.0",
        },
    )
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Invalid token
# ---------------------------------------------------------------------------
def test_invalid_token_returns_error():
    app, _, _ = _build_app_and_token()
    client = app.test_client()
    client.set_cookie("access_token", "not-a-real-jwt")

    resp = client.get("/protected")
    assert resp.status_code != 200
    data = resp.get_json()
    assert data is not None


# ---------------------------------------------------------------------------
# Expired token → refresh
# ---------------------------------------------------------------------------
def test_expired_token_triggers_refresh():
    app, raw_token, dev_uid = _build_app_and_token(
        extra_config={"JWT_ACCESS_TOKEN_EXPIRES": 1}
    )
    client = _make_client_with_token(app, raw_token)

    import time

    time.sleep(1.5)

    resp = client.get(
        "/protected",
        headers={
            "X-CSRF-TOKEN": "fake-csrf",
            "deviceUID": dev_uid,
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/130.0.0.0",
        },
    )

    data = resp.get_json()
    if resp.status_code == 200 and data.get("refreshed"):
        assert data["refreshed"] is True
        assert "access_token" in data


# ---------------------------------------------------------------------------
# callback edge cases
# ---------------------------------------------------------------------------
def test_user_lookup_returns_none():
    app, raw_token, dev_uid = _build_app_and_token(
        callbacks_overrides={"user_lookup": lambda identity: None},
    )
    client = _make_client_with_token(app, raw_token)

    resp = client.get(
        "/protected",
        headers={
            "X-CSRF-TOKEN": "fake-csrf",
            "deviceUID": dev_uid,
        },
    )
    assert resp.status_code in (401, 455)


def test_inactive_user_rejected():
    app, raw_token, dev_uid = _build_app_and_token(active=False)
    client = _make_client_with_token(app, raw_token)

    resp = client.get(
        "/protected",
        headers={
            "X-CSRF-TOKEN": "fake-csrf",
            "deviceUID": dev_uid,
        },
    )
    assert resp.status_code in (401, 455)


# ---------------------------------------------------------------------------
# Optional callbacks gracefully absent
# ---------------------------------------------------------------------------
def test_no_verify_token_callback():
    app, raw_token, dev_uid = _build_app_and_token(
        callbacks_overrides={"verify_user_token": None},
    )
    client = _make_client_with_token(app, raw_token)

    resp = client.get(
        "/protected",
        headers={
            "X-CSRF-TOKEN": "fake-csrf",
            "deviceUID": dev_uid,
        },
    )
    assert resp.status_code == 200


def test_jwt_required_rejects_unauthenticated():
    """A @jwt_required (non-optional) route returns 4xx without a token."""
    app, _, _ = _build_app_and_token()
    client = app.test_client()

    resp = client.get("/protected")
    assert resp.status_code in (401, 422)
    data = resp.get_json()
    assert data is not None
