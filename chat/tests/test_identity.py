import os

os.environ.setdefault("JWT_SECRET", "test-secret-for-unit-tests")

from chat.identity import storage_key


def test_storage_key_is_stable_for_one_subject():
    assert storage_key("alice@example.com") == storage_key("alice@example.com")


def test_storage_key_differs_between_subjects():
    assert storage_key("alice@example.com") != storage_key("bob@example.com")


def test_storage_key_does_not_leak_the_subject():
    """The subject is an email address. The browser stores this value, so it
    must not be recoverable from it by inspection."""
    key = storage_key("alice@example.com")
    assert "alice" not in key
    assert "example" not in key
    assert key != "alice@example.com"


def test_storage_key_is_short_lowercase_hex():
    key = storage_key("alice@example.com")
    assert len(key) == 16
    assert all(c in "0123456789abcdef" for c in key)
