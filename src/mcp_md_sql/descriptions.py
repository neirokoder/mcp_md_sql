"""Tool descriptions exposed to the LLM via MCP."""
MD_SELECT_DESCRIPTION = "Reads the structure of an md files via SQL SELECT queries (headings, links, code blocks, etc.) with filtering"
MD_CHANGE_DESCRIPTION = "Edits md files via SQL INSERT/UPDATE/DELETE queries with filtering"

# ── Param descriptions (shared between tool schemas) ──

ROOT_PATH_DESC = (
    "Absolute path to the project root directory. "
    "In the `md` table, `root` stores the full absolute path. "
    "Optional — if omitted, root is resolved from IDE roots (MCP list_roots), "
    "then DOC_ROOTS env. "
)

SQL_SELECT_DESC = """\
Execute SELECT queries (standard SQLite SQL) against parsed Markdown.

Table `md` (parsed AST of .md files):
    root (полный абсолютный путь к корню документации),  # e.g. "/project/docs"
    file (relative path),
    line_start, line_end (1-based inclusive),
    pos_in_file (0-based char offset),
    parent_pos (pos_in_file of parent),
    depth, type, content, attrs_json

type — element kind: heading, paragraph, code_block, list_item, blockquote, \
table, hr, frontmatter, html, strong, emphasis, inline_code, link, image, \
strikethrough, html_inline

attrs_json — type-dependent attributes:
  heading:     {"level": 1|2|3|...}
  code_block:  {"lang": "..."}
  link:        {"url": "...", "title": "..."}
  image:       {"url": "...", "alt": "..."}
  list_item:   {"list_type": "ordered"|"unordered"}

Inline elements (strong, emphasis, ...) have parent_pos != NULL and depth > 0.
Use json_extract(attrs_json, '$.key') to query attributes.
"""

SQL_CHANGE_DESC = """\
Modify Markdown files via SQL. INSERT, UPDATE, DELETE to table `md` are applied as patches
to the original .md files. Returns a diff.

INSERT requires: root (nullable for fallback), file (relative path), type, content.
Other columns are computed automatically; providing them causes an error.
UPDATE and DELETE require a WHERE clause (rejected otherwise).

Table `md` columns same as md_select.
"""

MD_DEBUG_DESCRIPTION = (
    "Diagnostics tool. Returns the full MCP server state: connection status, "
    "environment variables, database counts, client capabilities, and a "
    "list_roots() probe result. Temporary — for debugging root resolution. "
    "Takes no parameters."
)
