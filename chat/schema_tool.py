"""The lookup_config_fields tool.

The documentation Markdown does not contain field tables — reference pages carry
a <FieldReference> component and the tables are injected at build time from
src/data/schema/*.json. Inlining all of that would add ~351k tokens to the
corpus, so the agent pulls one file at a time through this tool instead.
"""
import json
from pathlib import Path

TOOL_NAME = "lookup_config_fields"

_DESCRIPTION = (
    "Look up the complete field and attribute reference for one Delft-FEWS "
    "configuration file, generated directly from its XSD schema. Call this "
    "whenever the user asks which fields, attributes, elements, child types, or "
    "enum values a config file supports, or whether a particular field exists. "
    "The documentation pages in your context explain concepts but do NOT contain "
    "these tables — this tool is the only way to see them."
)


def schema_names(schema_dir: Path) -> list[str]:
    """Sorted schema stems. Sorted because this list renders ahead of the
    cached system prompt; an unstable order invalidates the corpus cache."""
    return sorted(p.stem for p in schema_dir.glob("*.json"))


def tool_definition(schema_dir: Path) -> dict:
    return {
        "name": TOOL_NAME,
        "description": _DESCRIPTION,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "config_file": {
                    "type": "string",
                    "description": "Which configuration file to look up.",
                    "enum": schema_names(schema_dir),
                }
            },
            "required": ["config_file"],
            "additionalProperties": False,
        },
    }


def _render_attribute(attr: dict) -> str:
    bits = [f"@{attr['name']}"]
    if attr.get("type"):
        bits.append(f"({attr['type']})")
    if attr.get("use"):
        bits.append(attr["use"])
    if attr.get("fixed"):
        bits.append(f"fixed={attr['fixed']}")
    if attr.get("default"):
        bits.append(f"default={attr['default']}")
    if attr.get("doc"):
        bits.append("— " + " ".join(attr["doc"].split()))
    return "  " + " ".join(bits)


def _render_field(field: dict) -> str:
    bits = [field["name"]]
    if field.get("type"):
        bits.append(f"({field['type']})")
    bits.append("required" if field.get("required") else "optional")
    if field.get("repeatable"):
        bits.append("repeatable")
    if field.get("enumValues"):
        bits.append("one of: " + ", ".join(field["enumValues"]))
    if field.get("doc"):
        bits.append("— " + " ".join(field["doc"].split()))
    return "  " + " ".join(bits)


def render_fields(schema_dir: Path, config_file: str) -> str:
    """Render one schema to compact text, or an error string the agent can read."""
    if config_file not in schema_names(schema_dir):
        return (
            f"Unknown config file {config_file!r}. Valid values: "
            + ", ".join(schema_names(schema_dir))
        )

    data = json.loads((schema_dir / f"{config_file}.json").read_text(encoding="utf-8"))

    lines = [f"Field reference for {config_file} (from {data.get('schemaFile', '')})"]
    if data.get("element"):
        lines.append(f"Root element: <{data['element']}>")
    if data.get("doc"):
        lines.append(" ".join(data["doc"].split()))
    lines.append("")

    for type_name, node in data.get("types", {}).items():
        lines.append(f"## {type_name}")
        if node.get("doc"):
            lines.append("  " + " ".join(node["doc"].split()))
        for attr in node.get("attributes", []):
            lines.append(_render_attribute(attr))
        for field in node.get("fields", []):
            lines.append(_render_field(field))
        lines.append("")

    return "\n".join(lines).strip()
