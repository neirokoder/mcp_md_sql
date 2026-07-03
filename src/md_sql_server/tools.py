"""MCP tools: md_select and md_change.

Implements SELECT validation, DML execution with diff generation,
and surgical file patching.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from .config import get_doc_roots
from .database import reload_file
from .reconstructor import render_single

logger = logging.getLogger(__name__)

TIMEOUT_SEC = 15
FORBIDDEN_DDL = re.compile(
    r'\b(CREATE|ALTER|DROP|TRUNCATE|RENAME|ANALYZE|REINDEX|ATTACH|DETACH)\b',
    re.IGNORECASE,
)
FORBIDDEN_TXN = re.compile(
    r'\b(BEGIN|COMMIT|ROLLBACK|SAVEPOINT|RELEASE)\b',
    re.IGNORECASE,
)


class ToolError(Exception):
    """User-facing error returned by tools."""
    pass


# ── md_select ────────────────────────────────────────────────────────────────

def md_select(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    """Execute a SELECT query and return rows as list of dicts."""
    _validate_select(sql)
    _set_timeout(conn)
    try:
        cur = conn.execute(sql)
        rows = cur.fetchall()
        return [dict(row) for row in rows]
    except sqlite3.Error as e:
        raise ToolError(f"SQL error: {e}")


def _validate_select(sql: str) -> None:
    """Ensure the statement is a single SELECT only."""
    stripped = sql.strip()
    if not re.match(r'^\s*SELECT\b', stripped, re.IGNORECASE):
        raise ToolError("Only SELECT queries are allowed in md_select")
    if ';' in stripped[:-1] if stripped else False:
        raise ToolError("Multiple statements are not allowed")


# ── md_change ────────────────────────────────────────────────────────────────

def md_change(conn: sqlite3.Connection, sql: str) -> dict[str, Any]:
    """Execute a DML query, generate diff, and patch files.

    Returns: {"changes": [...], "files_changed": [...]}
    """
    _validate_dml(sql)
    _set_timeout(conn)

    op = _detect_operation(sql)
    if op == 'INSERT':
        return _handle_insert(conn, sql)
    elif op == 'UPDATE':
        return _handle_update(conn, sql)
    elif op == 'DELETE':
        return _handle_delete(conn, sql)
    else:
        raise ToolError(f"Unsupported DML operation: {op}")


def _validate_dml(sql: str) -> None:
    """Reject DDL, transaction control, and direct id manipulation."""
    stripped = sql.strip().upper()
    if not any(stripped.startswith(w) for w in ('INSERT', 'UPDATE', 'DELETE')):
        raise ToolError("Only INSERT, UPDATE, DELETE are allowed in md_change")

    if FORBIDDEN_DDL.search(sql):
        raise ToolError("DDL commands (CREATE, ALTER, DROP, etc.) are not allowed")

    if FORBIDDEN_TXN.search(sql):
        raise ToolError("Transaction control (BEGIN, COMMIT, ROLLBACK) is not allowed")

    # Check for direct id manipulation in SET (UPDATE)
    if sql.strip().upper().startswith('UPDATE'):
        set_cols = _extract_set_columns(sql)
        if 'id' in set_cols:
            raise ToolError("Direct modification of `id` column is not allowed")
    # INSERT: reject if `id` appears in the column list
    insert_cols = _extract_insert_columns(sql)
    if 'id' in insert_cols:
        raise ToolError("Direct insertion of `id` column is not allowed")


def _detect_operation(sql: str) -> str:
    upper = sql.strip().upper()
    if upper.startswith('INSERT'):
        return 'INSERT'
    if upper.startswith('UPDATE'):
        return 'UPDATE'
    if upper.startswith('DELETE'):
        return 'DELETE'
    raise ToolError("Unknown operation")


def _extract_insert_columns(sql: str) -> set[str]:
    """Extract column names from INSERT INTO ... (col1, col2) ..."""
    m = re.search(r'INSERT\s+(?:OR\s+\w+\s+)?INTO\s+\w+\s*\(([^)]+)\)', sql, re.IGNORECASE)
    if not m:
        return set()
    return {c.strip().lower() for c in m.group(1).split(',')}


def _extract_set_columns(sql: str) -> set[str]:
    """Extract column names from UPDATE ... SET col=val, col2=val2 ... ."""
    m = re.search(r'\bSET\b\s+(.+?)(\bWHERE\b|$)', sql, re.IGNORECASE | re.DOTALL)
    if not m:
        return set()
    set_clause = m.group(1)
    cols: set[str] = set()
    for part in set_clause.split(','):
        eq = part.strip().split('=', 1)
        if eq:
            col = eq[0].strip().lower()
            if col and col != '?':
                cols.add(col)
    return cols


def _set_timeout(conn: sqlite3.Connection) -> None:
    """Set a busy timeout — note: sqlite3 doesn't support query timeout natively.

    We use a progress handler approach instead.
    """
    deadline = time.time() + TIMEOUT_SEC

    def progress_handler() -> int:
        if time.time() > deadline:
            return 1  # non-zero = interrupt
        return 0

    conn.set_progress_handler(progress_handler, 1)


# ── INSERT handler ───────────────────────────────────────────────────────────

def _handle_insert(conn: sqlite3.Connection, sql: str) -> dict[str, Any]:
    """Handle INSERT: execute, shift positions, patch file, reload."""
    # Parse the INSERT to get values
    values = _parse_insert_values(sql)
    root = values.get('root', '')
    file = values.get('file', '')
    if not root or not file:
        raise ToolError("INSERT must specify `root` and `file`")

    target_pos = values.get('pos_in_file')
    if target_pos is None:
        # Auto: insert at end
        cur = conn.execute("SELECT COALESCE(MAX(pos_in_file), 0) FROM md WHERE root = ? AND file = ?",
                          (root, file))
        target_pos = cur.fetchone()[0] + 1
        values['pos_in_file'] = target_pos
    else:
        target_pos = int(target_pos)

    # Shift existing elements
    _shift_positions(conn, root, file, target_pos, +1)

    # Parameterised INSERT — no SQL concatenation
    conn.execute(
        """INSERT INTO md
           (root, file, line_start, line_end, pos_in_file, parent_pos,
            depth, type, content, attrs_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            values.get('root'),
            values.get('file'),
            values.get('line_start'),
            values.get('line_end'),
            target_pos,
            values.get('parent_pos'),
            values.get('depth', 0),
            values.get('type'),
            values.get('content'),
            values.get('attrs_json', '{}'),
        ),
    )
    new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Get the newly inserted row
    new_row = dict(conn.execute("SELECT *, attrs_json as _aj FROM md WHERE id = ?", (new_id,)).fetchone())
    # Drop the _aj alias
    if '_aj' in new_row and 'attrs_json' not in new_row:
        new_row['attrs_json'] = new_row.pop('_aj')

    # Generate diff
    changes = [{
        'operation': 'insert',
        'root': root,
        'file': file,
        'id': new_id,
        'type': new_row['type'],
        'new_content': new_row['content'],
        'new_attrs_json': new_row['attrs_json'],
    }]

    # Patch file
    _patch_file_insert(conn, root, file, target_pos, new_row)
    _reload_after_write(conn, root, file)

    conn.commit()
    return {
        'changes': changes,
        'files_changed': [f'{root}:{file}'],
    }


