# flask-session.manager.sk

Flask companion package for [react-session.manager.sk](https://github.com/Skulldorom/react-session.manager.sk).

## Install

```bash
pip install flask-session.manager.sk
```

## Quick Start

```python
from flask import Flask
from flask_jwt_extended import create_access_token
from flask_session_manager_sk import SessionManager, SessionManagerCallbacks

app = Flask(__name__)
app.config["SECRET_KEY"] = "..."
app.config["JWT_TOKEN_LOCATION"] = ["cookies"]
app.config["JWT_COOKIE_CSRF_PROTECT"] = True
app.config["FRONTEND_URL"] = "http://localhost:5173"
app.config["CORS_ORIGINS"] = ["http://localhost:5173"]

manager = SessionManager()

callbacks = SessionManagerCallbacks(
    user_lookup=lambda identity: get_user_by_id(identity),
    refresh_user_token=lambda user, agent, device_uid: create_and_store_token(
        user, agent, device_uid
    ),
)

manager.init_app(app, callbacks=callbacks)
```

## License

MIT
