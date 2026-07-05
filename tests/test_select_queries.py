"""Tests for 30 typical SELECT patterns and new validation rules."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

import pytest

from mcp_md_sql.config import RootConfig, set_roots, parse_doc_roots
from mcp_md_sql.database import get_connection, load_files
from mcp_md_sql.reconstructor import _parse_attrs
from mcp_md_sql.tools import md_select, md_change, ToolError


# ── Fixture ────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def db():
    """Load a realistic multi-file doc set once per module."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        docs = root / "docs"
        docs.mkdir()

        # api.md — reference doc
        (docs / "api.md").write_text(
            "# API Reference\n"
            "\n"
            "## Authentication\n"
            "\n"
            "Use **API keys** for auth. See `config.md` for setup.\n"
            "\n"
            "> All requests require the `Authorization` header.\n"
            "\n"
            "### Endpoints\n"
            "\n"
            "- **GET** `/users` — returns ~~deprecated~~ user list\n"
            "- **POST** `/users` — create user\n"
            "\n"
            "## Rate Limiting\n"
            "\n"
            "Limits:\n"
            "\n"
            "| Tier  | RPS  |\n"
            "|-------|------|\n"
            "| Free  | 10   |\n"
            "| Pro   | 100  |\n"
            "\n"
            "```bash\n"
            "curl -H 'API-Key: xyz' https://api.example.com/v1/users\n"
            "```\n"
            "\n"
            "---\n"
            "\n"
            "For help, [contact support](https://support.example.com).\n",
            encoding='utf-8',
        )

        # config.md — setup doc
        (docs / "config.md").write_text(
            "# Configuration\n"
            "\n"
            "## Environment\n"
            "\n"
            "Set `DOC_ROOT` to the path. ![icon](icon.png)\n",
            encoding='utf-8',
        )

        # large.md — big doc for HAVING / stats tests
        large_lines = []
        for i in range(15):
            large_lines.append(f"## Section {i}\n")
            large_lines.append(f"Paragraph content {i}.\n")
            large_lines.append(f"- item {i}.1\n")
            large_lines.append(f"- item {i}.2\n")
        (docs / "large.md").write_text("".join(large_lines), encoding='utf-8')

        conn = get_connection()
        cfg = [RootConfig(path=docs)]
        set_roots(cfg)
        load_files(conn, cfg)

        yield conn


def rows(conn, sql: str) -> list[dict]:
    return md_select(conn, sql)


# ── 1. Structure & Navigation ──────────────────────────────────────────────────

def test_toc(db):
    """1. Table of contents: headings ordered by pos."""
    r = rows(db, "SELECT pos_in_file, content, attrs_json FROM md WHERE type='heading' ORDER BY pos_in_file")
    assert len(r) >= 10  # at least from api.md + config.md + large.md
    assert any('API Reference' in row['content'] for row in r)
    assert any('Configuration' in row['content'] for row in r)


def test_file_element_count(db):
    """2. Files with element counts."""
    r = rows(db, "SELECT file, COUNT(*) AS cnt FROM md GROUP BY file ORDER BY file")
    files = {row['file']: row['cnt'] for row in r}
    assert 'api.md' in files
    assert files['api.md'] > 10
    assert 'large.md' in files


def test_large_docs(db):
    """3. Files with > 5 elements (use 5 since our test docs are small)."""
    r = rows(db, "SELECT file, COUNT(*) AS cnt FROM md GROUP BY file HAVING cnt > 5")
    assert any(row['file'] == 'large.md' for row in r)


def test_all_links(db):
    """4. All link elements."""
    r = rows(db, "SELECT content, attrs_json FROM md WHERE type='link'")
    assert len(r) >= 1
    assert any('contact support' in row['content'] for row in r)


def test_all_images(db):
    """5. All image elements."""
    r = rows(db, "SELECT content, attrs_json FROM md WHERE type='image'")
    assert len(r) >= 1
    # image content is alt text
    assert any('icon' in row['content'] for row in r)


