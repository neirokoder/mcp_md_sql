"""MCP server entry point.

Registers `md_select` and `md_change` tools against per-root in-memory SQLite databases.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

from .config import RootConfig, get_doc_roots, set_roots
from .database import get_connection, load_files
from .descriptions import MD_SELECT_DESCRIPTION, MD_CHANGE_DESCRIPTION, SQL_SELECT_DESC, SQL_CHANGE_DESC, ROOT_PATH_DESC
from .tools import md_select, md_change, ToolError, set_root_config
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


@dataclass
class _ServerState:
    """Per-instance state — isolated between server instances in the same process."""
    databases: dict[str, sqlite3.Connection] = field(default_factory=dict)
    watchers: dict[str, FileWatcher] = field(default_factory=dict)
    session: types.Session | None = None

    def get_or_create_db(self, root_cfg: RootConfig) -> sqlite3.Connection:
        """Get existing database for a root or create a new one with loaded files."""
        alias = root_cfg.alias
        if alias in self.databases:
            return self.databases[alias]

        conn = get_connection()
        count = load_files(conn, [root_cfg])
        self.databases[alias] = conn
        set_root_config(alias, root_cfg)
        logger.info("Created DB for root '%s' (%s) — loaded %d rows", alias, root_cfg.path, count)

        w = FileWatcher(conn)
        w.start([root_cfg])
        self.watchers[alias] = w

        return conn

    def resolve_connection(self, sql: str,
                           ide_roots: list[RootConfig] | None) -> sqlite3.Connection | None:
        """Resolve which database connection to use for a tool call.

        Priority:
        1. IDE roots (first root's alias)
        2. root alias extracted from SQL WHERE clause → match in existing DBs
        3. Single pre-loaded root (DOC_ROOTS) — only when exactly one
        """
        # Priority 1: IDE roots
        if ide_roots:
            return self.get_or_create_db(ide_roots[0])

        # Priority 2: extract root from SQL and search existing DBs
        alias = _extract_root_from_sql(sql)
        if alias:
            if alias in self.databases:
                return self.databases[alias]
            # Containment match
            for a, conn in self.databases.items():
                if alias in a or a in alias:
                    logger.info("Matched root '%s' to existing DB '%s'", alias, a)
                    return self.databases[a]

        # Priority 3: single pre-loaded root (from DOC_ROOTS, not a fallback)
        if len(self.databases) == 1:
            return next(iter(self.databases.values()))

        logger.error("No document roots available")
        return None

    def stop_watchers(self) -> None:
        """Stop all file watchers."""
        for w in self.watchers.values():
            w.stop()
        self.watchers.clear()
        self.databases.clear()


def _extract_root_from_sql(sql: str) -> str | None:
    """Extract root alias from WHERE root = '...' clause."""
    m = re.search(r"""WHERE\s+root\s*=\s*['"]([^'"]+)['"]""", sql, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


async def _try_load_roots(session) -> list[RootConfig] | None:
    """Request roots from IDE via MCP protocol, return None if not available."""
    try:
        import asyncio
        from urllib.parse import urlparse
        from urllib.request import url2pathname

        logger.info("Requesting roots from IDE...")
        result = await asyncio.wait_for(session.list_roots(), timeout=2.0)
        if not result.roots:
            logger.error("IDE returned no roots")
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


def _build_server(state: _ServerState) -> Server:
    """Create the MCP server with tool registrations. State is captured in closures."""
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
        sql = arguments.get("sql", "")
        root_path = arguments.get("root_path")

        # Priority 0: explicit root_path (like codegraph_explore's projectPath)
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
            alias = path.name or "root"
            cfg = RootConfig(alias=alias, path=path)
            conn = state.get_or_create_db(cfg)
            return await _handle_call_tool(name, arguments, conn, state)

        # Always request roots from IDE on every tool call
        ide_roots = None
        try:
            state.session = server.request_context.session
            ide_roots = await _try_load_roots(state.session)
        except (LookupError, Exception) as e:
            logger.warning("call_tool: could not request IDE roots: %s", e)

        # Resolve which connection to use (priorities 1-3)
        conn = state.resolve_connection(sql, ide_roots)
        if conn is None:
            return [types.TextContent(type="text", text="Error: no document roots available")]

        return await _handle_call_tool(name, arguments, conn, state)

    return server


async def _handle_call_tool(name: str, arguments: dict,
                            conn: sqlite3.Connection,
                            state: _ServerState | None = None) -> list[types.TextContent]:
    """Tool dispatch logic."""
    sql = arguments.get("sql", "")
    if not sql:
        return [types.TextContent(type="text", text="Error: 'sql' parameter is required")]

    try:
        if name == "md_select":
            logger.info("md_select: %.200s", sql)
            rows = md_select(conn, sql)
            result = _format_select_result(rows)
            if not rows:
                result = "(no results)"
            return [types.TextContent(type="text", text=result)]

        elif name == "md_change":
            logger.info("md_change: %.200s", sql)
            result = md_change(conn, sql)
            # Notify watcher to ignore the files we just wrote
            if state:
                for key in result.get("files_changed", []):
                    r, f = key.split(":", 1)
                    watcher = state.watchers.get(r)
                    if watcher:
                        watcher.ignore_path(r, f)
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

    Pre-loads static roots (DOC_ROOTS) — if set — so the server is usable
    even without IDE support. IDE roots are requested on every tool call.
    root_path parameter takes highest priority.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    state = _ServerState()

    # Pre-load static roots so server is usable immediately
    static = get_doc_roots()
    for root_cfg in static:
        state.get_or_create_db(root_cfg)
    logger.info("Pre-loaded %d static root(s)", len(static))

    server = _build_server(state)

    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
    finally:
        state.stop_watchers()


def main() -> None:
    """Synchronous entry point."""
    import asyncio
    asyncio.run(run_server())


if __name__ == "__main__":
    main()
