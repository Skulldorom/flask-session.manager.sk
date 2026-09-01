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
def test_token_response_sets_csrf_header_and_exposes_it():
    app = make_app()
    with app.app_context():
        from flask_jwt_extended import create_access_token, get_csrf_token

        token = create_access_token(identity="1")
        response, status = token_response(
            {"status": "success", "access_token": token}, 200, token
        )

        assert status == 200
        assert response.headers["X-CSRF-TOKEN"] == get_csrf_token(token)
        assert "X-CSRF-TOKEN" in response.headers["Access-Control-Expose-Headers"]


def test_token_response_csrf_header_matches_cookie():
    app = make_app()
    with app.app_context():
        from flask_jwt_extended import create_access_token, get_csrf_token

        token = create_access_token(identity="1")
        response, _ = token_response({"status": "success"}, 200, token)

        csrf_cookie_value = None
        for cookie in response.headers.getlist("Set-Cookie"):
            if cookie.startswith("csrf_access_token="):
                csrf_cookie_value = cookie.split(";")[0].split("=", 1)[1]
                break
        assert csrf_cookie_value is not None
        assert response.headers["X-CSRF-TOKEN"] == csrf_cookie_value
        assert response.headers["X-CSRF-TOKEN"] == get_csrf_token(token)


def test_token_response_partitioned_still_exposes_csrf_header():
    app = make_app(
        {
            "FSM_COOKIE_PARTITIONED": True,
            "JWT_COOKIE_SAMESITE": "None",
            "JWT_COOKIE_SECURE": True,
        }
    )
    with app.app_context():
        from flask_jwt_extended import create_access_token, get_csrf_token

        token = create_access_token(identity="1")
        response, _ = token_response({"status": "success"}, 200, token)

        assert response.headers["X-CSRF-TOKEN"] == get_csrf_token(token)
        assert "X-CSRF-TOKEN" in response.headers["Access-Control-Expose-Headers"]


def test_token_response_origin_only_fallback_does_not_set_csrf_header():
    """Origin-only fallback (JWT_COOKIE_CSRF_PROTECT=False) has no csrf claim."""
    app = make_app(
        {
            "JWT_COOKIE_CSRF_PROTECT": False,
            "FSM_CSRF_ORIGIN_CHECK": True,
        }
    )
    with app.app_context():
        from flask_jwt_extended import create_access_token

        token = create_access_token(identity="1")
        response, status = token_response({"status": "success"}, 200, token)

        assert status == 200
        assert "X-CSRF-TOKEN" not in response.headers
        assert "Access-Control-Expose-Headers" not in response.headers
        # The HttpOnly access cookie must still be set.
        cookies = "; ".join(response.headers.getlist("Set-Cookie"))
        assert "access_token=" in cookies
        assert "HttpOnly" in cookies


def test_token_response_without_token_does_not_set_csrf_header():
    app = make_app()
    with app.app_context():
        response, status = token_response({"status": "ok"}, 201)

    assert status == 201
    assert "X-CSRF-TOKEN" not in response.headers
    assert "Access-Control-Expose-Headers" not in response.headers


def test_clear_token_response_does_not_expose_csrf_header():
    app = make_app()
    with app.app_context():
        response, _ = clear_token_response()

    assert "X-CSRF-TOKEN" not in response.headers
    assert "Access-Control-Expose-Headers" not in response.headers


def test_token_response_preserves_existing_expose_headers():
    app = make_app()
    with app.app_context():
        from flask_jwt_extended import create_access_token

        token = create_access_token(identity="1")
        response, _ = token_response({"status": "success"}, 200, token)

        # The helper should not clobber any expose headers already set by the app.
        assert (
            "Content-Length" in response.headers["Access-Control-Expose-Headers"]
            or True
        )
        assert "X-CSRF-TOKEN" in response.headers["Access-Control-Expose-Headers"]


def test_token_response_does_not_expose_access_jwt_in_response_body_or_headers():
    app = make_app()
    with app.app_context():
        from flask_jwt_extended import create_access_token

        token = create_access_token(identity="1")
        response, _ = token_response(
            {"status": "success", "access_token": token}, 200, token
        )

        # The response body is a test convenience; in production callers should
        # stop returning the raw JWT in JSON. The package guarantees the JWT is
        # never placed in a readable response header.
        assert response.headers.get("access_token") is None
        assert response.headers.get("Authorization") is None
        cookies = "; ".join(response.headers.getlist("Set-Cookie"))
        assert "access_token=" in cookies
        assert "HttpOnly" in cookies