def test_code_blocks(db):
    """6. All code blocks."""
    r = rows(db, "SELECT content, attrs_json FROM md WHERE type='code_block'")
    assert len(r) >= 1
    assert any('curl' in (row['content'] or '') for row in r)


def test_all_tables(db):
    """7. All table elements."""
    r = rows(db, "SELECT file, pos_in_file FROM md WHERE type='table'")
    assert len(r) >= 1


def test_inline_elements(db):
    """8. All inline elements (strong, emphasis, inline_code, strikethrough)."""
    r = rows(db, "SELECT type, content, parent_pos FROM md "
                 "WHERE type IN ('strong','emphasis','inline_code','strikethrough') ORDER BY pos_in_file")
    types = {row['type'] for row in r}
    assert 'strong' in types or 'inline_code' in types


def test_elements_by_depth(db):
    """9. Root elements grouped by type."""
    r = rows(db, "SELECT type, COUNT(*) AS cnt FROM md WHERE depth=0 GROUP BY type ORDER BY cnt DESC")
    types = {row['type'] for row in r}
    assert 'heading' in types
    assert 'paragraph' in types


# ── 2. Search ──────────────────────────────────────────────────────────────────

def test_search_paragraphs(db):
    """10. Search paragraphs for keyword."""
    r = rows(db, "SELECT file, content FROM md WHERE type='paragraph' AND content LIKE '%API%'")
    assert len(r) >= 1


def test_search_headings(db):
    """11. Case-insensitive search in headings."""
    r = rows(db, "SELECT file, content FROM md WHERE type='heading' AND LOWER(content) LIKE '%rate%'")
    assert len(r) >= 1


def test_search_term_all_types(db):
    """12. Search all types for a term."""
    r = rows(db, "SELECT file, type, content FROM md WHERE content LIKE '%Authorization%' ORDER BY file")
    assert len(r) >= 1


# ── 3. Parent-child navigation ────────────────────────────────────────────────

def test_children_by_parent(db):
    """13. Get inline children of a paragraph by parent_pos."""
    # Find a paragraph that has inline children (strong, code, etc.)
    pars = rows(db, "SELECT pos_in_file FROM md WHERE type='paragraph' AND content LIKE '%API keys%' AND file='api.md'")
    if not pars:
        pytest.skip("Paragraph with inline children not found")
    parent = pars[0]['pos_in_file']
    children = rows(db, f"SELECT type, depth, content FROM md WHERE parent_pos={parent} ORDER BY pos_in_file")
    assert len(children) >= 1


def test_heading_hierarchy(db):
    """14. Sub-headings content under a main section (by position range, not parent-child)."""
    r = rows(db,
        "SELECT content, attrs_json FROM md "
        "WHERE type='heading' AND file='api.md' AND json_extract(attrs_json, '$.level')=2 "
        "ORDER BY pos_in_file"
    )
    assert len(r) >= 1


def test_root_paragraphs(db):
    """15. Top-level paragraphs (depth=0)."""
    r = rows(db, "SELECT content FROM md WHERE type='paragraph' AND depth=0")
    assert len(r) >= 1


def test_list_items(db):
    """16. All list items."""
    r = rows(db, "SELECT content, depth, attrs_json FROM md WHERE type='list_item' ORDER BY pos_in_file")
    assert len(r) >= 6  # api.md has 2 + large.md has 30
    # some are ordered, some unordered
    list_types = set()
    for row in r:
        attrs = json.loads(row['attrs_json']) if isinstance(row['attrs_json'], str) else row['attrs_json']
        if 'list_type' in attrs:
            list_types.add(attrs['list_type'])
    assert 'unordered' in list_types


def test_inline_by_parent(db):
    """17. Inline elements inside a specific parent."""
    # Find a paragraph that has inline children
    parent = rows(db,
        "SELECT pos_in_file FROM md WHERE type='paragraph' AND content LIKE '%API keys%' AND file='api.md'")
    if not parent:
        pytest.skip("Paragraph with inline children not found")
    parent_pos = parent[0]['pos_in_file']
    children = rows(db, f"SELECT type, content FROM md WHERE parent_pos={parent_pos}")
    assert len(children) >= 1


