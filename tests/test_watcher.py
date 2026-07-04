"""Tests for file watcher (_MdHandler)."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

import pytest

from mcp_md_sql.config import RootConfig
from mcp_md_sql.database import get_connection, create_db


class _FakeEvent:
    """Minimal event stub for _MdHandler testing."""
    def __init__(self, src_path, is_dir=False, event_type='modified'):
        self.src_path = src_path
        self.is_directory = is_dir
        self.event_type = event_type


def _make_handler(conn, tmpdir):
    from mcp_md_sql.watcher import _MdHandler
    cfg = RootConfig(alias='test', path=tmpdir)
    return _MdHandler(conn, cfg)


def test_ignores_directory():
    """Directory events are skipped — no rows loaded."""
    conn = get_connection()
    create_db(conn)
    with tempfile.TemporaryDirectory() as tmpdir:
        handler = _make_handler(conn, Path(tmpdir))
        event = _FakeEvent(str(Path(tmpdir) / 'subdir'), is_dir=True)
        handler._handle(event)

        rows = conn.execute('SELECT COUNT(*) AS cnt FROM md').fetchall()
        assert rows[0]['cnt'] == 0


def test_ignores_non_md():
    """Non-.md files are skipped — no rows loaded."""
    conn = get_connection()
    create_db(conn)
    with tempfile.TemporaryDirectory() as tmpdir:
        handler = _make_handler(conn, Path(tmpdir))
        txt = Path(tmpdir) / 'notes.txt'
        txt.write_text('hello', encoding='utf-8')
        event = _FakeEvent(str(txt))
        handler._handle(event)

        rows = conn.execute('SELECT COUNT(*) AS cnt FROM md').fetchall()
        assert rows[0]['cnt'] == 0


def test_processes_md():
    """.md modification triggers reload — data visible in DB."""
    conn = get_connection()
    create_db(conn)
    with tempfile.TemporaryDirectory() as tmpdir:
        md = Path(tmpdir) / 'doc.md'
        md.write_text('# Hello\n', encoding='utf-8')

        handler = _make_handler(conn, Path(tmpdir))
        event = _FakeEvent(str(md))
        handler._handle(event)

        rows = conn.execute(
            "SELECT content FROM md WHERE type='heading' AND root='test' AND file='doc.md'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]['content'] == 'Hello'


def test_ignore_path_skips_event():
    """ignore_path prevents reload on next event."""
    conn = get_connection()
    create_db(conn)
    with tempfile.TemporaryDirectory() as tmpdir:
        md = Path(tmpdir) / 'doc.md'
        md.write_text('# Hello\n', encoding='utf-8')

        handler = _make_handler(conn, Path(tmpdir))
        handler.ignore_path(str(md).replace('\\', '/'))
        event = _FakeEvent(str(md))
        handler._handle(event)

        rows = conn.execute('SELECT COUNT(*) AS cnt FROM md').fetchall()
        assert rows[0]['cnt'] == 0  # reload was skipped
