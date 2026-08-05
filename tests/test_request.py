"""Tests for request/device helpers."""

from flask import Flask
from flask_jwt_extended import JWTManager

from flask_session_manager_sk.request import (
    get_agent,
    get_dets_from_request,
    get_ip,
    get_token,
)


def make_app(extra_config=None):
    app = Flask(__name__)
    app.config.update(
        {
            "SECRET_KEY": "test-secret",
            "JWT_TOKEN_LOCATION": ["cookies", "headers"],
            "JWT_ACCESS_COOKIE_NAME": "access_token",
            "JWT_COOKIE_CSRF_PROTECT": True,
            "FRONTEND_URL": "https://example.com",
            "CORS_ORIGINS": ["https://example.com"],
        }
    )
    if extra_config:
        app.config.update(extra_config)
    JWTManager(app)
    return app


# ---------------------------------------------------------------------------
# get_token
# ---------------------------------------------------------------------------
def test_bearer_token_takes_precedence():
    app = make_app()
    with app.test_request_context(
        "/",
        headers={
            "Authorization": "Bearer abc123",
            "Cookie": "access_token=cookie456",
        },
    ):
        from flask import request

        result = get_token(request)
    assert result == "abc123"


def test_cookie_token_when_no_bearer():
    app = make_app()
    with app.test_request_context(
        "/",
        headers={"Cookie": "access_token=cookie456"},
    ):
        from flask import request

        result = get_token(request)
    assert result == "cookie456"


def test_configured_cookie_name():
    app = make_app({"JWT_ACCESS_COOKIE_NAME": "my_session"})
    with app.test_request_context(
        "/",
        headers={"Cookie": "my_session=custom-token"},
    ):
        from flask import request

        result = get_token(request)
    assert result == "custom-token"


def test_no_token_returns_none():
    app = make_app()
    with app.test_request_context("/"):
        from flask import request

        result = get_token(request)
    assert result is None


# ---------------------------------------------------------------------------
# get_agent
# ---------------------------------------------------------------------------
def test_get_agent_returns_stable_string():
    app = make_app()
    with app.test_request_context(
        "/",
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/130.0.0.0",
        },
    ):
        from flask import request

        agent = get_agent(request)
    assert "Chrome" in agent
    assert "Linux" in agent


# ---------------------------------------------------------------------------
# get_ip
# ---------------------------------------------------------------------------
def test_get_ip():
    app = make_app()
    with app.test_request_context("/", environ_base={"REMOTE_ADDR": "192.168.1.1"}):
        from flask import request

        ip = get_ip(request)
    assert ip == "192.168.1.1"


# ---------------------------------------------------------------------------
# get_dets_from_request
# ---------------------------------------------------------------------------
def test_get_dets_from_request():
    app = make_app()
    with app.test_request_context(
        "/",
        headers={
            "Authorization": "Bearer abc123",
            "deviceUID": "dev-001",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/130.0.0.0",
        },
    ):
        from flask import request

        agent, device_uid, token = get_dets_from_request(request)
    assert "Chrome" in agent
    assert device_uid == "dev-001"
    assert token == "abc123"
