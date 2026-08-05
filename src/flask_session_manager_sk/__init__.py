"""Flask companion package for react-session.manager.sk.

Public API (stable):
    SessionManager          — Flask extension for JWT session management
    SessionManagerCallbacks — frozen dataclass of callback hooks
    token_response          — create a Flask response with an HttpOnly JWT cookie
    clear_token_response    — create a Flask response that clears JWT cookies
"""

from importlib.metadata import version as _version

from .cookies import clear_token_response, token_response
from .extension import SessionManager, SessionManagerCallbacks

__version__ = _version("flask-session.manager.sk")
__all__ = [
    "SessionManager",
    "SessionManagerCallbacks",
    "token_response",
    "clear_token_response",
    "__version__",
]
