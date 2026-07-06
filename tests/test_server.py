"""Tests for the MCP server — calls tool handlers directly (no MCP transport)."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from mcp_md_sql import server as srv
from mcp_md_sql.config import RootConfig, set_roots
from mcp_md_sql.database import get_connection, load_files
from mcp_md_sql.tools import ToolError


# ── helpers ──────────────────────────────────────────────────────────────────
def _create_test_env(tmpdir: str) -> Path:
    root = Path(tmpdir)
    docs = root / "docs"
    docs.mkdir()
    (docs / "api.md").write_text(
        "# API Reference\n\n## GET /users\n\nReturns list of users.\n",
        encoding='utf-8',
    )
    return root


def _make_conn(root: Path) -> sqlite3.Connection:
    """Create a connection with loaded files for testing."""
    import sqlite3
    cfg = RootConfig(path=root)
    set_roots([cfg])
    conn = get_connection()
    load_files(conn, [cfg])
    return conn


# ── unit: _format_select_result ──────────────────────────────────────────────


class TestFormatSelectResult:
    def test_single_row(self):
        rows = [{
            'root': '/some/root/path',
            'file': 'api.md',
            'line_start': 1,
            'line_end': 1,
            'pos_in_file': 1,
            'parent_pos': None,
            'depth': 0,
            'type': 'heading',
            'content': '# Hello',
        }]
        result = srv._format_select_result(rows)
        assert '/some/root/path/\n' in result
        assert 'api.md' in result
        assert 'line: 1-1' in result
        assert 'pos: 1' in result
        assert 'parent:' not in result
        assert 'depth:' not in result
        assert 'type: heading' in result
        assert '---' in result
        assert '# Hello' in result

    def test_multiple_rows(self):
        rows = [
            {'root': 'r', 'file': 'f.md', 'line_start': 1, 'line_end': 1,
             'pos_in_file': 1, 'parent_pos': None, 'depth': 0,
             'type': 'heading', 'content': '# A'},
            {'root': 'r', 'file': 'f.md', 'line_start': 3, 'line_end': 3,
             'pos_in_file': 2, 'parent_pos': 1, 'depth': 1,
             'type': 'paragraph', 'content': 'Body.'},
        ]
        result = srv._format_select_result(rows)
        assert result.count('---') == 2
        assert result.count('r/\n') == 2
        assert '# A' in result
        assert 'Body.' in result

    def test_parent_pos_and_depth_shown_when_present(self):
        rows = [{
            'root': 'r', 'file': 'f.md', 'line_start': 1, 'line_end': 1,
            'pos_in_file': 2, 'parent_pos': 1, 'depth': 1,
            'type': 'paragraph', 'content': 'Child',
        }]
        result = srv._format_select_result(rows)
        assert 'parent: 1' in result
        assert 'depth: 1' in result

    def test_parent_pos_and_depth_omitted_when_null(self):
        rows = [{
            'root': 'r', 'file': 'f.md', 'line_start': 1, 'line_end': 1,
            'pos_in_file': 1, 'parent_pos': None, 'depth': 0,
            'type': 'heading', 'content': '# Top',
        }]
        result = srv._format_select_result(rows)
        assert 'parent:' not in result
        assert 'depth:' not in result

    def test_multiline_content(self):
        rows = [{
            'root': 'r', 'file': 'f.md', 'line_start': 1, 'line_end': 3,
            'pos_in_file': 1, 'parent_pos': None, 'depth': 0,
            'type': 'code_block', 'content': 'line1\nline2\nline3',
        }]
        result = srv._format_select_result(rows)
        assert 'line1' in result
        assert 'line2' in result
        assert 'line3' in result

    def test_empty_rows(self):
        assert srv._format_select_result([]) == ''

    def test_empty_root_omits_prefix(self):
        rows = [{
            'root': '',
            'file': 'f.md', 'line_start': 1, 'line_end': 1,
            'pos_in_file': 1, 'parent_pos': None, 'depth': 0,
            'type': 'heading', 'content': 'Hi',
        }]
        result = srv._format_select_result(rows)
        # Should not start with '/'
        assert result.startswith('f.md'), f"Got: {result}"
        assert '/\n' not in result

    def test_extra_columns_shown_as_json(self):
        rows = [{
            'root': 'r',
            'file': 'f.md',
            'line_start': 1,
            'line_end': 1,
            'pos_in_file': 1,
            'parent_pos': None,
            'depth': 0,
            'type': 'heading',
            'content': '# Hi',
            'total': 42,
        }]
        result = srv._format_select_result(rows)
        assert '42' in result
        assert '{"total": 42}' in result

    def test_extra_columns_multi_row(self):
        rows = [
            {'root': 'r', 'file': 'f.md', 'line_start': 1, 'line_end': 1,
             'pos_in_file': 1, 'depth': 0, 'type': 'paragraph',
             'content': 'A', 'cnt': 3},
            {'root': 'r', 'file': 'f.md', 'line_start': 3, 'line_end': 3,
             'pos_in_file': 2, 'depth': 0, 'type': 'paragraph',
             'content': 'B', 'cnt': 7},
        ]
        result = srv._format_select_result(rows)
        assert result.count('{"cnt": 3}') == 1
        assert result.count('{"cnt": 7}') == 1

    def test_null_or_missing_line_fields(self):
        """NULL line_start/line_end/pos_in_file не выводятся."""
        rows = [{
            'root': 'r', 'file': 'f.md',
            'line_start': None, 'line_end': None,
            'pos_in_file': None, 'parent_pos': None, 'depth': 0,
            'type': 'heading', 'content': '# Nulls',
        }]
        result = srv._format_select_result(rows)
        assert 'line:' not in result, f"Got: {result}"
        assert 'pos:' not in result, f"Got: {result}"


# ── integration: _handle_call_tool ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_md_select_text_format():
    """_handle_call_tool returns text format for md_select."""
    with tempfile.TemporaryDirectory() as tmpdir:
        srv._conn = _make_conn(_create_test_env(tmpdir))

        result = await srv._handle_call_tool('md_select', {
            'sql': "SELECT * FROM md WHERE type='heading' "
                   "ORDER BY pos_in_file",
        })

    assert len(result) == 1
    text = result[0].text

    assert 'API Reference' in text, f"Expected content, got: {text}"
    assert 'docs/api.md' in text
    assert 'type: heading' in text
    assert '---' in text
    assert 'GET /users' in text  # at least one sub-heading
    # root-level blocks: parent & depth should be OMITTED
    assert 'parent:' not in text
    assert 'depth:' not in text
    assert '[' not in text.splitlines()[0]  # not JSON


@pytest.mark.asyncio
async def test_md_select_empty_result():
    """_handle_call_tool returns '(no results)' for empty queries."""
    with tempfile.TemporaryDirectory() as tmpdir:
        srv._conn = _make_conn(_create_test_env(tmpdir))

        result = await srv._handle_call_tool('md_select', {
            'sql': "SELECT * FROM md WHERE type='table'",
        })

    assert result[0].text == '(no results)'


@pytest.mark.asyncio
async def test_md_select_error():
    """_handle_call_tool returns error message for invalid SQL."""
    with tempfile.TemporaryDirectory() as tmpdir:
        srv._conn = _make_conn(_create_test_env(tmpdir))

        result = await srv._handle_call_tool('md_select', {
            'sql': 'DROP TABLE md',
        })

    assert 'Error:' in result[0].text


@pytest.mark.asyncio
async def test_md_select_missing_sql():
    """_handle_call_tool returns error when sql param is missing."""
    srv._conn = _make_conn(Path(tempfile.mkdtemp()))

    result = await srv._handle_call_tool('md_select', {})

    assert 'Error:' in result[0].text
    assert 'sql' in result[0].text.lower()


@pytest.mark.asyncio
async def test_unknown_tool():
    """_handle_call_tool returns error for unknown tool name."""
    srv._conn = _make_conn(Path(tempfile.mkdtemp()))

    result = await srv._handle_call_tool('unknown_tool', {'sql': 'SELECT 1'})

    assert 'Unknown tool' in result[0].text


# ── tests for new resolution logic ─────────────────────────────────────────────


# ── _uri_to_path ────────────────────────────────────────────────────────────


class TestUriToPath:
    def test_file_uri_windows(self):
        p = srv._uri_to_path("file:///C:/project/docs")
        assert p == Path("C:/project/docs")

    def test_file_uri_percent_encoded(self):
        p = srv._uri_to_path("file:///C%3A/project")
        assert p == Path("C:/project")

    def test_file_uri_unix(self):
        p = srv._uri_to_path("file:///home/user/docs")
        assert p == Path("/home/user/docs")

    def test_non_file_scheme(self):
        assert srv._uri_to_path("https://example.com") is None

    def test_invalid_uri_does_not_crash(self):
        p = srv._uri_to_path("not a uri")
        assert p is None  # non-file scheme returns None


# ── _resolve_roots ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_roots_no_context():
    """_resolve_roots без request context (None server) → None, флаг failed."""
    srv._resolved_roots = None
    srv._roots_probe_failed = False
    result = await srv._resolve_roots(None)
    assert result is None
    assert srv._roots_probe_failed is True


@pytest.mark.asyncio
async def test_resolve_roots_cached():
    """Второй вызов возвращает кэшированное значение."""
    srv._resolved_roots = [RootConfig(path=Path("/cached"))]
    srv._roots_probe_failed = False
    result = await srv._resolve_roots(None)
    assert result is not None
    assert result[0].path == Path("/cached")


@pytest.mark.asyncio
async def test_resolve_roots_failed_flag():
    """Если флаг _roots_probe_failed — сразу None."""
    srv._resolved_roots = None
    srv._roots_probe_failed = True
    result = await srv._resolve_roots(None)
    assert result is None


# ── _handle_debug ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_debug_no_conn():
    """_handle_debug без загруженной БД — возвращает JSON без падения."""
    srv._resolved_roots = None
    srv._roots_probe_failed = False
    saved = srv._conn
    srv._conn = None
    try:
        result = await srv._handle_debug(None)
        text = result[0].text
        assert '"conn_loaded": false' in text
        assert '"db": null' in text or '"db": None' in text
        assert 'no error' not in text.lower()  # нет сырых исключений
    finally:
        srv._conn = saved


@pytest.mark.asyncio
async def test_handle_debug_with_db():
    """_handle_debug с загруженной БД — показывает md_count и distinct_roots."""
    import tempfile
    root = Path(tempfile.mkdtemp())
    docs = root / "docs"
    docs.mkdir()
    (docs / "readme.md").write_text("# Test\n\nHello.\n", encoding='utf-8')

    saved = srv._conn
    srv._conn = None
    try:
        cfg = RootConfig(path=root)
        conn = get_connection()
        load_files(conn, [cfg])
        srv._conn = conn

        result = await srv._handle_debug(None)
        text = result[0].text
        assert '"conn_loaded": true' in text
        assert '"md_count"' in text
        assert '"distinct_roots"' in text
    finally:
        srv._conn = saved


@pytest.mark.asyncio
async def test_handle_debug_no_server():
    """server=None — клиентские разделы пропущены, не падает."""
    result = await srv._handle_debug(None)
    text = result[0].text
    # Без server: client_capabilities и list_roots_probe остаются null
    assert '"client_capabilities": null' in text
    assert '"list_roots_probe": null' in text


