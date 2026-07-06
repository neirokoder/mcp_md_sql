"""Tool descriptions exposed to the LLM via MCP."""
MD_SELECT_DESCRIPTION = "Reads the structure of an md files via SQL SELECT queries (headings, links, code blocks, etc.) with filtering"
MD_CHANGE_DESCRIPTION = "Edits md files via SQL INSERT/UPDATE/DELETE queries with filtering"

# ── Param descriptions (shared between tool schemas) ──

ROOT_DESC = (
    "Absolute path to the project root directory. "
    "All .md files under this directory will be parsed and indexed. "
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

INSERT requires: root, file (relative path), type, content.
Other columns are computed automatically; providing them causes an error.
UPDATE and DELETE require a WHERE clause (rejected otherwise).

Table `md` columns same as md_select.
"""

MD_INITIALIZE_DESCRIPTION = (
    "Initialize the database with a root directory. "
    "Parses all .md files under the given root and indexes them, "
    "then starts a file watcher for live updates. "
    "Returns initialization status and server diagnostics. "
    "Must be called before md_select / md_change."
)
