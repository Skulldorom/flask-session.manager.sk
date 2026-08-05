"""Tests for cookie auth helpers."""

from flask import Flask
from flask_jwt_extended import JWTManager

from flask_session_manager_sk.cookies import (
    clear_session_token_value,
    clear_token_response,
    configured_browser_origins,
    reject_cookie_csrf,
    request_has_bearer_auth,
    request_uses_cookie_auth,
    token_response,
)


class FakeToken:
    token_hash = "old-hash"
    hint = "old-hint"
    token = "old-plaintext"


def make_app(extra_config=None):
    app = Flask(__name__)
    app.config.update(
        {
            "SECRET_KEY": "test-secret",
            "JWT_TOKEN_LOCATION": ["cookies", "headers"],
            "JWT_ACCESS_COOKIE_NAME": "access_token",
            "JWT_COOKIE_CSRF_PROTECT": True,
            "FRONTEND_URL": "https://example.com",
            "CORS_ORIGINS": ["https://example.com", "http://localhost:3000"],
        }
    )
    if extra_config:
        app.config.update(extra_config)
    JWTManager(app)
    return app


# ---------------------------------------------------------------------------
# token_response
# ---------------------------------------------------------------------------
def test_token_response_sets_httponly_cookie():
    app = make_app()
    with app.app_context():
        from flask_jwt_extended import create_access_token

        token = create_access_token(identity="1")
        response, status = token_response(
            {"status": "success", "access_token": token}, 200, token
        )

    assert status == 200
    headers = response.headers.getlist("Set-Cookie")
    cookie_str = "; ".join(headers)
    assert "access_token=" in cookie_str
    assert "HttpOnly" in cookie_str


def test_token_response_without_token_sets_no_cookie():
    app = make_app()
    with app.app_context():
        response, status = token_response({"status": "ok"}, 201)

    assert status == 201
    cookie_value = response.headers.get("Set-Cookie")
    assert cookie_value is None


# ---------------------------------------------------------------------------
# clear_token_response
# ---------------------------------------------------------------------------
def test_clear_token_response_expires_cookie():
    app = make_app()
    with app.app_context():
        response, status = clear_token_response()

    assert status == 200
    body = response.get_json()
    assert body["status"] == "success"
    headers = response.headers.getlist("Set-Cookie")
    cookie_str = "; ".join(headers)
    assert "access_token=" in cookie_str
    # The cookie should be expired
    assert "Max-Age=0" in cookie_str or "Thu, 01 Jan 1970" in cookie_str


def test_clear_token_response_accepts_custom_payload():
    app = make_app()
    with app.app_context():
        response, status = clear_token_response({"status": "ok", "msg": "bye"}, 204)

    assert status == 204
    assert response.get_json() == {"status": "ok", "msg": "bye"}


# ---------------------------------------------------------------------------
# cookie_auth / bearer_auth detection
# ---------------------------------------------------------------------------
def test_request_uses_cookie_auth():
    app = make_app()
    with app.test_request_context(
        "/",
        headers={"Cookie": "access_token=abc123"},
    ):
        assert request_uses_cookie_auth() is True

    with app.test_request_context("/"):
        assert request_uses_cookie_auth() is False


def test_request_has_bearer_auth():
    app = make_app()
    with app.test_request_context("/", headers={"Authorization": "Bearer secret"}):
        assert request_has_bearer_auth() is True

    with app.test_request_context(
        "/", headers={"Authorization": "Basic YWxhZGRpbjpvcGVuc2VzYW1l"}
    ):
        assert request_has_bearer_auth() is False

    with app.test_request_context("/"):
        assert request_has_bearer_auth() is False


# ---------------------------------------------------------------------------
# configured_browser_origins
# ---------------------------------------------------------------------------
def test_configured_browser_origins():
    app = make_app()
    with app.app_context():
        origins = configured_browser_origins()
    assert "https://example.com" in origins
    assert "http://localhost:3000" in origins


# ---------------------------------------------------------------------------
# CSRF rejection
# ---------------------------------------------------------------------------
def test_get_requests_skip_csrf_check():
    app = make_app()
    with app.test_request_context(
        "/",
        method="GET",
        headers={
            "Cookie": "access_token=abc",
            "Origin": "https://attacker.com",
        },
    ):
        result = reject_cookie_csrf()
    assert result is None


def test_cookie_auth_post_requires_allowed_origin():
    app = make_app()
    with app.test_request_context(
        "/",
        method="POST",
        headers={
            "Cookie": "access_token=abc",
            "Origin": "https://attacker.com",
        },
    ):
        result = reject_cookie_csrf()

    assert result is not None
    assert result[1] == 403
    assert result[0].get_json()["status"] == "fail"


def test_cookie_auth_post_allows_configured_origin():
    app = make_app()
    with app.test_request_context(
        "/",
        method="POST",
        headers={
            "Cookie": "access_token=abc",
            "Origin": "https://example.com",
        },
    ):
        result = reject_cookie_csrf()
    assert result is None


def test_bearer_auth_bypasses_csrf_check():
    """Non-browser clients using Bearer tokens should not be CSRF-checked."""
    app = make_app()
    with app.test_request_context(
        "/",
        method="POST",
        headers={
            "Authorization": "Bearer xyz",
            "Cookie": "access_token=abc",
            "Origin": "https://attacker.com",
        },
    ):
        result = reject_cookie_csrf()
    assert result is None


# ---------------------------------------------------------------------------
# clear_session_token_value
# ---------------------------------------------------------------------------
def test_clear_session_token_value():
    token = FakeToken()
    clear_session_token_value(token)
    assert token.token_hash != "old-hash"
    assert token.hint == ""
    assert token.token is None
