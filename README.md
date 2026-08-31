# flask-session.manager.sk

[![Version](https://img.shields.io/github/v/tag/Skulldorom/flask-session.manager.sk)](https://github.com/Skulldorom/flask-session.manager.sk/tags)
[![Python](https://img.shields.io/pypi/pyversions/flask-session.manager.sk)](https://pypi.org/project/flask-session.manager.sk/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](./LICENSE)

<p align="center">
  <a href="https://ko-fi.com/skulldorom"><img src="https://ko-fi.com/img/githubbutton_sm.svg" alt="Support me on Ko-fi" /></a>
</p>

Flask companion package for
[react-session.manager.sk](https://github.com/Skulldorom/react-session.manager.sk).

Cookie-driven JWT session management for Flask backends, designed to pair with the
React session manager's HttpOnly-cookie transport.

## Install

```bash
# uv (recommended)
uv add flask-session.manager.sk

# pip
pip install flask-session.manager.sk
```

## Quick Start

```python
from flask import Flask, jsonify
from flask_jwt_extended import create_access_token
from flask_session_manager_sk import (
    SessionManager,
    SessionManagerCallbacks,
    verify_session_token_record,
)

app = Flask(__name__)
app.config.update(
    {
        "SECRET_KEY": "your-secret-key",
        "JWT_TOKEN_LOCATION": ["cookies"],
        "JWT_COOKIE_SECURE": True,  # require HTTPS in production
        "JWT_COOKIE_CSRF_PROTECT": True,
        "JWT_ACCESS_COOKIE_NAME": "access_token_cookie",
        "FRONTEND_URL": "https://myapp.example.com",
        "CORS_ORIGINS": ["https://myapp.example.com"],
    }
)

manager = SessionManager()


def verify_user_token(user, agent, device_uid, token):
    record = user.find_session_record(agent=agent, device_uid=device_uid)
    if verify_session_token_record(token, record):
        return record
    return None


callbacks = SessionManagerCallbacks(
    user_lookup=lambda identity: get_user_by_id(identity),
    refresh_user_token=lambda user, agent, device_uid: create_and_store_token(
        user, agent, device_uid
    ),
    verify_user_token=verify_user_token,
    is_user_active=lambda user: user.is_active,
    is_session_persistent=lambda user, agent, device_uid, record=None: bool(
        record and record.persistent
    ),
)

manager.init_app(app, callbacks=callbacks)


@app.route("/auth/who")
@jwt_required(optional=True)
def whoami():
    from flask_jwt_extended import current_user

    if current_user:
        return jsonify(logged_in=True, user_id=current_user.id)
    return jsonify(logged_in=False)
```

`SessionManager.init_app()` automatically registers the package CSRF/origin guard
for cookie authentication. Browser unsafe requests using cookies must come from a
configured `FRONTEND_URL` or `CORS_ORIGINS` origin unless you deliberately opt out
with `FSM_CSRF_ORIGIN_CHECK=False`.

## Public API

Only these names are part of the stable surface:

| Name | Description |
|------|-------------|
| `SessionManager` | Flask extension that wires JWT callbacks |
| `SessionManagerCallbacks` | Frozen dataclass of application hooks |
| `token_response` | Create a JSON response with an HttpOnly JWT cookie |
| `clear_token_response` | Create a response that clears JWT cookies |
| `verify_session_token_record` | Verify a presented JWT against a server-side token record |

Everything else in the package is internal and may change without notice.

```python
import flask_session_manager_sk

print(flask_session_manager_sk.__version__)  # e.g. "1.0.0"
```

### SessionManagerCallbacks

```python
@dataclass(frozen=True)
class SessionManagerCallbacks:
    # Required
    user_lookup: Callable[[str], Any | None]
    refresh_user_token: Callable[[Any, str, str | None], str | None]

    # Optional
    verify_user_token: (
        Callable[[Any, str | None, str | None, str | None], Any | None] | None
    ) = None
    is_user_active: Callable[[Any], bool] | None = None
    is_session_persistent: (
        Callable[[Any, str | None, str | None, Any | None], bool] | None
    ) = None
```

`verify_user_token` receives `(user, agent, device_uid, token)`. Agent and device
UID are identifiers/signals only. They are not secrets and must never be enough
to authenticate a session by themselves. Resolve the relevant server-side session
record with metadata, then compare the presented JWT against the record's stored
hash with `verify_session_token_record()` or equivalent constant-time logic.

`is_session_persistent` receives `(user, agent, device_uid, token_record)` during
expired-token refresh. Return `True` only when the remembered-session policy is
still valid for that server-side record. This lets the package reissue persistent
cookies without owning your database schema or lifetime policy.

## Flask Configuration Reference

These values are read at runtime via `current_app.config`. No configuration is
stored in the package.

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `SECRET_KEY` | Yes | - | Flask secret, must be >=32 bytes for HMAC-SHA256 |
| `JWT_TOKEN_LOCATION` | Yes | - | Include `"cookies"` for browser cookie auth; bearer-only clients can use `"headers"` |
| `JWT_ACCESS_COOKIE_NAME` | Yes | - | Cookie name; `react-session.manager.sk` expects `"access_token_cookie"` |
| `JWT_COOKIE_CSRF_PROTECT` | Yes | - | Must be `True` for browser cookie clients unless you deliberately disable package origin checks |
| `JWT_COOKIE_SECURE` | Yes | - | `True` in production and always required when `JWT_COOKIE_SAMESITE="None"` |
| `JWT_COOKIE_SAMESITE` | No | - | `"Lax"` or `"Strict"`; use `"None"` only with `Secure=True` for cross-site cookies |
| `FSM_CSRF_ORIGIN_CHECK` | No | `True` | Automatically reject unsafe cookie-auth requests from unconfigured or missing origins. Set `False` only for deliberate, security-reviewed opt-out. |
| `FSM_PERSISTENT_MAX_AGE` | For `persistent=True` | - | `timedelta` or int seconds for remembered-session cookie `Max-Age` |
| `FSM_COOKIE_PARTITIONED` | No | `False` | Append `; Partitioned` (CHIPS) to all auth cookies. Requires `JWT_COOKIE_SAMESITE="None"` and `JWT_COOKIE_SECURE=True`. Helps cross-site cookie auth on supported iOS WebKit/Safari ITP and other third-party-cookie blockers. |
| `FRONTEND_URL` | For cookie origin checks | - | Canonical URL of the SPA |
| `CORS_ORIGINS` | For cookie origin checks | - | Allowed browser origins as a string or list |

When cookie auth and `FSM_CSRF_ORIGIN_CHECK=True` are enabled, initialization
validates the security-sensitive combinations above and raises `RuntimeError`
with an actionable message if the configuration is unsafe or missing required
browser origins. Bearer-only configurations are not blocked by these cookie
checks.

## CSRF and Origin Policy

Flask-JWT-Extended's double-submit CSRF token and this package's origin check are
complementary defenses:

- safe methods (`GET`, `HEAD`, `OPTIONS`) are not blocked by the package origin guard
- unsafe methods (`POST`, `PUT`, `PATCH`, `DELETE`) using cookie auth require an
  allowed `Origin` or `Referer`
- if both `Origin` and `Referer` are missing on an unsafe cookie-auth request,
  the guard fails closed with `403`
- bearer-authenticated requests skip the browser cookie origin guard
- if both a cookie and a bearer authorization header are present, bearer mode
  wins for the package origin guard

## Session Lifetime and Remember Me

JWT expiry, browser cookie lifetime, and remembered-session lifetime are separate
concepts:

- `JWT_ACCESS_TOKEN_EXPIRES` controls how long an individual JWT is valid.
- Session cookies (`persistent=False`) are browser-session cookies and do not
  receive `Max-Age`.
- Remembered cookies (`persistent=True`) receive `Max-Age` from
  `FSM_PERSISTENT_MAX_AGE` and can survive browser restarts.
- Expired cookie JWTs are refreshed through replacement HttpOnly cookies. The
  refresh response is signal-only (`{"refreshed": true}`) and does not expose a
  new raw access token to JavaScript.
- `is_session_persistent(...)` decides whether refresh should preserve persistent
  cookie attributes for a remembered session.
- Absolute versus sliding remembered-session lifetime is application policy. Store
  fields such as `created_at`, `expires_at`, `last_seen`, or `revoked_at` on your
  own session record and return `False` from callbacks when the remembered
  session is no longer valid.
- Logout, revocation, inactive users, and failed token-hash verification override
  persistence and must reject refresh.

## Token Verification and Revocation

Use token hashes as the proof of possession. Device UID, User-Agent, IP address,
and similar metadata are useful for finding a candidate session record, but they
are not authentication secrets.

Recommended consumer pattern:

```python
def verify_user_token(user, agent, device_uid, token):
    record = user.find_session_record(agent=agent, device_uid=device_uid)
    if verify_session_token_record(token, record):
        return record
    return None
```

`verify_token_hash()` uses a constant-time digest comparison and fails closed for
missing values. `clear_session_token_value()` preserves the registered-device row
while clearing active token state (`token_hash = None`, empty hint, no plaintext
token). Legacy rows containing the old SHA-256 hash of an empty string are treated
as revoked and never verify.

## Cross-Site Cookies and iOS WebKit (CHIPS)

Browser auth uses an HttpOnly cookie. When the SPA and the API live on different
registrable domains (e.g. `app.example.com` to `api.herokuapp.com`), the cookie
is a third-party cookie. Desktop Chromium accepts `SameSite=None; Secure`
third-party cookies, but iOS WebKit (Safari and every iOS browser, including
Brave, which is WebKit under the hood) blocks them by default via Intelligent
Tracking Prevention. Result: login succeeds, the browser never stores the cookie,
and the next request 401s.

Set `FSM_COOKIE_PARTITIONED=True` to emit `; Partitioned` (CHIPS) on all auth
cookies. Partitioned cookies are stored in a per-top-level-site partition, so
WebKit keeps them even with third-party cookies blocked. Requirements:

- `JWT_COOKIE_SAMESITE="None"` and `JWT_COOKIE_SECURE=True` (enforced; a
  misconfigured combination raises `RuntimeError` at cookie-set time).
- Browser support: iOS 16.4+ / Safari 16.4+, Chrome 114+. Older iOS remains
  affected; the durable fix is serving the API from the same site as the SPA.

## React Companion Contract

The frontend companion
[react-session.manager.sk](https://github.com/Skulldorom/react-session.manager.sk)
(v4.0+) uses this transport contract:

- **Axios** configured with `withCredentials: true`, `withXSRFToken: true`
- Cookies expected at default Flask-JWT-Extended names:
  - `access_token_cookie` (JWT)
  - `csrf_access_token` (CSRF double-submit)
- CSRF header sent as `X-CSRF-TOKEN`
- `deviceUID` header sent on every request
- `appVersion` header sent on every request
- No `Authorization` bearer header for browser requests
- Legacy `localStorage`/`sessionStorage` bearer tokens are automatically cleared

## Internal Modules

Not part of the stable API. Import at your own risk.

| Module | Content |
|--------|---------|
| `flask_session_manager_sk.cookies` | CSRF origin checks, cookie auth detection, `clear_session_token_value` |
| `flask_session_manager_sk.request` | `get_agent`, `get_ip`, `get_token`, `get_dets_from_request` |
| `flask_session_manager_sk.tokens` | `create_token_hash`, `token_hint`, `verify_token_hash`, `verify_session_token_record`, `update_token_record` |
| `flask_session_manager_sk.extension` | `SessionManager` implementation details |

## Migrating from Bearer Tokens

If your backend currently returns access tokens as JSON payloads (e.g.
`{"access_token": "..."}`) and stores them in `localStorage`:

1. **Package adoption**: Wire `SessionManager` with your user-lookup,
   token-refresh, token-verification, and optional persistence callbacks.
2. **Login endpoint**: Call `token_response(payload, status, access_token)`
   instead of `jsonify(payload)`. This sets the HttpOnly cookie automatically.
3. **CSRF/origin checks**: Remove custom boilerplate that manually registers
   `reject_cookie_csrf()` as a `before_request` hook. `SessionManager.init_app()`
   registers it automatically for cookie auth by default.
4. **Token verification**: Make sure your `verify_user_token` callback compares
   the presented JWT against the stored token hash. Matching device metadata
   alone is insecure.
5. **Frontend**: Upgrade `react-session.manager.sk` to v4.0+. It removes
   bearer-token browser storage and sends cookie credentials automatically.
6. **Backwards compatibility**: Non-browser clients (API scripts, scheduled
   tasks) can still send valid bearer authorization when `JWT_TOKEN_LOCATION`
   includes `"headers"`. The package origin guard skips bearer-authenticated
   requests. Expired bearer tokens are not auto-refreshed by this package because
   refreshed JWTs are only issued through HttpOnly cookies; bearer clients should
   use the application's explicit token-renewal flow.

## Development

```bash
git clone https://github.com/Skulldorom/flask-session.manager.sk.git
cd flask-session.manager.sk

# Install deps + editable package
uv sync --dev

# Run checks
uv run ruff check .
uv run ruff format --check .
uv run pytest -v

# Build
uv build
```

## License

MIT - see [LICENSE](./LICENSE).

<!-- test auto-versioning pipeline -->
