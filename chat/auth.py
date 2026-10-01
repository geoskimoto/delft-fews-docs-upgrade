"""Authentication for the chat endpoints.

Deliberately does NOT use streamflows_auth.protect_app(). That helper exempts
every path beginning with /api/ (see _EXEMPT_PREFIXES in its middleware), so
applying it here would leave these endpoints open to the internet with an
Anthropic API key behind them. It also redirects to an HTML login page, which a
fetch() caller cannot act on.

JWT verification itself still goes through streamflows_auth.tokens.decode_token
so there is only one copy of that logic.
"""
import logging
from functools import wraps

import jwt
from flask import g, jsonify, request
from streamflows_auth.tokens import decode_token

from chat.identity import storage_key

COOKIE_NAME = "streamflows_auth"
REQUIRED_GROUP = "streamflow"
ADMIN_GROUP = "admin"

audit = logging.getLogger("chat.audit")


def _authenticate(allowed_groups: set):
    """Return (user, groups, None) or (None, groups, error_response)."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None, set(), (jsonify({"error": "not_authenticated"}), 401)
    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError:
        return None, set(), (jsonify({"error": "session_expired"}), 401)
    except jwt.InvalidTokenError:
        return None, set(), (jsonify({"error": "not_authenticated"}), 401)

    # Never let an authorization decision depend on the claim's SHAPE.
    # `in` substring-matches on strings, so a scalar claim of
    # "streamflow-readonly" or "administrative" would sail past a naive
    # membership test, and a non-iterable claim would raise TypeError into
    # a 500. Accept only a list, and only its string elements.
    raw = payload.get("groups")
    groups = {g for g in raw if isinstance(g, str)} if isinstance(raw, list) else set()

    # The rate limiter keys on this. Defaulting a missing or blank sub to ""
    # would drop every such caller into one shared bucket, so one noisy
    # token could lock out others. A token with no subject identifies
    # nobody and should not authorize anything.
    user = payload.get("sub")
    if not isinstance(user, str) or not user.strip():
        return None, groups, (jsonify({"error": "not_authenticated"}), 401)

    if not groups & allowed_groups:
        return user, groups, (jsonify({"error": "not_authorized"}), 403)
    return user, groups, None


def require_streamflows_user(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user, groups, error = _authenticate({REQUIRED_GROUP, ADMIN_GROUP})
        if error:
            return error
        g.current_user = user
        g.is_admin = ADMIN_GROUP in groups
        return view(*args, **kwargs)

    return wrapped


def require_admin(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user, groups, error = _authenticate({ADMIN_GROUP})
        if error:
            if error[1] == 403:
                # Opaque id only: the subject is an email address.
                audit.warning("admin access denied actor=%s path=%s",
                              storage_key(user), request.path)
            return error
        g.current_user = user
        g.is_admin = True
        return view(*args, **kwargs)

    return wrapped
