"""MCP server entry point.

Registers `md_select` and `md_change` tools against per-root in-memory SQLite databases.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

from .config import RootConfig, get_doc_roots
from .database import get_connection, load_files
from .descriptions import MD_SELECT_DESCRIPTION, MD_CHANGE_DESCRIPTION, SQL_SELECT_DESC, SQL_CHANGE_DESC, ROOT_PATH_DESC
from .tools import md_select, md_change, ToolError
from .watcher import FileWatcher


# Global state — single connection and watcher shared across tool calls
_conn: sqlite3.Connection | None = None
_watcher: FileWatcher | None = None


STANDARD_COLS = {'root', 'file', 'line_start', 'line_end', 'pos_in_file',
                 'parent_pos', 'depth', 'type', 'content', 'attrs_json'}


def _format_select_result(rows: list[dict]) -> str:
    """Format md_select result as plain text for AI.

    Known columns are rendered in the standard block.
    Any extra columns (e.g. aggregates) are appended as a JSON line.
    """
    out: list[str] = []
    for r in rows:
        root = r.get('root') or ''
        file_ = r.get('file', '')
        root_prefix = f'{root}/\n' if root else ''

        ls = r.get('line_start')
        le = r.get('line_end')
        parts = []
        if ls is not None:
            parts.append(f'line: {ls}-{le}')
        pif = r.get('pos_in_file')
        if pif is not None:
            parts.append(f'pos: {pif}')
        parent = r.get('parent_pos')
        if parent is not None:
            parts.append(f'parent: {parent}')
        depth = r.get('depth')
        if depth:
            parts.append(f'depth: {depth}')
        t = r.get('type')
        if t:
            parts.append(f'type: {t}')

        meta = f'{root_prefix}{file_} ({', '.join(parts)})\n---'
        content = (r.get('content') or '').rstrip('\n')
        out.append(meta)
        out.append(content)

        # Extra columns not in the standard set — emit as JSON
        extra = {k: v for k, v in r.items() if k not in STANDARD_COLS}
        if extra:
            out.append(json.dumps(extra, ensure_ascii=False))

        out.append('')
    return '\n'.join(out)

logger = logging.getLogger(__name__)




def _build_server() -> Server:
    """Create the MCP server with tool registrations."""
    server = Server("md-sql")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="md_select",
                description=MD_SELECT_DESCRIPTION,
                inputSchema={
                    "type": "object",
                    "properties": {
                        "sql": {
                            "type": "string",
                            "description": SQL_SELECT_DESC,
                        },
                        "root_path": {
                            "type": "string",
                            "description": ROOT_PATH_DESC,
                        },
                    },
                    "required": ["sql"],
                },
            ),
            types.Tool(
                name="md_change",
                description=MD_CHANGE_DESCRIPTION,
                inputSchema={
                    "type": "object",
                    "properties": {
                        "sql": {
                            "type": "string",
                            "description": SQL_CHANGE_DESC,
                        },
                        "root_path": {
                            "type": "string",
                            "description": ROOT_PATH_DESC,
                        },
                    },
                    "required": ["sql"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        global _conn, _watcher

        sql = arguments.get("sql", "")
        root_path = arguments.get("root_path")

        # Priority: explicit root_path
        if root_path:
            path = Path(root_path).resolve()
            if not path.exists():
                return [types.TextContent(
                    type="text",
                    text=f"Error: root_path does not exist: {root_path}",
                )]
            if not path.is_dir():
                return [types.TextContent(
                    type="text",
                    text=f"Error: root_path must be a directory: {root_path}",
                )]
            cfg = RootConfig(path=path)
            _conn = get_connection()
            load_files(_conn, [cfg])
            _watcher = FileWatcher(_conn)
            _watcher.start([cfg])
            return await _handle_call_tool(name, arguments)

        # Fallback: pre-loaded DOC_ROOTS
        if _conn is not None:
            return await _handle_call_tool(name, arguments)

        return [types.TextContent(type="text", text="Error: no document roots available. Use root_path or configure DOC_ROOTS.")]

    return server


async def _handle_call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    """Tool dispatch logic. Uses global _conn and _watcher."""
    global _conn, _watcher

    sql = arguments.get("sql", "")
    if not sql:
        return [types.TextContent(type="text", text="Error: 'sql' parameter is required")]

    if _conn is None:
        return [types.TextContent(type="text", text="Error: no document root loaded")]

    try:
        if name == "md_select":
            logger.info("md_select: %.200s", sql)
            rows = md_select(_conn, sql)
            result = _format_select_result(rows)
            if not rows:
                result = "(no results)"
            return [types.TextContent(type="text", text=result)]

        elif name == "md_change":
            logger.info("md_change: %.200s", sql)
            result = md_change(_conn, sql)
            # Notify watcher to ignore the files we just wrote
            if _watcher:
                for key in result.get("files_changed", []):
                    r, f = key.rsplit(":", 1)
                    _watcher.ignore_path(r, f)
            result_json = json.dumps(result, ensure_ascii=False, indent=2)
            return [types.TextContent(type="text", text=result_json)]

        else:
            return [types.TextContent(type="text", text=f"Unknown tool: {name}")]

    except ToolError as e:
        return [types.TextContent(type="text", text=f"Error: {e}")]
    except Exception as e:
        logger.exception("Tool %s failed", name)
        return [types.TextContent(type="text", text=f"Internal error: {e}")]


async def run_server() -> None:
    """Initialize resources and start the MCP server.

    Pre-loads DOC_ROOTS — if set — into the global connection.
    root_path in tool call takes highest priority.
    """
    global _conn, _watcher

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    static = get_doc_roots()
    if static:
        _conn = get_connection()
        load_files(_conn, static)
        _watcher = FileWatcher(_conn)
        _watcher.start(static)
        logger.info("Pre-loaded %d static root(s)", len(static))

    server = _build_server()

    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
    finally:
        if _watcher:
            _watcher.stop()


def main() -> None:
    """Synchronous entry point."""
    import asyncio
    asyncio.run(run_server())


if __name__ == "__main__":
    main()