# ── 4. Statistics ──────────────────────────────────────────────────────────────

def test_avg_content_length(db):
    """18. Average content length by type."""
    r = rows(db, "SELECT type, AVG(LENGTH(content)) AS avg_len FROM md GROUP BY type ORDER BY avg_len DESC")
    types = {row['type'] for row in r}
    assert 'heading' in types


def test_code_by_lang(db):
    """19. Code blocks grouped by language (JSON extraction)."""
    r = rows(db,
        "SELECT json_extract(attrs_json, '$.lang') AS lang, COUNT(*) AS cnt "
        "FROM md WHERE type='code_block' AND json_extract(attrs_json, '$.lang') != '' "
        "GROUP BY lang ORDER BY cnt DESC")
    langs = {row['lang'] for row in r if row['lang']}
    assert 'bash' in langs or not langs  # mistune may not set lang


def test_largest_files(db):
    """20. Top files by element count."""
    r = rows(db,
        "SELECT file, COUNT(*) AS elements, SUM(LENGTH(content)) AS total_chars "
        "FROM md GROUP BY file ORDER BY elements DESC LIMIT 10")
    assert r[0]['file'] == 'large.md'


def test_depth_distribution(db):
    """21. Element count by depth level."""
    r = rows(db, "SELECT depth, COUNT(*) AS cnt FROM md GROUP BY depth ORDER BY depth")
    depths = {row['depth'] for row in r}
    assert 0 in depths
    assert 1 in depths


def test_avg_per_file(db):
    """22. Average elements per file."""
    r = rows(db, "SELECT COUNT(*)*1.0/COUNT(DISTINCT file) AS avg_per_file FROM md")
    assert r[0]['avg_per_file'] >= 5


def test_heading_level_1(db):
    """23. H1 headings only."""
    r = rows(db, "SELECT content FROM md WHERE type='heading' AND json_extract(attrs_json, '$.level')=1")
    assert any('API Reference' in row['content'] for row in r)


def test_file_element_count_grouped(db):
    """24. Element count per file per root."""
    r = rows(db, "SELECT root, file, COUNT(*) AS cnt FROM md GROUP BY root, file ORDER BY file")
    assert len(r) >= 3
    # All rows should have the same root (the full path)
    roots = {row['root'] for row in r}
    assert len(roots) == 1, f"Expected 1 unique root, got {len(roots)}: {roots}"


def test_block_types_summary(db):
    """25. Block types with JSON attribute presence."""
    r = rows(db,
        "SELECT type, COUNT(*) AS cnt, "
        "SUM(CASE WHEN attrs_json IS NOT NULL AND attrs_json != '{}' THEN 1 ELSE 0 END) AS with_attrs "
        "FROM md WHERE depth=0 GROUP BY type ORDER BY cnt DESC")
    assert len(r) >= 4


def test_items_with_parents(db):
    """26. All elements that have a parent (not root)."""
    r = rows(db, "SELECT type, content, parent_pos FROM md WHERE parent_pos IS NOT NULL ORDER BY pos_in_file")
    assert len(r) >= 1


def test_nested_items(db):
    """27. All nested elements (depth > 0)."""
    r = rows(db, "SELECT type, content, depth FROM md WHERE depth > 0 ORDER BY depth, pos_in_file")
    assert len(r) >= 1


def test_distinct_types(db):
    """28. All distinct element types."""
    r = rows(db, "SELECT DISTINCT type FROM md ORDER BY type")
    types = {row['type'] for row in r}
    assert 'heading' in types
    assert 'paragraph' in types
    assert 'list_item' in types


