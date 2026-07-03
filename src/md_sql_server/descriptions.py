"""Tool descriptions exposed to the LLM via MCP."""

MD_SELECT_DESCRIPTION = """\
Execute SELECT queries (standard SQLite SQL) against parsed Markdown.

Table `md` (parsed AST of .md files):
    root (alias for doc root, nullable for fallback),
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

MD_CHANGE_DESCRIPTION = """\
Modify Markdown files via SQL. INSERT, UPDATE, DELETE to table `md` are applied as patches
to the original .md files. Returns a diff.

INSERT requires: root (nullable for fallback), file (relative path), type, content.
Other columns are computed automatically; providing them causes an error.
UPDATE and DELETE require a WHERE clause (rejected otherwise).

Table `md` columns same as md_select.
"""
