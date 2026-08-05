"""Flask extension for cookie-driven JWT session management."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SessionManagerCallbacks:
    """Callback hooks for the SessionManager extension.

    Required:
        user_lookup        — resolve an identity value to a user object (or None)
        refresh_user_token — create and return a new JWT for a user (or None)

    Optional:
        verify_user_token  — validate a token claim for a user (return record or None).
                             Called as fn(user, agent, device_uid, token).
        is_user_active     — check whether a user object is active (return bool)
    """

    user_lookup: Callable[[str], Any | None]
    refresh_user_token: Callable[[Any, str, str | None], str | None]
    verify_user_token: Callable[[Any, str | None, str | None, str | None], Any | None] | None = None
    is_user_active: Callable[[Any], bool] | None = None


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
        """Register JWT handlers on the given Flask app."""
        self.jwt.init_app(app)
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

                if callbacks.is_user_active is not None and (
                    not user or not callbacks.is_user_active(user)
                ):
                    return None

                from .request import get_dets_from_request

                agent, device_uid, token = get_dets_from_request(request)
                return callbacks.verify_user_token(user, agent, device_uid, token)

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
            if user is None:
                return (
                    jsonify(code="Invalid", msg="Invalid Token"),
                    455,
                )

            from .request import get_dets_from_request

            agent, device_uid, _ = get_dets_from_request(request)
            new_token = callbacks.refresh_user_token(user, agent, device_uid)
            if new_token:
                return (
                    jsonify(refreshed=True, access_token=new_token),
                    200,
                )
            return (
                jsonify(code="Invalid", msg="Invalid Token"),
                455,
            )