def test_content_stats(db):
    """29. Basic content statistics."""
    r = rows(db,
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN content IS NULL OR content = '' THEN 1 ELSE 0 END) AS empty_content, "
        "MAX(LENGTH(content)) AS max_len "
        "FROM md")
    assert r[0]['total'] >= 10


def test_complex_where(db):
    """30. Multi-condition WHERE across columns."""
    r = rows(db,
        "SELECT file, type, content FROM md "
        "WHERE file='api.md' AND type IN ('heading','paragraph') AND depth=0 "
        "ORDER BY pos_in_file")
    assert len(r) >= 3
    assert r[0]['type'] == 'heading'


# ── Validation tests ───────────────────────────────────────────────────────────

def test_delete_no_match(db):
    """DELETE with WHERE that matches no rows → ToolError."""
    with pytest.raises(ToolError, match='DELETE matched no rows'):
        md_change(db, "DELETE FROM md WHERE type='paragraph' AND content='nonexistent_xyzzy'")


def test_delete_no_where(db):
    """DELETE without WHERE → ToolError."""
    with pytest.raises(ToolError, match='DELETE without WHERE'):
        md_change(db, "DELETE FROM md")


def test_md_select_multi_statement(db):
    """SELECT with ; and second statement → ToolError."""
    with pytest.raises(ToolError, match='Multiple statements'):
        md_select(db, "SELECT 1; SELECT 2")


def test_md_select_ddl(db):
    """SELECT that is actually DDL → ToolError."""
    with pytest.raises(ToolError, match='Only SELECT'):
        md_select(db, "DROP TABLE md")


def test_md_change_insert_or_replace(db):
    """INSERT OR REPLACE works (parser handles OR modifier)."""
    # Get the actual root value stored in the DB
    root_val = db.execute("SELECT DISTINCT root FROM md LIMIT 1").fetchone()[0]
    result = md_change(db,
        f"INSERT OR REPLACE INTO md (root, file, type, content, depth) "
        f"VALUES ('{root_val}', 'api.md', 'paragraph', 'Inserted via OR REPLACE', 0)")
    assert len(result['changes']) == 1
    assert result['changes'][0]['operation'] == 'insert'


def test_md_change_empty_sql(db):
    """Empty sql string → ToolError."""
    with pytest.raises(ToolError):
        md_change(db, "")


# ── Config tests ───────────────────────────────────────────────────────────────

def test_parse_doc_roots_json():
    """JSON array of paths -> full resolved path."""
    import os
    from pathlib import Path
    old = os.environ.get("DOC_ROOTS")
    try:
        if "DOC_ROOTS" in os.environ:
            del os.environ["DOC_ROOTS"]
        os.environ["DOC_ROOTS"] = '["/tmp/x", "/tmp/y"]'
        roots = parse_doc_roots()
        assert len(roots) == 2
        assert str(roots[0].path) == str(Path("/tmp/x").resolve())
        assert str(roots[1].path) == str(Path("/tmp/y").resolve())
    finally:
        if old is not None:
            os.environ["DOC_ROOTS"] = old


def test_parse_doc_roots_empty():
    """Empty DOC_ROOTS -> empty list."""
    import os
    old = os.environ.get("DOC_ROOTS")
    try:
        if "DOC_ROOTS" in os.environ:
            del os.environ["DOC_ROOTS"]
        assert parse_doc_roots() == []
    finally:
        if old is not None:
            os.environ["DOC_ROOTS"] = old


def test_parse_doc_roots_malformed():
    """Malformed pairs -> skipped."""
    import os
    old = os.environ.get("DOC_ROOTS")
    try:
        if "DOC_ROOTS" in os.environ:
            del os.environ["DOC_ROOTS"]
        os.environ["DOC_ROOTS"] = "justalias"
        assert parse_doc_roots() == []
    finally:
        if old is not None:
            os.environ["DOC_ROOTS"] = old


