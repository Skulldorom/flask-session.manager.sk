"""Integration tests: full session lifecycle with a minimal Flask app."""

import pytest
from flask import Flask, jsonify
from flask import request as flask_request
from flask_jwt_extended import create_access_token, jwt_required

from flask_session_manager_sk import SessionManager, SessionManagerCallbacks
from flask_session_manager_sk.cookies import clear_token_response, token_response
from flask_session_manager_sk.tokens import verify_token_hash


class FakeUser:
    def __init__(self, user_id, active=True):
        self.id = user_id
        self.active = active
        self._tokens = []

    def verify_token(self, agent, device_uid, token):
        for t in self._tokens:
            metadata_matches = t["agent"] == agent and t["device_uid"] == device_uid
            if metadata_matches and verify_token_hash(token, t["token_hash"]):
                return t
        return None

    def add_token(self, raw_token, agent, device_uid):
        from flask_session_manager_sk.tokens import create_token_hash, token_hint

        self._tokens.append(
            {
                "agent": agent,
                "device_uid": device_uid,
                "token_hash": create_token_hash(raw_token),
                "hint": token_hint(raw_token),
            }
        )

    def create_and_store_token(self, agent, device_uid, app_for_context):
        with app_for_context.app_context():
            new_token = create_access_token(identity=str(self.id))
        self.add_token(new_token, agent, device_uid)
        return new_token


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config.update(
        {
            "SECRET_KEY": "integration-test-secret-key-for-sha256",
            "JWT_TOKEN_LOCATION": ["cookies"],
            "JWT_ACCESS_COOKIE_NAME": "access_token_cookie",
            "JWT_COOKIE_CSRF_PROTECT": True,
            "JWT_COOKIE_SECURE": False,
            "JWT_COOKIE_SAMESITE": "Lax",
            "FRONTEND_URL": "http://localhost:5173",
            "CORS_ORIGINS": ["http://localhost:5173"],
        }
    )

    user = FakeUser("user-1")

    callbacks = SessionManagerCallbacks(
        user_lookup=lambda identity: user if identity == user.id else None,
        refresh_user_token=lambda u, ag, dev: u.create_and_store_token(ag, dev, app),
        verify_user_token=lambda u, ag, dev, tok: u.verify_token(ag, dev, tok),
        is_user_active=lambda u: u.active,
    )

    manager = SessionManager()
    manager.init_app(app, callbacks=callbacks)

    # ---- App routes ----
    @app.route("/auth/login", methods=["POST"])
    def login():
        _ = flask_request.get_json()
        agent = "Linux Chrome"
        device_uid = flask_request.headers.get("deviceUID", "dev-default")
        new_token = user.create_and_store_token(agent, device_uid, app)
        return token_response(
            {"status": "success", "access_token": new_token},
            200,
            new_token,
        )

    @app.route("/auth/who", methods=["GET"])
    @jwt_required(optional=True)
    def who():
        from flask_jwt_extended import current_user

        if current_user:
            return (
                jsonify(
                    logged_in=True,
                    is_admin=True,
                    Info={"firstName": "Test", "lastName": "User"},
                ),
                200,
            )
        return jsonify(logged_in=False), 200

    @app.route("/auth/logout", methods=["POST"])
    @jwt_required(optional=True)
    def logout():
        return clear_token_response()

    @app.route("/protected", methods=["GET"])
    @jwt_required()
    def protected():
        return jsonify(status="ok")

    return app


def _login_and_configure_client(app, device_uid="dev-test"):
    """Login and return a client with auth cookies already set."""
    ua = "Mozilla/5.0 (X11; Linux x86_64) Chrome/130.0.0.0"
    client = app.test_client()
    resp = client.post(
        "/auth/login",
        json={"email": "test@example.com", "password": "pw"},
        headers={
            "Origin": "http://localhost:5173",
            "deviceUID": device_uid,
            "User-Agent": ua,
        },
    )
    assert resp.status_code == 200

    # Transfer cookies from login response to client
    for cookie_header in resp.headers.getlist("Set-Cookie"):
        name_val = cookie_header.split(";")[0]
        name, val = name_val.split("=", 1)
        client.set_cookie(name, val)

    # Extract CSRF token for subsequent requests
    csrf_token = None
    for c in resp.headers.getlist("Set-Cookie"):
        if c.startswith("csrf_access_token="):
            csrf_token = c.split(";")[0].split("=", 1)[1]

    return client, csrf_token, device_uid, ua


# ---------------------------------------------------------------------------
# Full lifecycle
# ---------------------------------------------------------------------------
def test_login_sets_cookie(app):
    client = app.test_client()
    resp = client.post(
        "/auth/login",
        json={"email": "test@example.com", "password": "pw"},
        headers={
            "deviceUID": "dev-integration-test",
            "Origin": "http://localhost:5173",
        },
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "success"

    cookies = resp.headers.getlist("Set-Cookie")
    cookie_str = "; ".join(cookies)
    assert "access_token_cookie=" in cookie_str
    assert "HttpOnly" in cookie_str


def test_who_returns_logged_out_before_login(app):
    client = app.test_client()
    resp = client.get("/auth/who")
    data = resp.get_json()
    assert data["logged_in"] is False


def test_who_returns_logged_in_after_login(app):
    client, csrf, dev_uid, ua = _login_and_configure_client(app, "dev-who-test")
    resp = client.get(
        "/auth/who",
        headers={
            "X-CSRF-TOKEN": csrf or "",
            "deviceUID": dev_uid,
            "User-Agent": ua,
        },
    )
    data = resp.get_json()
    assert data["logged_in"] is True


def test_protected_route_accessible_after_login(app):
    client, csrf, dev_uid, ua = _login_and_configure_client(app)
    resp = client.get(
        "/protected",
        headers={
            "X-CSRF-TOKEN": csrf or "",
            "deviceUID": dev_uid,
            "User-Agent": ua,
        },
    )
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


def test_logout_clears_cookie(app):
    client, csrf, dev_uid, ua = _login_and_configure_client(app)
    resp = client.post(
        "/auth/logout",
        headers={
            "X-CSRF-TOKEN": csrf or "",
            "Origin": "http://localhost:5173",
            "User-Agent": ua,
            "deviceUID": dev_uid,
        },
    )
    assert resp.status_code == 200
    logout_cookies = resp.headers.getlist("Set-Cookie")
    logout_str = "; ".join(logout_cookies)
    assert "access_token_cookie=" in logout_str


def test_protected_route_blocked_without_login(app):
    client = app.test_client()
    resp = client.get("/protected")
    assert resp.status_code != 200
