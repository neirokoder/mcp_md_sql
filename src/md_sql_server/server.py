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

        from mcp.types import ListRootsRequest, ListRootsResult

        result = await session.send_request(
            ListRootsRequest(method="roots/list"),
            ListRootsResult,
        )
        if not result.roots:
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
        return roots
    except Exception:
        return None


def _build_server() -> Server:
    """Create the MCP server with tool registrations."""
    server = Server("md-sql")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
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

The `md` table has columns:
  id          INTEGER PRIMARY KEY
  root        TEXT    — root alias (folder name from DOC_ROOTS)
  file        TEXT    — relative path inside root
  line_start  INTEGER — 1-based start line in source file
  line_end    INTEGER — 1-based end line
  pos_in_file INTEGER — element position in document order (1..N)
  parent_pos  INTEGER — pos_in_file of parent element (NULL for top-level)
  depth       INTEGER — nesting level (0 = top-level)
  type        TEXT    — element type (see below)
  content     TEXT    — text content
  attrs_json  TEXT    — JSON with attributes (level, url, lang, title, list_type, alt)

Element types (type):
  Block:     heading, paragraph, code_block, list_item, blockquote, table, hr, frontmatter, html
  Inline:    strong, emphasis, inline_code, link, image, strikethrough, html_inline
  (inline elements have parent_pos pointing to their parent block, depth > 0)

Attributes in attrs_json:
  heading:     {"level": 1|2|3|...}
  code_block:  {"lang": "python"}
  link:        {"url": "...", "title": "..."}
  image:       {"url": "...", "alt": "..."}
  list_item:   {"list_type": "ordered"|"unordered"}

Examples:
  SELECT * FROM md WHERE type='heading' AND json_extract(attrs_json,'$.level')=1
  SELECT content FROM md WHERE type='paragraph' AND content LIKE '%API%'
  SELECT file, COUNT(*) AS cnt FROM md GROUP BY file ORDER BY cnt DESC LIMIT 5
""",
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
Patches the source .md files on disk and returns a diff.

Requirements:
  INSERT must specify root (alias), file (relative path), type, and content.
  UPDATE and DELETE require a WHERE clause (safety — rejected otherwise).
  Direct modification of the `id` column is forbidden.
  Use UPDATE to change content or attrs_json of existing elements.
  Use DELETE to remove elements (blocks are removed as whole lines from the .md file).
  Use INSERT to add new paragraphs, headings, etc. at a position (or at end by default).

Table columns (same as md_select):
  id, root, file, line_start, line_end, pos_in_file, parent_pos,
  depth, type, content, attrs_json

Examples:
  INSERT INTO md (root, file, type, content, depth)
      VALUES ('docs', 'api.md', 'paragraph', 'New text', 0)
  UPDATE md SET content = 'Updated' WHERE type = 'heading' AND content = 'Old'
  DELETE FROM md WHERE type = 'paragraph' AND content LIKE '%deprecated%'
""",
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
