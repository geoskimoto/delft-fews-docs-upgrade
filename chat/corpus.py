"""Build the documentation corpus that rides in the agent's system prompt.

Read once at service startup. Byte stability matters: this string sits behind a
prompt-cache breakpoint, so any reordering silently invalidates the cache and
turns every cached read into a full-price cache write.
"""
import re
from pathlib import Path

_FRONTMATTER = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n", re.DOTALL)
_TITLE = re.compile(r"^title:\s*(.+?)\s*$", re.MULTILINE)
# Requires real import syntax (`... from '...'`). A looser pattern that matched
# any line starting with "import " silently ate prose — generalAdapterRun.mdx
# hard-wraps sentences onto lines beginning "import cycle in full." and
# "import results (...)", and those disappeared from the corpus with no error.
_IMPORT_LINE = re.compile(
    r"^import\s+.+?\s+from\s+['\"][^'\"]+['\"]\s*;?[ \t]*$\n?", re.MULTILINE
)
_SCHEMA_IMPORT = re.compile(r"^import\s+data\s+from\s+.*?/schema/([\w-]+)\.json.*$", re.MULTILINE)
_FIELD_REF_TAG = re.compile(r"^<FieldReference\b[^>]*/>\s*$", re.MULTILINE)


def _read_title(raw: str, fallback: str) -> str:
    head = _FRONTMATTER.match(raw)
    if head:
        found = _TITLE.search(head.group(0))
        if found:
            return found.group(1).strip().strip("\"'")
    return fallback


def page_url(path: Path, docs_dir: Path, base_url: str) -> str:
    """Map a content file to its published Starlight URL."""
    rel = path.relative_to(docs_dir).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "index":
        parts.pop()
    slug = "/".join(parts)
    base = base_url.rstrip("/")
    return f"{base}/{slug}/" if slug else f"{base}/"


def _strip(raw: str) -> tuple[str, str | None]:
    """Return (body, schema_name). schema_name is set on reference pages."""
    schema_match = _SCHEMA_IMPORT.search(raw)
    schema_name = schema_match.group(1) if schema_match else None

    body = _FRONTMATTER.sub("", raw)
    body = _IMPORT_LINE.sub("", body)

    if schema_name:
        pointer = (
            f"[The complete field and attribute table for this file is NOT shown "
            f"here. Call lookup_config_fields with config_file=\"{schema_name}\" "
            f"to retrieve it.]"
        )
        body = _FIELD_REF_TAG.sub(pointer, body)

    return body.strip(), schema_name


def build_corpus(docs_dir: Path, base_url: str) -> str:
    """Concatenate every documentation page into one cache-stable string."""
    paths = sorted(
        (p for p in docs_dir.rglob("*") if p.suffix in (".md", ".mdx")),
        key=lambda p: str(p.relative_to(docs_dir)),
    )
    chunks = []
    for path in paths:
        raw = path.read_text(encoding="utf-8")
        title = _read_title(raw, path.stem)
        body, _ = _strip(raw)
        chunks.append(
            f"=== {title} ===\nURL: {page_url(path, docs_dir, base_url)}\n\n{body}"
        )
    return "\n\n".join(chunks) + "\n"