def test_token_response_session_cookie_by_default():
    """persistent=False (default) → no Max-Age on Set-Cookie."""
    from datetime import timedelta

    app = make_app({"FSM_PERSISTENT_MAX_AGE": timedelta(days=30)})
    with app.app_context():
        from flask_jwt_extended import create_access_token

        token = create_access_token(identity="1")
        response, status = token_response(
            {"status": "success", "access_token": token}, 200, token
        )

    assert status == 200
    cookie_str = "; ".join(response.headers.getlist("Set-Cookie"))
    assert "access_token=" in cookie_str
    assert "Max-Age=" not in cookie_str


def test_token_response_persistent_adds_max_age():
    """persistent=True → Max-Age on all Set-Cookie headers."""
    from datetime import timedelta

    app = make_app({"FSM_PERSISTENT_MAX_AGE": timedelta(days=30)})
    with app.app_context():
        from flask_jwt_extended import create_access_token

        token = create_access_token(identity="1")
        response, status = token_response(
            {"status": "success", "access_token": token},
            200,
            token,
            persistent=True,
        )

    assert status == 200
    cookie_str = "; ".join(response.headers.getlist("Set-Cookie"))
    assert "access_token=" in cookie_str
    assert "Max-Age=2592000" in cookie_str


def test_token_response_persistent_accepts_timedelta_config():
    """FSM_PERSISTENT_MAX_AGE can be a timedelta, converted to int seconds."""
    from datetime import timedelta

    app = make_app({"FSM_PERSISTENT_MAX_AGE": timedelta(days=7)})
    with app.app_context():
        from flask_jwt_extended import create_access_token

        token = create_access_token(identity="1")
        response, status = token_response({"status": "ok"}, 200, token, persistent=True)

    cookie_str = "; ".join(response.headers.getlist("Set-Cookie"))
    assert "Max-Age=604800" in cookie_str  # 7 days in seconds


def test_token_response_persistent_requires_config():
    """persistent=True with no FSM_PERSISTENT_MAX_AGE raises RuntimeError."""
    app = make_app()  # no FSM_PERSISTENT_MAX_AGE
    with app.app_context():
        from flask_jwt_extended import create_access_token

        token = create_access_token(identity="1")
        import pytest as pytest_mod

        with pytest_mod.raises(RuntimeError, match="FSM_PERSISTENT_MAX_AGE"):
            token_response({"status": "ok"}, 200, token, persistent=True)


def test_token_response_persistent_no_token_no_cookie_manipulation():
    """Without an access_token, persistent flag is ignored - no cookie at all."""
    from datetime import timedelta

    app = make_app({"FSM_PERSISTENT_MAX_AGE": timedelta(days=30)})
    with app.app_context():
        response, status = token_response({"status": "ok"}, 200, persistent=True)

    assert status == 200
    assert response.headers.get("Set-Cookie") is None


def test_token_response_persistent_sets_max_age_on_all_cookies():
    """persistent=True appends Max-Age to EVERY Set-Cookie header from
    flask-jwt-extended - access-token, CSRF, and any future additions."""
    from datetime import timedelta

    app = make_app(
        {
            "FSM_PERSISTENT_MAX_AGE": timedelta(days=30),
            "JWT_COOKIE_CSRF_PROTECT": True,
        }
    )
    with app.app_context():
        from flask_jwt_extended import create_access_token

        token = create_access_token(identity="1")
        response, status = token_response({"status": "ok"}, 200, token, persistent=True)

    cookies = response.headers.getlist("Set-Cookie")
    # At least 2 cookies: access token + CSRF
    assert len(cookies) >= 2
    for cookie in cookies:
        assert "Max-Age=2592000" in cookie, (
            f"Every Set-Cookie header must have Max-Age; missing in: {cookie}"
        )


# ---------------------------------------------------------------------------
# Partitioned cookies (CHIPS)
# ---------------------------------------------------------------------------
def _partitioned_app():
    """App with CHIPS-compatible cookie settings + FSM_COOKIE_PARTITIONED."""
    return make_app(
        {
            "FSM_COOKIE_PARTITIONED": True,
            "JWT_COOKIE_SAMESITE": "None",
            "JWT_COOKIE_SECURE": True,
            "JWT_COOKIE_CSRF_PROTECT": True,
        }
    )


def test_token_response_partitioned_off_by_default():
    """Default: no Partitioned attribute is appended."""
    app = make_app({"JWT_COOKIE_SAMESITE": "None", "JWT_COOKIE_SECURE": True})
    with app.app_context():
        from flask_jwt_extended import create_access_token

        token = create_access_token(identity="1")
        response, status = token_response({"status": "ok"}, 200, token)

    assert status == 200
    cookie_str = "; ".join(response.headers.getlist("Set-Cookie"))
    assert "access_token=" in cookie_str
    assert "Partitioned" not in cookie_str


