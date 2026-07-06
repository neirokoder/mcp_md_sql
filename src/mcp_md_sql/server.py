"""MCP server entry point.

Registers `md_select` and `md_change` tools against per-root in-memory SQLite databases.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from pathlib import Path
from urllib.parse import urlparse, unquote

from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

from .config import RootConfig, get_doc_roots
from .database import get_connection, load_files
from .descriptions import (
    MD_SELECT_DESCRIPTION,
    MD_CHANGE_DESCRIPTION,
    SQL_SELECT_DESC,
    SQL_CHANGE_DESC,
    ROOT_PATH_DESC,
    MD_DEBUG_DESCRIPTION,
)
from .tools import md_select, md_change, ToolError
from .watcher import FileWatcher


# Global state — single connection and watcher shared across tool calls
_conn: sqlite3.Connection | None = None
_watcher: FileWatcher | None = None
_resolved_roots: list[RootConfig] | None = None
_roots_probe_failed: bool = False


STANDARD_COLS = {'root', 'file', 'line_start', 'line_end', 'pos_in_file',
                 'parent_pos', 'depth', 'type', 'content', 'attrs_json'}


def _uri_to_path(uri: str) -> Path | None:
    """Convert file:// URI to Path. Returns None for non-file schemes."""
    p = urlparse(uri)
    if p.scheme != 'file':
        return None
    raw = unquote(p.path)
    if p.netloc:
        raw = p.netloc + raw
    # Windows: /C:/proj → C:/proj
    if raw.startswith('/') and len(raw) > 2 and raw[2] == ':':
        raw = raw[1:]
    return Path(raw)


async def _resolve_roots(server) -> list[RootConfig] | None:
    """Return cached roots from init handler or fallback.

    Never calls list_roots itself — that would deadlock (client
    awaits CallToolResult). Roots are fetched by the
    InitializedNotification handler, which runs when the client
    is NOT waiting for a response.
    """
    global _resolved_roots, _roots_probe_failed
    if _resolved_roots is not None:
        return _resolved_roots
    if _roots_probe_failed:
        return None
    # Not fetched yet — DOC_ROOTS fallback used at call site
    return None


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

    # ── Fetch roots from IDE after client initialized ─────────────────────────
    async def _on_initialized(notif: types.InitializedNotification) -> None:
        """Called when client sends InitializedNotification.

        At this point the client is NOT waiting for a response,
        so we can safely call list_roots() without deadlock.
        """
        global _resolved_roots, _roots_probe_failed
        try:
            ctx = server.request_context
            session = ctx.session
            caps = session.client_params.capabilities if session.client_params else None
            if caps is not None and caps.roots is not None:
                res = await asyncio.wait_for(session.list_roots(), timeout=5)
                roots = []
                for r in res.roots:
                    p = _uri_to_path(str(r.uri))
                    if p and p.exists() and p.is_dir():
                        roots.append(RootConfig(path=p))
                if roots:
                    _resolved_roots = roots
                    logger.info("list_roots (on init): %d root(s)", len(roots))
                else:
                    logger.info("list_roots (on init): returned empty")
            else:
                logger.info("list_roots (on init): client did not declare roots capability")
        except asyncio.TimeoutError:
            logger.warning("list_roots (on init): timed out after 5s")
        except Exception as e:
            logger.info("list_roots (on init): %s: %s", type(e).__name__, e)
            _roots_probe_failed = True

    server.notification_handlers[types.InitializedNotification] = _on_initialized

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
            types.Tool(
                name="md_debug",
                description=MD_DEBUG_DESCRIPTION,
                inputSchema={
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        global _conn, _watcher

        # Debug tool — no root needed
        if name == "md_debug":
            return await _handle_debug(server)

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
            # Stop the old watcher before replacing it
            if _watcher:
                _watcher.stop()
            cfg = RootConfig(path=path)
            _conn = get_connection()
            load_files(_conn, [cfg])
            _watcher = FileWatcher(_conn)
            _watcher.start([cfg])
            return await _handle_call_tool(name, arguments)

        # Auto-resolve roots: init handler cache → DOC_ROOTS
        if _conn is None:
            roots = _resolved_roots or get_doc_roots()
            if roots:
                _conn = get_connection()
                load_files(_conn, roots)
                _watcher = FileWatcher(_conn)
                _watcher.start(roots)
                logger.info("Auto-resolved %d root(s)", len(roots))

        if _conn is None:
            return [types.TextContent(
                type="text",
                text="Error: no document roots available. Use root_path or configure DOC_ROOTS.",
            )]

        return await _handle_call_tool(name, arguments)

    return server


async def _handle_debug(server) -> list[types.TextContent]:
    """Collect diagnostics and return as JSON."""
    _watcher_alive = (
        _watcher is not None
        and hasattr(_watcher, 'observer')
        and getattr(_watcher.observer, 'is_alive', lambda: False)()
    )

    info = {
        "server": {
            "conn_loaded": _conn is not None,
            "watcher_running": _watcher_alive,
            "resolved_roots_cache": [str(r.path) for r in _resolved_roots] if _resolved_roots else None,
            "roots_probe_failed": _roots_probe_failed,
        },
        "env": {
            "DOC_ROOTS_raw": os.environ.get("DOC_ROOTS", ""),
            "DOC_ROOTS_parsed": [str(r.path) for r in get_doc_roots()],
            "cwd": str(Path.cwd()),
        },
        "db": None,
        "client_capabilities": None,
        "list_roots_probe": None,
    }

    # DB stats
    if _conn is not None:
        try:
            count = _conn.execute("SELECT COUNT(*) FROM md").fetchone()[0]
            roots_db = [row[0] for row in _conn.execute("SELECT DISTINCT root FROM md").fetchall()]
            info["db"] = {"md_count": count, "distinct_roots": roots_db}
        except Exception as e:
            info["db"] = {"error": f"{type(e).__name__}: {e}"}

    # Client capabilities + list_roots probe
    if server is not None:
        try:
            ctx = server.request_context
            session = ctx.session
            caps = session.client_params.capabilities if session.client_params else None
            if caps:
                info["client_capabilities"] = caps.model_dump(mode="json", exclude_none=True)
            else:
                info["client_capabilities"] = None

            # Live list_roots probe (only if client declared capability)
            if caps is not None and caps.roots is not None:
                try:
                    res = await asyncio.wait_for(session.list_roots(), timeout=3)
                    info["list_roots_probe"] = res.model_dump(mode="json", exclude_none=True)
                except asyncio.TimeoutError:
                    info["list_roots_probe"] = {"error": "TimeoutError: list_roots timed out after 3s"}
                except Exception as e:
                    info["list_roots_probe"] = {"error": f"{type(e).__name__}: {e}"}
            else:
                info["list_roots_probe"] = {"status": "client did not declare roots capability"}
        except LookupError:
            info["client_capabilities"] = "LookupError — called outside request context"
        except Exception as e:
            info["client_capabilities"] = {"error": f"{type(e).__name__}: {e}"}

    payload = json.dumps(info, ensure_ascii=False, indent=2, default=str)
    return [types.TextContent(type="text", text=payload)]


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
    asyncio.run(run_server())


if __name__ == "__main__":
    main()
