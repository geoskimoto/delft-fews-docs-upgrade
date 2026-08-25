import json

import pytest

from chat import config
from chat.schema_tool import render_fields, schema_names, tool_definition


@pytest.fixture
def schema_dir(tmp_path):
    d = tmp_path / "schema"
    d.mkdir()
    (d / "locations.json").write_text(json.dumps({
        "element": "locations",
        "schemaFile": "locations.xsd",
        "schemaUrl": "https://example/locations.xsd",
        "types": {
            "LocationComplexType": {
                "doc": "A single location.",
                "attributes": [
                    {"name": "id", "type": "string", "use": "required",
                     "doc": "Unique location id."}
                ],
                "fields": [
                    {"name": "shortName", "type": "string", "required": True,
                     "repeatable": False, "kind": "scalar",
                     "doc": "Display name."},
                    {"name": "type", "type": "string", "required": False,
                     "repeatable": False, "kind": "enum",
                     "enumValues": ["gauge", "reservoir"]},
                ],
            }
        },
    }))
    (d / "filters.json").write_text(json.dumps({"types": {}}))
    return d


def test_schema_names_are_sorted(schema_dir):
    assert schema_names(schema_dir) == ["filters", "locations"]


def test_schema_names_are_byte_stable(schema_dir):
    assert schema_names(schema_dir) == schema_names(schema_dir)


def test_tool_definition_pins_argument_to_enum(schema_dir):
    d = tool_definition(schema_dir)
    assert d["name"] == "lookup_config_fields"
    assert d["strict"] is True
    props = d["input_schema"]["properties"]
    assert props["config_file"]["enum"] == ["filters", "locations"]
    assert d["input_schema"]["additionalProperties"] is False
    assert d["input_schema"]["required"] == ["config_file"]


def test_render_includes_type_doc_attributes_and_fields(schema_dir):
    out = render_fields(schema_dir, "locations")
    assert "LocationComplexType" in out
    assert "A single location." in out
    assert "@id" in out
    assert "Unique location id." in out
    assert "shortName" in out
    assert "required" in out


def test_render_includes_enum_values(schema_dir):
    out = render_fields(schema_dir, "locations")
    assert "gauge" in out and "reservoir" in out


def test_render_rejects_unknown_config_file(schema_dir):
    out = render_fields(schema_dir, "does_not_exist")
    assert "Unknown config file" in out


def test_render_rejects_path_traversal(schema_dir):
    out = render_fields(schema_dir, "../../../etc/passwd")
    assert "Unknown config file" in out


def test_field_or_attribute_without_a_name_renders_instead_of_raising(schema_dir):
    """render_fields must always hand the agent a readable string. If the XSD
    generator ever emits a field with no 'name', a KeyError would escape as an
    unhandled exception in the request path instead of a recoverable result."""
    (schema_dir / "odd.json").write_text(json.dumps({
        "types": {
            "OddType": {
                "attributes": [{"type": "string", "doc": "nameless attribute"}],
                "fields": [{"type": "string", "required": True}],
            }
        },
    }))
    out = render_fields(schema_dir, "odd")
    assert "OddType" in out
    assert "nameless attribute" in out


def test_every_real_schema_renders_without_raising():
    names = schema_names(config.SCHEMA_DIR)
    assert len(names) == 33
    for name in names:
        out = render_fields(config.SCHEMA_DIR, name)
        assert out
        assert "Unknown config file" not in out