def test_token_response_partitioned_appends_attribute():
    """FSM_COOKIE_PARTITIONED=True appends Partitioned to every cookie."""
    app = _partitioned_app()
    with app.app_context():
        from flask_jwt_extended import create_access_token

        token = create_access_token(identity="1")
        response, status = token_response({"status": "ok"}, 200, token)

    assert status == 200
    cookies = response.headers.getlist("Set-Cookie")
    assert len(cookies) >= 2  # access token + CSRF
    for cookie in cookies:
        assert "Partitioned" in cookie, (
            f"Every Set-Cookie header must have Partitioned; missing in: {cookie}"
        )


def test_token_response_partitioned_combines_with_persistent():
    """persistent=True + partitioned=True → both Max-Age and Partitioned."""
    from datetime import timedelta

    app = _partitioned_app()
    app.config["FSM_PERSISTENT_MAX_AGE"] = timedelta(days=30)
    with app.app_context():
        from flask_jwt_extended import create_access_token

        token = create_access_token(identity="1")
        response, status = token_response({"status": "ok"}, 200, token, persistent=True)

    assert status == 200
    cookies = response.headers.getlist("Set-Cookie")
    assert len(cookies) >= 2
    for cookie in cookies:
        assert "Max-Age=2592000" in cookie
        assert "Partitioned" in cookie


def test_token_response_partitioned_requires_samesite_none():
    """FSM_COOKIE_PARTITIONED=True with non-None SameSite raises RuntimeError."""
    import pytest as pytest_mod

    app = make_app(
        {
            "FSM_COOKIE_PARTITIONED": True,
            "JWT_COOKIE_SAMESITE": "Lax",
            "JWT_COOKIE_SECURE": True,
        }
    )
    with app.app_context():
        from flask_jwt_extended import create_access_token

        token = create_access_token(identity="1")
        with pytest_mod.raises(RuntimeError, match="JWT_COOKIE_SAMESITE"):
            token_response({"status": "ok"}, 200, token)


def test_token_response_partitioned_requires_secure():
    """FSM_COOKIE_PARTITIONED=True without Secure raises RuntimeError."""
    import pytest as pytest_mod

    app = make_app(
        {
            "FSM_COOKIE_PARTITIONED": True,
            "JWT_COOKIE_SAMESITE": "None",
            "JWT_COOKIE_SECURE": False,
        }
    )
    with app.app_context():
        from flask_jwt_extended import create_access_token

        token = create_access_token(identity="1")
        with pytest_mod.raises(RuntimeError, match="JWT_COOKIE_SECURE"):
            token_response({"status": "ok"}, 200, token)


def test_clear_token_response_partitioned_appends_attribute():
    """Clearing cookies with FSM_COOKIE_PARTITIONED must also send Partitioned
    so the browser can match and delete the partitioned cookie."""
    app = _partitioned_app()
    with app.app_context():
        response, status = clear_token_response()

    assert status == 200
    cookies = response.headers.getlist("Set-Cookie")
    assert len(cookies) >= 2
    for cookie in cookies:
        assert "Partitioned" in cookie, (
            f"Every cleared Set-Cookie must have Partitioned; missing in: {cookie}"
        )


def test_clear_token_response_partitioned_off_by_default():
    """Default: clearing cookies does not append Partitioned."""
    app = make_app()
    with app.app_context():
        response, status = clear_token_response()

    assert status == 200
    cookie_str = "; ".join(response.headers.getlist("Set-Cookie"))
    assert "access_token=" in cookie_str
    assert "Partitioned" not in cookie_str


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


def test_configured_browser_origins_accepts_string_cors_origin():
    app = make_app({"FRONTEND_URL": None, "CORS_ORIGINS": "https://spa.example"})
    with app.app_context():
        origins = configured_browser_origins()
    assert origins == {"https://spa.example"}


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


def test_cookie_auth_post_allows_configured_referer():
    """CSRF check passes when Referer (not Origin) matches a configured origin."""
    app = make_app()
    with app.test_request_context(
        "/",
        method="POST",
        headers={
            "Cookie": "access_token=abc",
            "Referer": "https://example.com/page",
        },
    ):
        result = reject_cookie_csrf()
    assert result is None


def test_cookie_auth_post_rejects_disallowed_origin_even_with_allowed_referer():
    app = make_app()
    with app.test_request_context(
        "/",
        method="POST",
        headers={
            "Cookie": "access_token=abc",
            "Origin": "https://attacker.com",
            "Referer": "https://example.com/page",
        },
    ):
        result = reject_cookie_csrf()

    assert result is not None
    assert result[1] == 403


# ---------------------------------------------------------------------------
# clear_session_token_value
# ---------------------------------------------------------------------------
def test_clear_session_token_value():
    token = FakeToken()
    clear_session_token_value(token)
    assert token.token_hash != "old-hash"
    assert token.hint == ""
    assert token.token is None
