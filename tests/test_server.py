"""Tests for the MCP server — calls tool handlers directly (no MCP transport)."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from md_sql_server import server as srv
from md_sql_server.config import RootConfig, set_roots
from md_sql_server.database import get_connection, load_files
from md_sql_server.tools import ToolError

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


def _init_server(root: Path) -> None:
    """Initialise server globals as `run_server()` would."""
    srv._conn = get_connection()
    cfg = RootConfig(alias="project", path=root)
    set_roots([cfg])
    load_files(srv._conn, [cfg])
    srv._roots_loaded = True  # prevent IDE root loading in tests


def _cleanup_server() -> None:
    srv._conn = None
    srv._watcher = None
    srv._roots_loaded = False


@pytest.fixture(autouse=True)
def _reset_server():
    _cleanup_server()
    yield
    _cleanup_server()


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


# ── integration: _handle_call_tool ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_md_select_text_format():
    """_handle_call_tool returns text format for md_select."""
    with tempfile.TemporaryDirectory() as tmpdir:
        _init_server(_create_test_env(tmpdir))

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
        _init_server(_create_test_env(tmpdir))

        result = await srv._handle_call_tool('md_select', {
            'sql': "SELECT * FROM md WHERE type='table'",
        })

    assert result[0].text == '(no results)'


@pytest.mark.asyncio
async def test_md_select_error():
    """_handle_call_tool returns error message for invalid SQL."""
    with tempfile.TemporaryDirectory() as tmpdir:
        _init_server(_create_test_env(tmpdir))

        result = await srv._handle_call_tool('md_select', {
            'sql': 'DROP TABLE md',
        })

    assert 'Error:' in result[0].text


@pytest.mark.asyncio
async def test_md_select_missing_sql():
    """_handle_call_tool returns error when sql param is missing."""
    _init_server(Path(tempfile.mkdtemp()))

    result = await srv._handle_call_tool('md_select', {})

    assert 'Error:' in result[0].text
    assert 'sql' in result[0].text.lower()


@pytest.mark.asyncio
async def test_unknown_tool():
    """_handle_call_tool returns error for unknown tool name."""
    _init_server(Path(tempfile.mkdtemp()))

    result = await srv._handle_call_tool('unknown_tool', {'sql': 'SELECT 1'})

    assert 'Unknown tool' in result[0].text