def _parse_insert_values(sql: str) -> dict[str, Any]:
    """Naive parse of INSERT INTO ... VALUES (...).

    Returns dict of column -> value for basic INSERT statements.
    """
    # Match: INSERT [OR REPLACE] INTO md (col1, col2, ...) VALUES (val1, val2, ...)
    m = re.search(
        r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+\S+\s*\(([^)]+)\)\s*VALUES\s*\((.+)\)",
        sql, re.IGNORECASE | re.DOTALL,
    )
    if not m:
        raise ToolError("Cannot parse INSERT statement. Use standard syntax: INSERT INTO md (cols) VALUES (vals)")

    cols = [c.strip() for c in m.group(1).split(',')]
    vals_raw = m.group(2).strip()

    # Simple value split — handles quoted strings and numbers
    vals = _split_values(vals_raw)
    if len(cols) != len(vals):
        raise ToolError(f"Column count ({len(cols)}) != value count ({len(vals)})")

    result = {}
    for col, val in zip(cols, vals):
        result[col.lower()] = _unquote(val)
    return result


def _split_values(raw: str) -> list[str]:
    """Split VALUES clause into individual values, respecting quotes."""
    parts = []
    current = ''
    in_quote = False
    quote_char = ''
    for ch in raw:
        if ch in ("'", '"') and not in_quote:
            in_quote = True
            quote_char = ch
            current += ch
        elif ch == quote_char and in_quote:
            in_quote = False
            current += ch
        elif ch == ',' and not in_quote:
            parts.append(current.strip())
            current = ''
        else:
            current += ch
    if current.strip():
        parts.append(current.strip())
    return parts


