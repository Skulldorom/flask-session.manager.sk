"""Request helpers for extracting device/agent/token info."""


def get_agent(req):
    """Parse the User-Agent header into a human-readable device/browser name."""
    from ua_parser import user_agent_parser

    parsed = user_agent_parser.Parse(req.user_agent.to_header())
    os_family = parsed["os"]["family"]
    browser = parsed["user_agent"]["family"]
    return f"{os_family} {browser}"


def get_ip(req):
    """Return the client's remote IP address."""
    return req.remote_addr


def get_token(req):
    """Extract a JWT from the Authorization bearer header or access cookie."""
    from flask import current_app

    auth_header = req.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]

    cookie_name = current_app.config.get(
        "JWT_ACCESS_COOKIE_NAME", "access_token_cookie"
    )
    return req.cookies.get(cookie_name)


def get_dets_from_request(req):
    """Collect (agent_name, device_uid, token) from a Flask request."""
    return get_agent(req), req.headers.get("deviceUID"), get_token(req)
