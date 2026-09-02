"""Cookie and CSRF helpers for Flask JWT session management."""

from collections.abc import Mapping
from urllib.parse import urlparse

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

CSRF_HEADER_NAME = "X-CSRF-TOKEN"


def _normalise_origin(value, *, strict=False, setting_name="origin"):
    if value is None:
        return None
    if not isinstance(value, str):
        if strict:
            raise RuntimeError(
                f"{setting_name} entries must be URL strings; got {value!r}."
            )
        return None
    try:
        parsed = urlparse(value)
        valid = (
            parsed.scheme in {"http", "https"}
            and bool(parsed.netloc)
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
        )
        # Accessing port validates malformed values such as ':not-a-port'.
        _ = parsed.port
    except ValueError:
        valid = False
    if not valid:
        if strict:
            raise RuntimeError(
                f"Invalid {setting_name} entry {value!r}; expected an http(s) "
                "browser URL."
            )
        return None
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def configured_browser_origins(app=None):
    from flask import current_app

    _app = app if app is not None else current_app
    origins = set()

    frontend_url = _app.config.get("FRONTEND_URL")
    frontend_origin = _normalise_origin(
        frontend_url, strict=frontend_url is not None, setting_name="FRONTEND_URL"
    )
    if frontend_origin:
        origins.add(frontend_origin)

    cors_origins = _app.config.get("CORS_ORIGINS", [])
    if isinstance(cors_origins, str):
        cors_origins = [cors_origins]

    for origin in cors_origins:
        normalised = _normalise_origin(origin, strict=True, setting_name="CORS_ORIGINS")
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
    if not allowed:
        return False
    if origin:
        return origin in allowed
    return bool(referer and referer in allowed)


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

    When FSM_COOKIE_PARTITIONED is enabled, all Set-Cookie headers also
    receive the ``Partitioned`` attribute (CHIPS).  Partitioned cookies are
    stored in a per-top-level-site partition, which keeps cross-site cookie
    auth working on browsers that block third-party cookies by default
    (notably iOS WebKit / Safari ITP).

    For cross-site SPAs that cannot read the API-scoped CSRF cookie, the
    current CSRF value is also returned in the ``X-CSRF-TOKEN`` response
    header and exposed via ``Access-Control-Expose-Headers``. The access
    JWT itself remains HttpOnly and is never exposed to JavaScript.
    """
    from flask import current_app, jsonify
    from flask_jwt_extended import set_access_cookies

    _validate_cookie_only_payload(payload, access_token)
    if access_token:
        validate_token_response_config(current_app, persistent=persistent)
    response = jsonify(payload)
    if access_token:
        set_access_cookies(response, access_token)
        if persistent:
            max_age = _resolve_persistent_max_age(current_app)
            _make_cookies_persistent(response, max_age)
        if _resolve_partitioned(current_app):
            _make_cookies_partitioned(response)
        _set_csrf_header(response, access_token)
    return response, status


def session_response(payload, status=200):
    """Create an authenticated session/bootstrap response with its CSRF claim.

    The calling route must establish JWT context (normally with
    ``@jwt_required()`` or ``@jwt_required(optional=True)``). The access JWT is
    never returned; only its double-submit CSRF claim is exposed.
    """
    from flask import current_app, jsonify
    from flask_jwt_extended import get_jwt

    response = jsonify(payload)
    if not current_app.config.get("JWT_COOKIE_CSRF_PROTECT", True):
        return response, status
    if not request_uses_cookie_auth() or request_has_bearer_auth():
        return response, status

    jwt_data = get_jwt()
    csrf_value = jwt_data.get("csrf") if jwt_data else None
    if csrf_value:
        _set_csrf_value_header(response, csrf_value)
    return response, status


def validate_token_response_config(app=None, *, persistent=False):
    """Validate failure-prone cookie options before a token is created/stored."""
    from flask import current_app

    _app = app if app is not None else current_app
    if persistent:
        _resolve_persistent_max_age(_app)
    _resolve_partitioned(_app)


def _validate_cookie_only_payload(payload, access_token):
    if not access_token or not isinstance(payload, Mapping):
        return
    if "access_token" in payload or any(
        value == access_token for value in payload.values()
    ):
        raise ValueError(
            "token_response() payload must not expose the access JWT; it is set "
            "only as an HttpOnly cookie."
        )


def _make_cookies_persistent(response, max_age_seconds):
    """Append Max-Age to every Set-Cookie header on the response.

    Uses only stable Werkzeug APIs (response.headers.getlist /
    response.headers.setlist) with simple string concatenation -
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
    timedelta and integer config values - flask-jwt-extended accepts
    either, but Max-Age in a Set-Cookie header MUST be integer
    seconds (RFC 6265).

    Raises RuntimeError if FSM_PERSISTENT_MAX_AGE is unset and
    persistent=True is used - there's no safe default; JWT token
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


