"""Every tunable in one place. Changing model means changing its rates too."""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "src" / "content" / "docs"
SCHEMA_DIR = REPO_ROOT / "src" / "data" / "schema"
STATE_DIR = Path(os.environ.get("CHAT_STATE_DIR", REPO_ROOT / "chat" / "data"))

SITE_BASE_URL = os.environ.get("CHAT_SITE_BASE_URL", "https://df-docs.streamflows.org")
ALLOWED_ORIGIN = os.environ.get("CHAT_ALLOWED_ORIGIN", SITE_BASE_URL)
LOGIN_URL = os.environ.get("AUTH_LOGIN_URL", "https://apps.streamflows.org/login")

MODEL = "claude-sonnet-5"
EFFORT = "medium"
MAX_TOKENS = 8000

# USD per token. Must be updated together with MODEL.
# claude-sonnet-5 standing rate: $3/MTok input, $15/MTok output.
RATE_INPUT = 3.0 / 1_000_000
RATE_OUTPUT = 15.0 / 1_000_000
RATE_CACHE_WRITE = RATE_INPUT * 2.0   # 1-hour TTL costs 2x base input
RATE_CACHE_READ = RATE_INPUT * 0.1

DAILY_BUDGET_USD = float(os.environ.get("CHAT_DAILY_BUDGET_USD", "2.00"))
RATE_LIMIT_CALLS = 20
RATE_LIMIT_WINDOW_SECONDS = 300
MAX_HISTORY_TURNS = 12
MAX_HISTORY_BYTES = 24 * 1024
# The history byte cap deliberately never drops the final message, so without
# its own limit a single enormous question sails straight past it: 2 MB is
# ~500k uncached input tokens, about $1.50 — most of a day's budget in one
# request.
MAX_QUESTION_BYTES = 8 * 1024
# Flask leaves MAX_CONTENT_LENGTH unset, so the request body is unbounded until
# we say otherwise. Set generously above the history cap; Werkzeug rejects
# anything larger with a 413 before we parse it.
MAX_REQUEST_BYTES = 256 * 1024
MAX_TOOL_CALLS = 3
