"""Cookie and CSRF helpers for Flask JWT session management."""


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
