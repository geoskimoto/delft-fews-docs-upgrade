from pathlib import Path

import pytest

from chat.corpus import build_corpus, page_url

BASE = "https://example.org"


@pytest.fixture
def docs(tmp_path):
    d = tmp_path / "docs"
    (d / "tasks").mkdir(parents=True)
    (d / "reference").mkdir(parents=True)
    (d / "index.mdx").write_text(
        "---\ntitle: Home\n---\n\nWelcome to the guide.\n"
    )
    (d / "tasks" / "locations.mdx").write_text(
        "---\ntitle: Define locations\ndescription: d\nsidebar:\n  order: 1\n---\n\n"
        "import { Aside } from '@astrojs/starlight/components';\n\n"
        "Locations are the where.\n"
    )
    (d / "reference" / "idMap.mdx").write_text(
        "---\ntitle: ID map file\n---\n\n"
        "import FieldReference from '../../../components/FieldReference.astro';\n"
        "import data from '../../../data/schema/idMap.json';\n\n"
        "Prose about ID mapping.\n\n"
        "<FieldReference data={data} />\n"
    )
    (d / "plain.md").write_text("---\ntitle: Plain\n---\n\nPlain body.\n")
    return d


def test_page_url_maps_nested_page(docs):
    assert page_url(docs / "tasks" / "locations.mdx", docs, BASE) == (
        f"{BASE}/tasks/locations/"
    )


def test_page_url_maps_index_to_site_root(docs):
    assert page_url(docs / "index.mdx", docs, BASE) == f"{BASE}/"


def test_corpus_includes_title_and_url_for_each_page(docs):
    out = build_corpus(docs, BASE)
    assert "=== Define locations ===" in out
    assert f"{BASE}/tasks/locations/" in out


def test_corpus_strips_frontmatter(docs):
    out = build_corpus(docs, BASE)
    assert "sidebar:" not in out
    assert "order: 1" not in out


def test_corpus_strips_import_lines(docs):
    out = build_corpus(docs, BASE)
    assert "@astrojs/starlight/components" not in out
    assert "FieldReference.astro" not in out


def test_corpus_keeps_body_text(docs):
    out = build_corpus(docs, BASE)
    assert "Locations are the where." in out
    assert "Plain body." in out


def test_prose_line_starting_with_the_word_import_survives(docs):
    """generalAdapterRun.mdx hard-wraps sentences onto lines that begin
    'import cycle in full.' and 'import results (...)'. A pattern matching any
    line starting with 'import ' deletes real documentation with no error."""
    (docs / "tasks" / "adapter.mdx").write_text(
        "---\ntitle: Adapter\n---\n\n"
        "import { Aside } from '@astrojs/starlight/components';\n\n"
        "The General Adapter runs the export, run and\n"
        "import cycle in full. This page explains it.\n"
    )
    out = build_corpus(docs, BASE)
    assert "import cycle in full." in out
    assert "@astrojs/starlight/components" not in out


def test_multiline_prose_mentioning_import_from_survives(docs):
    """Prose can legitimately contain the words 'import' and 'from' on one
    line; only a real module specifier in quotes makes it an import."""
    (docs / "tasks" / "prose.mdx").write_text(
        "---\ntitle: Prose\n---\n\n"
        "import results from the upstream model are written to disk.\n"
    )
    out = build_corpus(docs, BASE)
    assert "import results from the upstream model" in out


def test_field_reference_becomes_a_tool_pointer(docs):
    out = build_corpus(docs, BASE)
    assert "<FieldReference" not in out
    assert 'lookup_config_fields with config_file="idMap"' in out


def test_corpus_is_byte_stable_across_calls(docs):
    assert build_corpus(docs, BASE) == build_corpus(docs, BASE)


def test_corpus_orders_pages_deterministically(docs):
    out = build_corpus(docs, BASE)
    assert out.index("=== Home ===") < out.index("=== Plain ===")


def test_real_corpus_builds_and_is_substantial():
    from chat import config

    out = build_corpus(config.DOCS_DIR, BASE)
    assert len(out) > 200_000
    assert "=== " in out
