"""Tool descriptions exposed to the LLM via MCP."""

MD_SELECT_DESCRIPTION = """\
Execute SELECT queries (standard SQLite SQL) against parsed Markdown.

Table `md`:
  root, file, line_start, line_end, pos_in_file, parent_pos,
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
Modify Markdown documentation via INSERT / UPDATE / DELETE (standard SQLite SQL).
Patches source .md files and returns a diff.

INSERT requires: root (alias), file, type, content.
UPDATE and DELETE require a WHERE clause (rejected otherwise).

Table columns same as md_select.
"""
