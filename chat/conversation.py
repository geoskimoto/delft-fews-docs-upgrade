"""Validate and bound the client-supplied transcript.

The browser owns conversation state, so this input is untrusted: it is capped,
type-checked, and stripped of any 'system' role before it reaches the API.
"""
from chat import config

_ROLES = ("user", "assistant")


class InvalidHistory(Exception):
    """Raised for any payload that cannot be turned into a valid message list."""


def normalise(payload) -> list[dict]:
    if not isinstance(payload, dict):
        raise InvalidHistory("payload must be an object")

    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise InvalidHistory("messages must be a non-empty array")

    cleaned = []
    for item in messages:
        if not isinstance(item, dict):
            raise InvalidHistory("each message must be an object")
        role = item.get("role")
        content = item.get("content")
        if role not in _ROLES:
            raise InvalidHistory(f"unsupported role: {role!r}")
        if not isinstance(content, str):
            raise InvalidHistory("message content must be a string")
        cleaned.append({"role": role, "content": content})

    if cleaned[-1]["role"] != "user":
        raise InvalidHistory("the last message must be from the user")
    if not cleaned[-1]["content"].strip():
        raise InvalidHistory("the last message is empty")

    # The truncation loop below always keeps the final message, so it needs its
    # own ceiling or a single huge question bypasses the history cap entirely.
    question_bytes = len(cleaned[-1]["content"].encode("utf-8"))
    if question_bytes > config.MAX_QUESTION_BYTES:
        raise InvalidHistory(
            f"that question is {question_bytes} bytes; the limit is "
            f"{config.MAX_QUESTION_BYTES}"
        )

    cleaned = cleaned[-config.MAX_HISTORY_TURNS :]

    total = 0
    kept: list[dict] = []
    for item in reversed(cleaned):
        size = len(item["content"].encode("utf-8"))
        if kept and total + size > config.MAX_HISTORY_BYTES:
            break
        total += size
        kept.append(item)
    kept.reverse()

    while len(kept) > 1 and kept[0]["role"] != "user":
        kept.pop(0)

    return kept