def _unquote(val: str) -> Any:
    """Remove quotes from a SQL value."""
    val = val.strip()
    if (val.startswith("'") and val.endswith("'")) or \
       (val.startswith('"') and val.endswith('"')):
        return val[1:-1]
    # Try numeric
    try:
        return int(val)
    except (ValueError, TypeError):
        pass
    try:
        return float(val)
    except (ValueError, TypeError):
        pass
    if val.upper() == 'NULL':
        return None
    return val


def _shift_positions(conn: sqlite3.Connection, root: str, file: str,
                     from_pos: int, delta: int) -> None:
    """Shift pos_in_file for all elements >= from_pos by delta."""
    # Must update in reverse order to avoid UNIQUE constraint violation
    rows = conn.execute(
        """SELECT id FROM md
           WHERE root = ? AND file = ? AND pos_in_file >= ?
           ORDER BY pos_in_file DESC""",
        (root, file, from_pos),
    ).fetchall()
    for row in rows:
        conn.execute(
            "UPDATE md SET pos_in_file = pos_in_file + ? WHERE id = ?",
            (delta, row[0]),
        )


def _patch_file_insert(conn: sqlite3.Connection, root: str, file: str,
                       pos: int, new_row: dict) -> None:
    """Insert new element's Markdown into the file at the correct position."""
    root_path = _find_root_path(root)
    full_path = root_path / file
    if not full_path.exists():
        raise ToolError(f"File does not exist: {full_path}")

    source_lines = full_path.read_text(encoding='utf-8').splitlines(keepends=True)

    # Find insert point: element before pos
    prev_row = conn.execute(
        """SELECT line_end FROM md
           WHERE root = ? AND file = ? AND pos_in_file < ?
           ORDER BY pos_in_file DESC LIMIT 1""",
        (root, file, pos),
    ).fetchone()

    if prev_row and prev_row[0] is not None:
        insert_after_line = prev_row[0]  # 1-based
    else:
        insert_after_line = 0  # beginning of file

    new_md = render_single(new_row) + '\n'
    
    if insert_after_line == 0:
        source_lines.insert(0, new_md + '\n')
    else:
        # Insert after the specified line
        idx = insert_after_line  # 1-based -> 0-based index
        if idx < len(source_lines):
            source_lines.insert(idx, '\n' + new_md)
        else:
            source_lines.append('\n' + new_md)

    full_path.write_text(''.join(source_lines), encoding='utf-8')


# ── UPDATE handler ───────────────────────────────────────────────────────────

def _handle_update(conn: sqlite3.Connection, sql: str) -> dict[str, Any]:
    """Handle UPDATE: capture before/after, generate diff, patch files."""
    # Extract WHERE clause to identify affected rows
    where_clause = _extract_where(sql)
    if not where_clause:
        raise ToolError("UPDATE without WHERE is not allowed (safety)")

    # Before state
    before_rows = conn.execute(
        f"SELECT * FROM md WHERE {where_clause}"
    ).fetchall()
    before_map = {row['id']: dict(row) for row in before_rows}

    if not before_rows:
        raise ToolError("UPDATE matched no rows")

    # Track affected IDs for after-state selection
    affected_ids = [row['id'] for row in before_rows]

    # Execute UPDATE
    placeholders = ', '.join('?' * len(affected_ids))
    conn.execute(f"UPDATE md SET {_strip_set(sql)} WHERE {where_clause}")

    # After state — same rows by id (deterministic, not relying on version)
    after_rows = conn.execute(
        f"SELECT * FROM md WHERE id IN ({placeholders})", affected_ids
    ).fetchall()
    after_map = {row['id']: dict(row) for row in after_rows}

    # Build diff
    changes = []
    affected_files: set[str] = set()

    for row_id in before_map:
        old = before_map[row_id]
        new = after_map.get(row_id)
        if new is None:
            continue  # shouldn't happen
        root = old['root']
        file = old['file']
        affected_files.add(f'{root}:{file}')
        changes.append({
            'operation': 'update',
            'root': root,
            'file': file,
            'id': row_id,
            'type': old['type'],
            'old_content': old['content'],
            'new_content': new['content'],
            'old_attrs_json': old['attrs_json'],
            'new_attrs_json': new['attrs_json'],
        })

    # Patch affected files
    for key in affected_files:
        r, f = key.split(':', 1)
        _patch_file_update(conn, r, f, before_map, after_map)
        _reload_after_write(conn, r, f)

    conn.commit()
    return {
        'changes': changes,
        'files_changed': sorted(affected_files),
    }


