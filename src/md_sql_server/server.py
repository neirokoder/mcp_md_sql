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

from .config import get_doc_roots
from .database import get_connection, load_files
from .tools import md_select, md_change, ToolError
from .watcher import FileWatcher

logger = logging.getLogger(__name__)


# Module-level state — shared across the server lifecycle
_conn: sqlite3.Connection | None = None
_watcher: FileWatcher | None = None


def _get_conn() -> sqlite3.Connection:
    assert _conn is not None, "Database not initialized"
    return _conn


def _get_watcher() -> FileWatcher:
    assert _watcher is not None, "Watcher not initialized"
    return _watcher


def _build_server() -> Server:
    """Create the MCP server with tool registrations."""
    server = Server("md-sql-server")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="md_select",
                description="Execute a SELECT query against Markdown documentation. "
                            "The database contains parsed .md files as rows in the `md` table.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "sql": {
                            "type": "string",
                            "description": "SELECT query to execute (e.g., 'SELECT * FROM md WHERE type=\"heading\"')",
                        }
                    },
                    "required": ["sql"],
                },
            ),
            types.Tool(
                name="md_change",
                description="Modify Markdown documentation via INSERT/UPDATE/DELETE. "
                            "Returns a diff of changes and updates the source .md files.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "sql": {
                            "type": "string",
                            "description": "DML statement: INSERT INTO md ..., UPDATE md ..., DELETE FROM md ...",
                        }
                    },
                    "required": ["sql"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        sql = arguments.get("sql", "")
        if not sql:
            return [types.TextContent(type="text", text="Error: 'sql' parameter is required")]

        try:
            if name == "md_select":
                rows = md_select(_get_conn(), sql)
                result = json.dumps(rows, ensure_ascii=False, indent=2)
                if not rows:
                    result = "[]"
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

    return server


async def run_server() -> None:
    """Initialize resources and start the MCP server."""
    global _conn, _watcher

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    _conn = get_connection()
    roots = get_doc_roots()
    count = load_files(_conn, roots)
    logger.info("Loaded %d elements from %d root(s)", count, len(roots))

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
