"""Flask companion package for react-session.manager.sk.

Public API (stable):
    SessionManager               - Flask extension for JWT session management
    SessionManagerCallbacks      - frozen dataclass of callback hooks
    token_response               - create a response with an HttpOnly JWT cookie
    session_response             - expose an authenticated session's CSRF claim
    clear_token_response         - create a response that clears JWT cookies
    verify_session_token_record  - validate a presented JWT against a token record
"""

from importlib.metadata import version as _version

from .cookies import clear_token_response, session_response, token_response
from .extension import SessionManager, SessionManagerCallbacks
from .tokens import verify_session_token_record

__version__ = _version("flask-session.manager.sk")
__all__ = [
    "SessionManager",
    "SessionManagerCallbacks",
    "token_response",
    "session_response",
    "clear_token_response",
    "verify_session_token_record",
    "__version__",
]
