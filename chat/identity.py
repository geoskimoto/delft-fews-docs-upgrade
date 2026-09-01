"""Derive an opaque, stable per-user key for the browser's conversation store.

The browser needs to namespace stored conversations per user, but it cannot
read the httponly session cookie, so the server has to supply an identifier.
The `sub` claim is an email address and must not end up in localStorage, hence
a digest rather than the subject itself.
"""
import hashlib
import os


def storage_key(subject: str) -> str:
    """First 16 hex characters of sha256(JWT_SECRET + subject).

    Salted with the service secret so the value cannot be precomputed from a
    guessed address, and truncated because 64 bits is far more than enough to
    separate a handful of users inside one browser.
    """
    secret = os.environ["JWT_SECRET"]
    digest = hashlib.sha256((secret + subject).encode("utf-8")).hexdigest()
    return digest[:16]
