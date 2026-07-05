"""SQLite database management for the `md` table.

Handles schema creation, file loading, and provides a connection.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from .config import RootConfig, get_doc_roots
from .parser import parse, ParsedElement

logger = logging.getLogger(__name__)

# The `md` table schema
SCHEMA = """
CREATE TABLE IF NOT EXISTS md (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    root        TEXT NOT NULL,
    file        TEXT NOT NULL,
    line_start  INTEGER,
    line_end    INTEGER,
    pos_in_file INTEGER NOT NULL,
    parent_pos  INTEGER,
    depth       INTEGER DEFAULT 0,
    type        TEXT NOT NULL,
    content     TEXT,
    attrs_json  TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_md_root_file ON md(root, file);
CREATE INDEX IF NOT EXISTS idx_md_root_file_pos ON md(root, file, pos_in_file);
"""


def create_db(conn: sqlite3.Connection) -> None:
    """Create schema in the given in-memory connection."""
    conn.executescript(SCHEMA)
    conn.commit()


def load_files(conn: sqlite3.Connection, roots: list[RootConfig] | None = None) -> int:
    """Walk all configured doc roots, parse .md files, insert into `md`.

    Returns number of rows inserted.
    """
    if roots is None:
        roots = get_doc_roots()

    conn.execute("DELETE FROM md")
    total = 0

    for root_cfg in roots:
        root_path = root_cfg.path
        if not root_path.exists():
            logger.warning("Root path does not exist: %s", root_path)
            continue

        for md_file in root_path.rglob("*.md"):
            if md_file.is_symlink() and not md_file.exists():
                continue
            try:
                content = md_file.read_text(encoding='utf-8')
            except Exception as e:
                logger.warning("Cannot read %s: %s", md_file, e)
                continue

            rel_path = str(md_file.relative_to(root_path)).replace('\\', '/')
            elements = parse(content)

            rows = []
            for el in elements:
                rows.append((
                    str(root_cfg.path),
                    rel_path,
                    el.line_start,
                    el.line_end,
                    el.pos_in_file,
                    el.parent_pos,
                    el.depth,
                    el.type,
                    el.content,
                    json.dumps(el.attrs, ensure_ascii=False) if el.attrs else '{}',
                ))

            conn.executemany(
                """INSERT INTO md
                   (root, file, line_start, line_end, pos_in_file, parent_pos,
                    depth, type, content, attrs_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            total += len(rows)

    conn.commit()
    logger.info("Loaded %d rows from %d root(s)", total, len(roots))
    return total


def reload_file(conn: sqlite3.Connection, root: str, file: str,
                root_path: Path) -> int:
    """Reload a single file: delete old rows, parse, insert new rows.

    Returns number of rows inserted.
    """
    conn.execute("DELETE FROM md WHERE root = ? AND file = ?", (root, file))
    full_path = root_path / file
    if not full_path.exists():
        conn.commit()
        return 0

    content = full_path.read_text(encoding='utf-8')
    elements = parse(content)

    rows = []
    for el in elements:
        rows.append((
            root, file,
            el.line_start, el.line_end, el.pos_in_file,
            el.parent_pos, el.depth, el.type, el.content,
            json.dumps(el.attrs, ensure_ascii=False) if el.attrs else '{}',
        ))

    conn.executemany(
        """INSERT INTO md
           (root, file, line_start, line_end, pos_in_file, parent_pos,
            depth, type, content, attrs_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    return len(rows)


def get_connection() -> sqlite3.Connection:
    """Create a new in-memory SQLite connection with schema."""
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    create_db(conn)
    return conn