def _resolve_partitioned(app):
    """Return whether FSM_COOKIE_PARTITIONED is enabled for the app.

    CHIPS (Partitioned cookies) requires the cookie to also be
    ``SameSite=None; Secure`` - browsers ignore the Partitioned attribute
    otherwise.  Raise a clear error instead of silently emitting a cookie
    that WebKit will drop, mirroring the FSM_PERSISTENT_MAX_AGE pattern.
    """
    if not app.config.get("FSM_COOKIE_PARTITIONED", False):
        return False
    samesite = app.config.get("JWT_COOKIE_SAMESITE")
    secure = app.config.get("JWT_COOKIE_SECURE")
    if samesite != "None" or not secure:
        raise RuntimeError(
            "FSM_COOKIE_PARTITIONED=True requires JWT_COOKIE_SAMESITE='None' "
            "and JWT_COOKIE_SECURE=True (CHIPS spec); got "
            f"samesite={samesite!r}, secure={secure!r}."
        )
    return True


def _make_cookies_partitioned(response):
    """Append the Partitioned attribute to every Set-Cookie header.

    Uses the same stable Werkzeug APIs as _make_cookies_persistent - no
    assumptions about flask-jwt-extended internals, no fragile header regex.
    """
    cookies = response.headers.getlist("Set-Cookie")
    if not cookies:
        return
    partitioned_cookies = [f"{cookie}; Partitioned" for cookie in cookies]
    response.headers.setlist("Set-Cookie", partitioned_cookies)


def _set_csrf_header(response, encoded_token):
    """Expose the current CSRF value for cross-site SPAs.

    Sets ``X-CSRF-TOKEN`` to the CSRF claim from the freshly issued JWT and
    ensures the browser can read it via ``Access-Control-Expose-Headers``.
    The JWT itself stays in the HttpOnly cookie.

    Only emitted when Flask-JWT-Extended's double-submit CSRF protection is
    enabled (``JWT_COOKIE_CSRF_PROTECT`` is truthy). Under the origin-only
    fallback (``JWT_COOKIE_CSRF_PROTECT=False``) the access JWT has no ``csrf``
    claim, so there is nothing to expose and the header is left unset.
    """
    from flask import current_app
    from flask_jwt_extended import get_csrf_token

    if not current_app.config.get("JWT_COOKIE_CSRF_PROTECT", True):
        return
    csrf_value = get_csrf_token(encoded_token)
    _set_csrf_value_header(response, csrf_value)


def _set_csrf_value_header(response, csrf_value):
    """Expose a known CSRF claim without requiring access to the encoded JWT."""
    response.headers[CSRF_HEADER_NAME] = csrf_value
    _expose_header(response, CSRF_HEADER_NAME)


def _expose_header(response, header_name):
    """Add ``header_name`` to ``Access-Control-Expose-Headers`` idempotently.

    This works regardless of which CORS middleware (if any) the application
    uses, because it mutates the response object directly.
    """
    existing = response.headers.get("Access-Control-Expose-Headers", "")
    names = {name.strip() for name in existing.split(",") if name.strip()}
    names.add(header_name)
    response.headers["Access-Control-Expose-Headers"] = ", ".join(sorted(names))


def clear_token_response(payload=None, status=200):
    """Create a response that clears JWT access cookies."""
    from flask import current_app, jsonify
    from flask_jwt_extended import unset_jwt_cookies

    response = jsonify(payload or {"status": "success", "message": "Logged out"})
    unset_jwt_cookies(response)
    if _resolve_partitioned(current_app):
        _make_cookies_partitioned(response)
    return response, status


def clear_session_token_value(token_record):
    """Invalidate a session token while preserving its registered-device row."""
    from .tokens import clear_session_token_value as _impl

    _impl(token_record)
