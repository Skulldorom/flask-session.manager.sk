"""Flask extension for cookie-driven JWT session management."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SessionManagerCallbacks:
    """Callback hooks for the SessionManager extension.

    Required:
        user_lookup        - resolve an identity value to a user object (or None)
        refresh_user_token - create and return a new JWT for a user (or None)

    Optional:
        verify_user_token     - validate a token claim for a user.
                                Called as fn(user, agent, device_uid, token).
        is_user_active        - check whether a user object is active (return bool)
        is_session_persistent - decide whether an expired-token refresh should
                                reissue persistent cookies. Called as
                                fn(user, agent, device_uid, token_record).
    """

    user_lookup: Callable[[str], Any | None]
    refresh_user_token: Callable[[Any, str, str | None], str | None]
    verify_user_token: (
        Callable[[Any, str | None, str | None, str | None], Any | None] | None
    ) = None
    is_user_active: Callable[[Any], bool] | None = None
    is_session_persistent: (
        Callable[[Any, str | None, str | None, Any | None], bool] | None
    ) = None


class SessionManager:
    """Flask extension that wires JWT handlers with application callbacks.

    Usage::

        manager = SessionManager()
        manager.init_app(app, callbacks=SessionManagerCallbacks(...))
    """

    def __init__(self, app=None, callbacks=None):
        from flask_jwt_extended import JWTManager

        self.jwt = JWTManager()
        if app is not None:
            self.init_app(app, callbacks)

    def init_app(self, app, callbacks=None):
        """Register JWT handlers and safe cookie defaults on the given Flask app."""
        self.jwt.init_app(app)
        self._validate_cookie_config(app)
        self._register_cookie_csrf_guard(app)
        if callbacks is not None:
            self._register_callbacks(app, callbacks)

    def _register_callbacks(self, app, callbacks: SessionManagerCallbacks):
        """Wire up Flask-JWT-Extended handlers from the callback object."""
        from flask import jsonify, request

        @self.jwt.user_identity_loader
        def _identity_loader(user):
            return user

        @self.jwt.user_lookup_loader
        def _user_lookup(_jwt_header, jwt_data):
            identity = jwt_data["sub"]
            return callbacks.user_lookup(identity)

        # ---- token verification (optional) ----
        if callbacks.verify_user_token is not None:

            @self.jwt.token_verification_loader
            def _token_verification(_self, jwt_data):
                user = callbacks.user_lookup(jwt_data["sub"])

                if not self._user_can_authenticate(user, callbacks):
                    return False

                from .request import get_dets_from_request

                agent, device_uid, token = get_dets_from_request(request)
                return bool(callbacks.verify_user_token(user, agent, device_uid, token))

        # ---- verification failure ----
        @self.jwt.token_verification_failed_loader
        def _verification_failed(_self, _callback):
            return (
                jsonify(logged_in=False, refreshed=False, msg="Invalid Token"),
                455,
            )

        # ---- expired token refresh ----
        @self.jwt.expired_token_loader
        def _expired_token(jwt_header, jwt_payload):
            identity = jwt_payload["sub"]
            user = callbacks.user_lookup(identity)
            if not self._user_can_authenticate(user, callbacks):
                return self._invalid_token_response()

            from .cookies import (
                request_has_bearer_auth,
                token_response,
                validate_token_response_config,
            )
            from .request import get_dets_from_request

            if request_has_bearer_auth(request):
                return self._invalid_token_response()

            agent, device_uid, token = get_dets_from_request(request)
            token_record = None
            if callbacks.verify_user_token is not None:
                token_record = callbacks.verify_user_token(
                    user, agent, device_uid, token
                )
                if not token_record:
                    return self._invalid_token_response()

            persistent = False
            if callbacks.is_session_persistent is not None:
                persistent = bool(
                    callbacks.is_session_persistent(
                        user, agent, device_uid, token_record
                    )
                )

            # Validate cookie configuration before refresh_user_token is called;
            # consumers commonly persist the replacement token in that callback.
            validate_token_response_config(app, persistent=persistent)
            new_token = callbacks.refresh_user_token(user, agent, device_uid)
            if new_token:
                return token_response(
                    {"refreshed": True},
                    200,
                    access_token=new_token,
                    persistent=persistent,
                )
            return self._invalid_token_response()

    @staticmethod
    def _invalid_token_response():
        from flask import jsonify

        return (
            jsonify(code="Invalid", msg="Invalid Token"),
            455,
        )

    @staticmethod
    def _user_can_authenticate(user, callbacks):
        if user is None:
            return False
        return not (
            callbacks.is_user_active is not None and not callbacks.is_user_active(user)
        )

    def _register_cookie_csrf_guard(self, app):
        if not self._cookie_auth_enabled(app):
            return
        if app.config.get("FSM_CSRF_ORIGIN_CHECK", True) is False:
            return

        from .cookies import reject_cookie_csrf

        @app.before_request
        def _flask_session_manager_cookie_csrf_guard():
            return reject_cookie_csrf()

    def _validate_cookie_config(self, app):
        if not self._cookie_auth_enabled(app):
            return

        csrf_enabled = app.config.get("JWT_COOKIE_CSRF_PROTECT", True) is not False
        origin_check_enabled = (
            app.config.get("FSM_CSRF_ORIGIN_CHECK", True) is not False
        )

        if not csrf_enabled and not origin_check_enabled:
            raise RuntimeError(
                "Cookie authentication requires at least one of "
                "JWT_COOKIE_CSRF_PROTECT=True or FSM_CSRF_ORIGIN_CHECK=True. "
                "Disabling both protections is not permitted."
            )

        if not origin_check_enabled:
            return

        from .cookies import configured_browser_origins

        samesite = app.config.get("JWT_COOKIE_SAMESITE")
        secure = app.config.get("JWT_COOKIE_SECURE")
        if samesite == "None" and not secure:
            raise RuntimeError(
                "Cookie authentication with JWT_COOKIE_SAMESITE='None' requires "
                "JWT_COOKIE_SECURE=True."
            )

        if not configured_browser_origins(app):
            raise RuntimeError(
                "Cookie CSRF/origin protection requires at least one valid "
                "FRONTEND_URL or CORS_ORIGINS entry, or set "
                "FSM_CSRF_ORIGIN_CHECK=False for a deliberate opt-out."
            )

    @staticmethod
    def _cookie_auth_enabled(app):
        locations = app.config.get("JWT_TOKEN_LOCATION", [])
        if isinstance(locations, str):
            locations = [locations]
        return "cookies" in locations
