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
from mcp_md_sql.tools import ToolError, set_root_config


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
    cfg = RootConfig(alias="project", path=root)
    set_roots([cfg])
    set_root_config("project", cfg)
    conn = get_connection()
    load_files(conn, [cfg])
    return conn


# Dummy state for _handle_call_tool (no watchers needed in unit tests)
_TEST_STATE = srv._ServerState()


# ── unit: _format_select_result ──────────────────────────────────────────────


class TestFormatSelectResult:
    def test_single_row(self):
        rows = [{
            'root': 'docs',
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
        assert 'docs/\n' in result
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
        conn = _make_conn(_create_test_env(tmpdir))

        result = await srv._handle_call_tool('md_select', {
            'sql': "SELECT * FROM md WHERE type='heading' "
                   "ORDER BY pos_in_file",
        }, conn)

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
        conn = _make_conn(_create_test_env(tmpdir))

        result = await srv._handle_call_tool('md_select', {
            'sql': "SELECT * FROM md WHERE type='table'",
        }, conn)

    assert result[0].text == '(no results)'


@pytest.mark.asyncio
async def test_md_select_error():
    """_handle_call_tool returns error message for invalid SQL."""
    with tempfile.TemporaryDirectory() as tmpdir:
        conn = _make_conn(_create_test_env(tmpdir))

        result = await srv._handle_call_tool('md_select', {
            'sql': 'DROP TABLE md',
        }, conn)

    assert 'Error:' in result[0].text


@pytest.mark.asyncio
async def test_md_select_missing_sql():
    """_handle_call_tool returns error when sql param is missing."""
    conn = _make_conn(Path(tempfile.mkdtemp()))

    result = await srv._handle_call_tool('md_select', {}, conn)

    assert 'Error:' in result[0].text
    assert 'sql' in result[0].text.lower()


@pytest.mark.asyncio
async def test_unknown_tool():
    """_handle_call_tool returns error for unknown tool name."""
    conn = _make_conn(Path(tempfile.mkdtemp()))

    result = await srv._handle_call_tool('unknown_tool', {'sql': 'SELECT 1'}, conn)

    assert 'Unknown tool' in result[0].text


# ── tests for new resolution logic ─────────────────────────────────────────────


class TestServerState:
    """Tests for _ServerState and connection resolution."""

    def test_get_or_create_db_creates_and_caches(self):
        """get_or_create_db creates a new DB on first call and reuses it."""
        state = srv._ServerState()
        cfg = RootConfig(alias="test", path=Path(tempfile.mkdtemp()))
        # Need at least one .md file for load_files to succeed
        (cfg.path / "test.md").write_text("# Test", encoding='utf-8')

        conn1 = state.get_or_create_db(cfg)
        assert conn1 is not None
        assert cfg.alias in state.databases

        conn2 = state.get_or_create_db(cfg)
        assert conn2 is conn1  # same cached connection

    def test_resolve_ide_roots_priority(self):
        """resolve_connection uses IDE roots first."""
        state = srv._ServerState()
        cfg = RootConfig(alias="ide_root", path=Path(tempfile.mkdtemp()))
        (cfg.path / "doc.md").write_text("# Doc", encoding='utf-8')

        conn = state.resolve_connection("SELECT 1", ide_roots=[cfg])
        assert conn is not None
        assert "ide_root" in state.databases

    def test_resolve_sql_root_match(self):
        """resolve_connection uses SQL WHERE root when no IDE roots."""
        state = srv._ServerState()
        cfg = RootConfig(alias="mydocs", path=Path(tempfile.mkdtemp()))
        (cfg.path / "read.md").write_text("# Read", encoding='utf-8')
        state.get_or_create_db(cfg)

        conn = state.resolve_connection(
            "SELECT * FROM md WHERE root='mydocs'", ide_roots=None)
        assert conn is not None
        assert conn is state.databases["mydocs"]

    def test_resolve_sql_root_containment_match(self):
        """resolve_connection matches root by containment."""
        state = srv._ServerState()
        cfg = RootConfig(alias="project_docs", path=Path(tempfile.mkdtemp()))
        (cfg.path / "f.md").write_text("# F", encoding='utf-8')
        state.get_or_create_db(cfg)

        # 'docs' is contained in 'project_docs'
        conn = state.resolve_connection(
            "SELECT * FROM md WHERE root='docs'", ide_roots=None)
        assert conn is not None

    def test_resolve_preloaded_root_found_by_sql(self):
        """resolve_connection finds a pre-loaded root via SQL WHERE."""
        state = srv._ServerState()
        root_path = Path(tempfile.mkdtemp())
        (root_path / "doc.md").write_text("# Doc", encoding='utf-8')
        cfg = RootConfig(alias="docs", path=root_path)
        state.get_or_create_db(cfg)

        conn = state.resolve_connection(
            "SELECT * FROM md WHERE root='docs'", ide_roots=None)
        assert conn is not None
        assert conn is state.databases["docs"]

    def test_resolve_no_roots_returns_none(self):
        """resolve_connection returns None when no roots available."""
        from mcp_md_sql.config import set_roots
        state = srv._ServerState()
        set_roots([])  # clear static roots

        conn = state.resolve_connection("SELECT 1", ide_roots=None)
        assert conn is None


class TestExtractRootFromSql:
    """Tests for _extract_root_from_sql."""

    def test_single_quotes(self):
        assert srv._extract_root_from_sql(
            "SELECT * FROM md WHERE root='project'") == "project"

    def test_double_quotes(self):
        assert srv._extract_root_from_sql(
            'SELECT * FROM md WHERE root="project"') == "project"

    def test_with_extra_conditions(self):
        assert srv._extract_root_from_sql(
            "SELECT * FROM md WHERE root='docs' AND type='heading'") == "docs"

    def test_no_where(self):
        assert srv._extract_root_from_sql(
            "SELECT * FROM md LIMIT 10") is None

    def test_root_not_in_where(self):
        assert srv._extract_root_from_sql(
            "SELECT * FROM md WHERE type='heading'") is None
