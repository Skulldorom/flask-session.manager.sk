"""Cookie and CSRF helpers for Flask JWT session management."""

from urllib.parse import urlparse

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _normalise_origin(value):
    if not value:
        return None
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def configured_browser_origins(app=None):
    from flask import current_app

    _app = app if app is not None else current_app
    origins = set()

    frontend_url = _app.config.get("FRONTEND_URL")
    frontend_origin = _normalise_origin(frontend_url)
    if frontend_origin:
        origins.add(frontend_origin)

    for origin in _app.config.get("CORS_ORIGINS", []):
        normalised = _normalise_origin(origin)
        if normalised:
            origins.add(normalised)

    return origins


def request_uses_cookie_auth(req=None, app=None):
    from flask import current_app, request

    _req = req if req is not None else request
    _app = app if app is not None else current_app
    return bool(_req.cookies.get(_app.config["JWT_ACCESS_COOKIE_NAME"]))


def request_has_bearer_auth(req=None):
    from flask import request

    _req = req if req is not None else request
    return _req.headers.get("Authorization", "").startswith("Bearer ")


def csrf_origin_is_allowed(req=None, app=None):
    from flask import current_app, request

    _req = req if req is not None else request
    _app = app if app is not None else current_app

    origin = _normalise_origin(_req.headers.get("Origin"))
    referer = _normalise_origin(_req.headers.get("Referer"))
    allowed = configured_browser_origins(_app)
    return bool(
        allowed and ((origin and origin in allowed) or (referer and referer in allowed))
    )


def reject_cookie_csrf(req=None, app=None):
    from flask import current_app, jsonify, request

    _req = req if req is not None else request
    _app = app if app is not None else current_app

    if _req.method not in UNSAFE_METHODS:
        return None
    if not request_uses_cookie_auth(_req, _app) or request_has_bearer_auth(_req):
        return None
    if csrf_origin_is_allowed(_req, _app):
        return None

    return jsonify(status="fail", message="CSRF origin check failed"), 403


def token_response(payload, status=200, access_token=None, persistent=False):
    """Create a Flask JSON response, optionally setting an HttpOnly JWT cookie.

    When persistent=True, all Set-Cookie headers receive a Max-Age so the
    session survives browser restarts.  Default (persistent=False) leaves
    cookies as session-only (cleared on browser exit).
    """
    from flask import current_app, jsonify
    from flask_jwt_extended import set_access_cookies

    response = jsonify(payload)
    if access_token:
        set_access_cookies(response, access_token)
        if persistent:
            max_age = _resolve_persistent_max_age(current_app)
            _make_cookies_persistent(response, max_age)
    return response, status


def _make_cookies_persistent(response, max_age_seconds):
    """Append Max-Age to every Set-Cookie header on the response.

    Uses only stable Werkzeug APIs (response.headers.getlist /
    response.headers.setlist) with simple string concatenation —
    no assumptions about flask-jwt-extended internals, no
    version-dependent kwargs, no fragile header regex.
    """
    cookies = response.headers.getlist("Set-Cookie")
    if not cookies:
        return
    persistent_cookies = [f"{cookie}; Max-Age={max_age_seconds}" for cookie in cookies]
    response.headers.setlist("Set-Cookie", persistent_cookies)


def _resolve_persistent_max_age(app):
    """Return the persistent Max-Age in integer seconds.

    Reads FSM_PERSISTENT_MAX_AGE from app config.  Handles both
    timedelta and integer config values — flask-jwt-extended accepts
    either, but Max-Age in a Set-Cookie header MUST be integer
    seconds (RFC 6265).

    Raises RuntimeError if FSM_PERSISTENT_MAX_AGE is unset and
    persistent=True is used — there's no safe default; JWT token
    expiry is typically 15 minutes which is not a meaningful
    "remember me" duration.
    """
    from datetime import timedelta

    value = app.config.get("FSM_PERSISTENT_MAX_AGE")
    if value is None:
        raise RuntimeError(
            "persistent=True requires FSM_PERSISTENT_MAX_AGE to be set "
            "in the Flask app config (e.g. app.config['FSM_PERSISTENT_MAX_AGE'] "
            "= datetime.timedelta(days=30))."
        )
    return int(value.total_seconds()) if isinstance(value, timedelta) else int(value)


def clear_token_response(payload=None, status=200):
    """Create a response that clears JWT access cookies."""
    from flask import jsonify
    from flask_jwt_extended import unset_jwt_cookies

    response = jsonify(payload or {"status": "success", "message": "Logged out"})
    unset_jwt_cookies(response)
    return response, status


def clear_session_token_value(token_record):
    """Invalidate a session token while preserving its registered-device row."""
    from .tokens import clear_session_token_value as _impl

    _impl(token_record)
