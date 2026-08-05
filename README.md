# flask-session.manager.sk

[![PyPI version](https://img.shields.io/pypi/v/flask-session.manager.sk)](https://pypi.org/project/flask-session.manager.sk/)
[![Python](https://img.shields.io/pypi/pyversions/flask-session.manager.sk)](https://pypi.org/project/flask-session.manager.sk/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](./LICENSE)

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
from flask_session_manager_sk import SessionManager, SessionManagerCallbacks

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

callbacks = SessionManagerCallbacks(
    user_lookup=lambda identity: get_user_by_id(identity),
    refresh_user_token=lambda user, agent, device_uid: create_and_store_token(
        user, agent, device_uid
    ),
    verify_user_token=lambda user, agent, device_uid, token: user.check_token(
        agent, device_uid, token
    ),
    is_user_active=lambda user: user.is_active,
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

## Public API

Only four names are part of the stable surface:

| Name | Description |
|------|-------------|
| `SessionManager` | Flask extension that wires JWT callbacks |
| `SessionManagerCallbacks` | Frozen dataclass of application hooks |
| `token_response` | Create a JSON response with an HttpOnly JWT cookie |
| `clear_token_response` | Create a response that clears JWT cookies |

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
    verify_user_token: Callable[[Any, str | None, str | None], Any | None] | None = None
    is_user_active: Callable[[Any], bool] | None = None
```

## Flask Configuration Reference

These values are read at runtime via `current_app.config`. No configuration is
stored in the package.

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `SECRET_KEY` | Yes | — | Flask secret, must be ≥32 bytes for HMAC-SHA256 |
| `JWT_TOKEN_LOCATION` | Yes | — | Should include `"cookies"` |
| `JWT_ACCESS_COOKIE_NAME` | Yes | — | Cookie name; `react-session.manager.sk` expects `"access_token_cookie"` |
| `JWT_COOKIE_CSRF_PROTECT` | Yes | — | Must be `True` for browser clients |
| `JWT_COOKIE_SECURE` | Yes | — | `True` in production (HTTPS only) |
| `JWT_COOKIE_SAMESITE` | No | — | `"Lax"` or `"Strict"` |
| `FRONTEND_URL` | Yes | — | Canonical URL of the SPA |
| `CORS_ORIGINS` | Yes | — | List of allowed browser origins |

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
| `flask_session_manager_sk.tokens` | `create_token_hash`, `token_hint`, `verify_token_hash`, `update_token_record` |
| `flask_session_manager_sk.extension` | `SessionManager` implementation details |

## Migrating from Bearer Tokens

If your backend currently returns access tokens as JSON payloads (e.g.
`{"access_token": "..."}`) and stores them in `localStorage`:

1. **Package adoption**: Wire `SessionManager` with your user-lookup and
   token-refresh callbacks.
2. **Login endpoint**: Call `token_response(payload, status, access_token)`
   instead of `jsonify(payload)`. This sets the HttpOnly cookie automatically.
3. **CSRF check**: Add `reject_cookie_csrf()` as a `@app.before_request` hook
   for all unsafe methods (POST, PUT, DELETE).
4. **Frontend**: Upgrade `react-session.manager.sk` to v4.0+. It removes
   bearer-token browser storage and sends cookie credentials automatically.
5. **Backwards compatibility**: Non-browser clients (API scripts, scheduled
   tasks) can still send `Authorization: Bearer <token>`. The CSRF check
   skips bearer-authenticated requests.

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

MIT — see [LICENSE](./LICENSE).
