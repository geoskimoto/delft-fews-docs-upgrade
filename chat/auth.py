"""Authentication for the chat endpoints.

Deliberately does NOT use streamflows_auth.protect_app(). That helper exempts
every path beginning with /api/ (see _EXEMPT_PREFIXES in its middleware), so
applying it here would leave these endpoints open to the internet with an
Anthropic API key behind them. It also redirects to an HTML login page, which a
fetch() caller cannot act on.

JWT verification itself still goes through streamflows_auth.tokens.decode_token
so there is only one copy of that logic.
"""
from functools import wraps

import jwt
from flask import g, jsonify, request
from streamflows_auth.tokens import decode_token

COOKIE_NAME = "streamflows_auth"
REQUIRED_GROUP = "streamflow"
ADMIN_GROUP = "admin"


def require_streamflows_user(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        token = request.cookies.get(COOKIE_NAME)
        if not token:
            return jsonify({"error": "not_authenticated"}), 401
        try:
            payload = decode_token(token)
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "session_expired"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "not_authenticated"}), 401

        # Never let an authorization decision depend on the claim's SHAPE.
        # `in` substring-matches on strings, so a scalar claim of
        # "streamflow-readonly" or "administrative" would sail past a naive
        # membership test, and a non-iterable claim would raise TypeError into
        # a 500. Accept only a list, and only its string elements.
        raw = payload.get("groups")
        groups = {g for g in raw if isinstance(g, str)} if isinstance(raw, list) else set()
        if not groups & {REQUIRED_GROUP, ADMIN_GROUP}:
            return jsonify({"error": "not_authorized"}), 403

        g.current_user = payload.get("sub", "")
        return view(*args, **kwargs)

    return wrapped