def _strip_set(sql: str) -> str:
    """Extract SET ... clause from UPDATE ... SET ... WHERE."""
    m = re.search(
        r'UPDATE\s+\S+\s+SET\s+(.+?)\s+WHERE\b',
        sql, re.IGNORECASE | re.DOTALL,
    )
    if m:
        return m.group(1)
    # No WHERE clause — shouldn't happen since we validated
    m = re.search(r'UPDATE\s+\S+\s+SET\s+(.+)', sql, re.IGNORECASE | re.DOTALL)
    return m.group(1) if m else ''


def _extract_where(sql: str) -> str:
    """Extract WHERE clause from DML statement."""
    m = re.search(r'\bWHERE\b\s+(.+)$', sql, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else ''


def _patch_file_update(conn: sqlite3.Connection, root: str, file: str,
                       before_map: dict[int, dict], after_map: dict[int, dict]) -> None:
    """Surgically replace lines in file for updated elements."""
    root_path = _find_root_path(root)
    full_path = root_path / file
    if not full_path.exists():
        raise ToolError(f"File does not exist: {full_path}")

    source_lines = full_path.read_text(encoding='utf-8').splitlines(keepends=True)

    # Get all updated rows for this file
    file_before = {k: v for k, v in before_map.items() if v['file'] == file and v['root'] == root}
    file_after = {k: v for k, v in after_map.items() if v['file'] == file and v['root'] == root}

    # Build patches: list of (line_start, line_end, new_text)
    patches = []
    for row_id, old in file_before.items():
        new = file_after.get(row_id)
        if new is None:
            continue
        
        if old['line_start'] is None:
            # Inline element — patch parent instead
            _patch_inline_parent(conn, root, file, old, new, patches, source_lines)
            continue

        new_md = render_single(new)
        patches.append((old['line_start'], old['line_end'], new_md))

    # Apply patches bottom-to-top
    patches.sort(key=lambda p: p[0], reverse=True)
    for line_start, line_end, new_text in patches:
        start_idx = line_start - 1  # 1-based -> 0-based
        end_idx = line_end  # exclusive
        # Replace lines [start_idx, end_idx) with new_text
        new_lines = (new_text + '\n').splitlines(keepends=True)
        source_lines[start_idx:end_idx] = new_lines

    full_path.write_text(''.join(source_lines), encoding='utf-8')


def _patch_inline_parent(conn: sqlite3.Connection, root: str, file: str,
                         old: dict, new: dict, patches: list,
                         source_lines: list[str]) -> None:
    # Inline element update: patch parent's source text
    parent_pos = old.get('parent_pos')
    if parent_pos is None:
        return

    parent_row = conn.execute(
        """SELECT * FROM md
           WHERE root = ? AND file = ? AND pos_in_file = ?""",
        (root, file, parent_pos),
    ).fetchone()

    if parent_row is None:
        return

    parent_line_start = parent_row['line_start']
    parent_line_end = parent_row['line_end']
    if parent_line_start is None:
        return

    # Extract parent's original source text
    start_idx = parent_line_start - 1
    end_idx = parent_line_end
    parent_source = ''.join(source_lines[start_idx:end_idx])

    old_rendered = render_single(old)
    new_rendered = render_single(new)

    # Try to find old inline markup in source and replace
    if old_rendered and old_rendered in parent_source:
        new_source = parent_source.replace(old_rendered, new_rendered, 1)
    else:
        # Fallback: try regex-based replacement
        old_regex = _build_inline_regex(old)
        if old_regex:
            new_source = re.sub(old_regex, new_rendered, parent_source, count=1)
        else:
            # Last resort: re-render parent from DB
            new_source = render_single(dict(parent_row))

    patches.append((parent_line_start, parent_line_end, new_source.rstrip('\n')))


def _build_inline_regex(el: dict) -> re.Pattern | None:
    """Build a regex to find an inline element's markup in source text."""
    t = el.get('type', '')
    content = re.escape(el.get('content', '') or '')
    if not content:
        return None

    if t == 'strong':
        return re.compile(r'\*\*' + content + r'\*\*')
    elif t == 'emphasis':
        return re.compile(r'(?<!\*)\*(?!\*)\s*' + content + r'\s*\*(?!\*)')
    elif t == 'inline_code':
        return re.compile(r'`' + content + r'`')
    elif t == 'strikethrough':
        return re.compile(r'~~' + content + r'~~')
    elif t == 'link':
        url = _parse_attrs_from_el(el).get('url', '')
        url_pattern = re.escape(url) if url else r'[^)]*'
        return re.compile(r'\[' + content + r'\]\(' + url_pattern + r'[^)]*\)')
    elif t == 'image':
        url = _parse_attrs_from_el(el).get('url', '')
        url_pattern = re.escape(url) if url else r'[^)]*'
        return re.compile(r'!\[' + content + r'\]\(' + url_pattern + r'[^)]*\)')
    elif t == 'html_inline':
        return re.compile(re.escape(content))
    return None


def _parse_attrs_from_el(el: dict) -> dict:
    """Parse attrs_json from a row dict."""
    raw = el.get('attrs_json', '{}')
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return {}


def _patch_inline_delete(source_lines: list[str], deleted_row: dict,
                         all_rows: list[dict], parent_row: dict) -> str | None:
    """Remove an inline element from its parent's source text.

    Returns the new source text for the parent, or None if not found.
    """
    parent_line_start = parent_row['line_start']
    parent_line_end = parent_row['line_end']
    if parent_line_start is None:
        return None

    start_idx = parent_line_start - 1
    end_idx = parent_line_end
    parent_source = ''.join(source_lines[start_idx:end_idx])

    old_rendered = render_single(deleted_row)
    if old_rendered and old_rendered in parent_source:
        # Remove the inline markup, keeping surrounding text
        return parent_source.replace(old_rendered, '', 1)

    # Fallback: try regex
    old_regex = _build_inline_regex(deleted_row)
    if old_regex:
        new_source = re.sub(old_regex, '', parent_source, count=1)
        if new_source != parent_source:
            return new_source

    return None


# ── DELETE handler ───────────────────────────────────────────────────────────

def _handle_delete(conn: sqlite3.Connection, sql: str) -> dict[str, Any]:
    """Handle DELETE: capture before, delete, patch files."""
    where_clause = _extract_where(sql)
    if not where_clause:
        raise ToolError("DELETE without WHERE is not allowed (safety)")

    # Before state
    before_rows = conn.execute(
        f"SELECT * FROM md WHERE {where_clause}"
    ).fetchall()

    if not before_rows:
        raise ToolError("DELETE matched no rows")

    # Build diff and organize by file
    changes = []
    affected_files: set[str] = set()
    files_deletions: dict[tuple[str, str], list[dict]] = {}
    files_inline_patches: dict[tuple[str, str], list[dict]] = {}

    for row in before_rows:
        d = dict(row)
        root = d['root']
        file = d['file']
        affected_files.add(f'{root}:{file}')
        key = (root, file)
        changes.append({
            'operation': 'delete',
            'root': root,
            'file': file,
            'id': d['id'],
            'type': d['type'],
            'old_content': d['content'],
            'old_attrs_json': d['attrs_json'],
        })

        if d['line_start'] is not None:
            # Block element
            if key not in files_deletions:
                files_deletions[key] = []
            files_deletions[key].append(d)
        elif d.get('parent_pos') is not None:
            # Inline element — collect for parent patching
            if key not in files_inline_patches:
                files_inline_patches[key] = []
            files_inline_patches[key].append(d)

    # Execute DELETE
    conn.execute(f"DELETE FROM md WHERE {where_clause}")

    # Patch files: block deletions
    for (root, file), deleted_rows in files_deletions.items():
        _patch_file_delete(root, file, deleted_rows)
        _reload_after_write(conn, root, file)

    # Patch files: inline deletions (re-render parent)
    for (root, file), inline_rows in files_inline_patches.items():
        _patch_file_inline_deletes(conn, root, file, inline_rows)
        _reload_after_write(conn, root, file)

    conn.commit()
    return {
        'changes': changes,
        'files_changed': sorted(affected_files),
    }


def _patch_file_delete(root: str, file: str, deleted_rows: list[dict]) -> None:
    """Remove lines for deleted block elements from the file."""
    root_path = _find_root_path(root)
    full_path = root_path / file
    if not full_path.exists():
        raise ToolError(f"File does not exist: {full_path}")

    source_lines = full_path.read_text(encoding='utf-8').splitlines(keepends=True)

    ranges: list[tuple[int, int]] = []
    for row in deleted_rows:
        if row['line_start'] is not None:
            ranges.append((row['line_start'], row['line_end']))

    ranges.sort(key=lambda r: r[0], reverse=True)
    for line_start, line_end in ranges:
        start_idx = line_start - 1
        end_idx = line_end
        del source_lines[start_idx:end_idx]

    full_path.write_text(''.join(source_lines), encoding='utf-8')


def _patch_file_inline_deletes(conn: sqlite3.Connection, root: str, file: str,
                                inline_rows: list[dict]) -> None:
    """Remove inline elements from their parent's source text.

    For each inline element being deleted, find its parent's source lines,
    remove the inline markup, and write the updated parent source back.
    """
    root_path = _find_root_path(root)
    full_path = root_path / file
    if not full_path.exists():
        raise ToolError(f"File does not exist: {full_path}")

    source_lines = full_path.read_text(encoding='utf-8').splitlines(keepends=True)

    # Group inline rows by parent_pos
    parents: dict[int, list[dict]] = {}
    for row in inline_rows:
        pp = row.get('parent_pos')
        if pp is not None:
            if pp not in parents:
                parents[pp] = []
            parents[pp].append(row)

    # Process each parent
    patches: list[tuple[int, int, str]] = []
    for parent_pos, rows in parents.items():
        parent_row = conn.execute(
            """SELECT * FROM md
               WHERE root = ? AND file = ? AND pos_in_file = ?""",
            (root, file, parent_pos),
        ).fetchone()

        if parent_row is None:
            continue

        parent_line_start = parent_row['line_start']
        parent_line_end = parent_row['line_end']
        if parent_line_start is None:
            continue

        start_idx = parent_line_start - 1
        end_idx = parent_line_end
        parent_source = ''.join(source_lines[start_idx:end_idx])

        for row in rows:
            old_rendered = render_single(row)
            if old_rendered and old_rendered in parent_source:
                parent_source = parent_source.replace(old_rendered, '', 1)
            else:
                old_regex = _build_inline_regex(row)
                if old_regex:
                    parent_source = re.sub(old_regex, '', parent_source, count=1)

        patches.append((parent_line_start, parent_line_end, parent_source.rstrip('\n')))

    # Apply patches bottom-to-top
    patches.sort(key=lambda p: p[0], reverse=True)
    for line_start, line_end, new_text in patches:
        start_idx = line_start - 1
        end_idx = line_end
        new_lines = (new_text + '\n').splitlines(keepends=True)
        source_lines[start_idx:end_idx] = new_lines

    full_path.write_text(''.join(source_lines), encoding='utf-8')


def _collect_inline_patch(source_lines: list[str], deleted_row: dict,
                          inline_patches: dict[int, str]) -> None:
    """Legacy stub — inline deletion now handled by _patch_file_inline_deletes."""
    pass


def _collect_inline_patch(source_lines: list[str], deleted_row: dict,
                          inline_patches: dict[int, str]) -> None:
    """Collect inline deletion patches.

    This is a best-effort approach without DB access.
    Full implementation requires parent line_start/line_end.
    """
    # Without DB, we can't resolve parent_pos to line_start/line_end.
    # The caller should provide this information.
    pass


# ── Helpers ──────────────────────────────────────────────────────────────────

def _find_root_path(root: str) -> Path:
    """Find the filesystem path for a root alias."""
    for cfg in get_doc_roots():
        if cfg.alias == root:
            return cfg.path
    raise ToolError(f"Unknown root alias: {root}")


def _reload_after_write(conn: sqlite3.Connection, root: str, file: str) -> None:
    """Reload a file into the database after writing to it."""
    root_path = _find_root_path(root)
    reload_file(conn, root, file, root_path)
