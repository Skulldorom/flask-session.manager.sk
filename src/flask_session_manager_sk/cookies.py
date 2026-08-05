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


def token_response(payload, status=200, access_token=None):
    """Create a Flask JSON response, optionally setting an HttpOnly JWT cookie."""
    from flask import jsonify
    from flask_jwt_extended import set_access_cookies

    response = jsonify(payload)
    if access_token:
        set_access_cookies(response, access_token)
    return response, status


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