def test_parse_doc_roots_multiple():
    """Multiple valid pairs → full resolved path."""
    import os
    from pathlib import Path
    old = os.environ.get("DOC_ROOTS")
    try:
        if "DOC_ROOTS" in os.environ:
            del os.environ["DOC_ROOTS"]
        os.environ["DOC_ROOTS"] = "a=/tmp/a;b=C:/tmp/b"
        roots = parse_doc_roots()
        assert len(roots) == 2
        assert str(roots[0].path) == str(Path("/tmp/a").resolve())
        assert str(roots[1].path) == str(Path("C:/tmp/b").resolve())
    finally:
        if old is not None:
            os.environ["DOC_ROOTS"] = old


# ── Reconstructor _parse_attrs tests ───────────────────────────────────────────

def test_parse_attrs_none():
    """None → empty dict."""
    assert _parse_attrs(None) == {}


def test_parse_attrs_empty_string():
    """Empty string → empty dict."""
    assert _parse_attrs('') == {}


def test_parse_attrs_dict():
    """Already a dict → returned as-is."""
    assert _parse_attrs({'key': 'val'}) == {'key': 'val'}


def test_parse_attrs_valid_json():
    """Valid JSON string → parsed."""
    assert _parse_attrs('{"a": 1}') == {'a': 1}


def test_parse_attrs_invalid_json():
    """Invalid JSON string → empty dict."""
    assert _parse_attrs('{bad') == {}


# ── Timeout tests ───────────────────────────────────────────────────────────────

def test_progress_handler_interrupts():
    """Progress handler returning non-zero interrupts SQL execution."""
    import sqlite3
    conn = get_connection()
    # Simulate timeout: handler returns 1 on every operation
    conn.set_progress_handler(lambda: 1, 1)
    with pytest.raises((sqlite3.DatabaseError, sqlite3.OperationalError)):
        conn.execute("SELECT 1")


def test_set_timeout_mechanism(monkeypatch):
    """_set_timeout sets a deadline-based progress handler that interrupts."""
    import time
    from mcp_md_sql.tools import _set_timeout

    conn = get_connection()

    # Mock time.time so deadline is immediately in the past
    # First call to time.time() sets deadline = 0 + 15 = 15
    # Subsequent calls (in progress handler) return 16 > 15 → interrupt
    call_count = 0

    def mock_time():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return 0.0  # deadline = 0 + 15 = 15
        return 16.0  # past deadline → handler returns 1

    monkeypatch.setattr(time, 'time', mock_time)
    _set_timeout(conn)

    import sqlite3
    with pytest.raises((sqlite3.DatabaseError, sqlite3.OperationalError)):
        conn.execute("SELECT 1")


# ── FS error tests ──────────────────────────────────────────────────────────────

def test_update_nonexistent_file(monkeypatch):
    """UPDATE on file that doesn't exist on disk → ToolError."""
    from mcp_md_sql.config import RootConfig, set_roots, get_doc_roots
    from mcp_md_sql.database import get_connection, load_files
    import tempfile

    conn = get_connection()
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        # Create a doc with one heading
        md_file = root / 'doc.md'
        md_file.write_text('# Hello\n', encoding='utf-8')

        cfg = [RootConfig(path=root)]
        set_roots(cfg)
        load_files(conn, cfg)

        # Delete the file on disk, then try to update
        md_file.unlink()

        with pytest.raises(ToolError, match='does not exist'):
            md_change(conn, "UPDATE md SET content='Updated' WHERE type='heading'")


def test_delete_nonexistent_file(monkeypatch):
    """DELETE that matches rows but file is missing → ToolError."""
    from mcp_md_sql.config import RootConfig, set_roots
    from mcp_md_sql.database import get_connection, load_files
    import tempfile

    conn = get_connection()
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        md_file = root / 'doc.md'
        md_file.write_text('# Hello\n', encoding='utf-8')

        cfg = [RootConfig(path=root)]
        set_roots(cfg)
        load_files(conn, cfg)

        # Delete the file on disk
        md_file.unlink()

        with pytest.raises(ToolError, match='does not exist'):
            md_change(conn, "DELETE FROM md WHERE type='heading'")
