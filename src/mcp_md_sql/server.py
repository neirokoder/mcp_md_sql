"""MCP server entry point.

Registers `md_select` and `md_change` tools against an in-memory SQLite database.
"""

from __future__ import annotations

import json
import logging
import sqlite3

from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

from .config import RootConfig, get_doc_roots, set_roots
from .database import get_connection, load_files
from .descriptions import MD_SELECT_DESCRIPTION, MD_CHANGE_DESCRIPTION
from .tools import md_select, md_change, ToolError
from .watcher import FileWatcher



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
        parts.append(f'type: {r.get("type", "?")}')

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


# Module-level state — shared across the server lifecycle
_conn: sqlite3.Connection | None = None
_watcher: FileWatcher | None = None
_roots_loaded: bool = False


def _get_conn() -> sqlite3.Connection:
    assert _conn is not None, "Database not initialized"
    return _conn


def _get_watcher() -> FileWatcher:
    assert _watcher is not None, "Watcher not initialized"
    return _watcher


async def _try_load_roots(session) -> list[RootConfig] | None:
    """Request roots from IDE via MCP protocol, return None if not available."""
    global _roots_loaded
    if _roots_loaded:
        return None
    _roots_loaded = True

    try:
        from urllib.parse import urlparse
        from urllib.request import url2pathname

        import asyncio
        logger.info("Requesting roots from IDE...")
        result = await asyncio.wait_for(session.list_roots(), timeout=2.0)
        if not result.roots:
            logger.info("IDE returned no roots")
            return None
        roots = []
        for r in result.roots:
            uri = urlparse(str(r.uri))
            alias = r.name or "root"
            if uri.scheme == 'file':
                path = Path(url2pathname(uri.path))
            else:
                path = Path(str(r.uri))
            roots.append(RootConfig(alias=alias, path=path.resolve()))
        logger.info("Got %d root(s) from IDE: %s", len(roots), [r.path for r in roots])
        return roots
    except asyncio.TimeoutError:
        logger.warning("IDE did not respond to roots/list request within 2s — using fallback")
        return None
    except Exception as e:
        logger.warning("Failed to get roots from IDE: %s", e)
        return None


def _build_server() -> Server:
    """Create the MCP server with tool registrations."""
    server = Server("md-sql")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        logger.info("list_tools called — checking IDE roots")
        # First call: try to load roots from IDE
        try:
            ctx = server.request_context
            ide_roots = await _try_load_roots(ctx.session)
            if ide_roots is not None:
                conn = _get_conn()
                set_roots(ide_roots)
                count = load_files(conn, ide_roots)
                _get_watcher().stop()
                _get_watcher().start(ide_roots)
                logger.info("Loaded %d elements from %d IDE root(s)", count, len(ide_roots))
        except (LookupError, Exception):
            pass

        return [
            types.Tool(
                name="md_select",
                description=MD_SELECT_DESCRIPTION,
                inputSchema={
                    "type": "object",
                    "properties": {
                        "sql": {
                            "type": "string",
                            "description": "SELECT query (single statement only)",
                        }
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
                            "description": "INSERT / UPDATE / DELETE statement",
                        }
                    },
                    "required": ["sql"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        return await _handle_call_tool(name, arguments)

    return server


async def _handle_call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    """Tool dispatch logic — extracted for testability."""
    sql = arguments.get("sql", "")
    if not sql:
        return [types.TextContent(type="text", text="Error: 'sql' parameter is required")]

    try:
        if name == "md_select":
            rows = md_select(_get_conn(), sql)
            result = _format_select_result(rows)
            if not rows:
                result = "(no results)"
            return [types.TextContent(type="text", text=result)]

        elif name == "md_change":
            result = md_change(_get_conn(), sql)
            # Notify watcher to ignore the files we just wrote
            for key in result.get("files_changed", []):
                r, f = key.split(":", 1)
                _get_watcher().ignore_path(r, f)
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

    Doc roots are resolved in order:
    1. MCP roots from IDE (via list_tools first-call lazy init)
    2. DOC_ROOTS environment variable
    3. Current working directory as fallback
    """
    global _conn, _watcher

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    _conn = get_connection()

    # Pre-load from env/config so server is usable immediately
    roots = get_doc_roots()
    count = load_files(_conn, roots)
    logger.info("Pre-loaded %d elements from %d root(s)", count, len(roots))

    _watcher = FileWatcher(_conn)
    _watcher.start(roots)

    server = _build_server()

    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
    finally:
        _watcher.stop()


def main() -> None:
    """Synchronous entry point."""
    import asyncio
    asyncio.run(run_server())


if __name__ == "__main__":
    main()
