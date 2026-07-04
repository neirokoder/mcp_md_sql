"""Markdown parser using mistune AST.

Parses .md source into a flat list of rows for the `md` table.
Line detection uses stripped-formatting matching for robust results.

Inline elements (strong, emphasis, link, image, codespan, strikethrough,
html_inline) are extracted as separate rows with `parent_pos` pointing
to their parent paragraph/heading.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import mistune


@dataclass
class ParsedElement:
    """One element = one row in the `md` table."""
    type: str
    content: str | None = None
    attrs: dict[str, Any] = field(default_factory=dict)
    depth: int = 0
    parent_pos: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    pos_in_file: int = 0
    list_type: str = ''  # 'ordered' or 'unordered' (set during walk)


def _extract_frontmatter(source: str) -> tuple[ParsedElement | None, str]:
    """Extract YAML frontmatter block (---\n...\n---) from source.

    Returns (frontmatter_element_or_None, remaining_source).
    """
    if not source.startswith('---\n'):
        return None, source

    # Find closing ---
    end_idx = source.find('\n---\n', 4)
    if end_idx == -1:
        # Check for --- at end of file (no trailing newline after closing)
        if source.endswith('\n---'):
            end_idx = len(source) - 4
            closing_offset = 4  # \n---
            # Re-check: ensure there's nothing after ---
            pass
        else:
            return None, source
    else:
        closing_offset = 5  # \n---\n

    yaml_text = source[4:end_idx]
    remaining = source[end_idx + closing_offset:]

    # Basic YAML key-value parsing
    yaml_dict = {}
    for line in yaml_text.split('\n'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if ':' in line:
            key, _, val = line.partition(':')
            key = key.strip()
            val = val.strip()
            if val and val[0] in ("'", '"') and val[-1] == val[0]:
                val = val[1:-1]
            yaml_dict[key] = val

    # Calculate line_end: count lines in frontmatter block
    line_end = len(source[:end_idx + closing_offset].splitlines())
    el = ParsedElement(
        type='frontmatter',
        content=yaml_text,
        attrs={'yaml': yaml_dict},
        line_start=1,
        line_end=line_end,
    )
    return el, remaining


def parse(source: str) -> list[ParsedElement]:
    """Parse Markdown source into a flat list of elements."""
    # Extract frontmatter before parsing
    frontmatter_el, body = _extract_frontmatter(source)

    md = mistune.create_markdown(
        renderer='ast',
        plugins=['strikethrough', 'table', 'task_lists'],
    )
    ast = md(body)

    elements: list[ParsedElement] = []
    if frontmatter_el is not None:
        elements.append(frontmatter_el)

    parent_refs: dict[int, int] = {}  # id(child_el) → id(parent_el)
    _walk(ast, elements, depth=0, parent_key=None, list_type='unordered',
          parent_refs=parent_refs)
    _assign_positions(elements)
    _resolve_parents(elements, parent_refs)
    _find_line_positions(source, elements)
    return elements


# ── AST walking ──────────────────────────────────────────────────────────────

def _walk(
    nodes: list[dict],
    out: list[ParsedElement],
    depth: int,
    parent_key: int | None,
    list_type: str = 'unordered',
    parent_refs: dict[int, int] | None = None,
) -> None:
    """Recursive walk of AST nodes into flat `out` list.

    `parent_key` is `id(parent_el)` for tracking parent-child relationships.
    `parent_refs` maps `id(child_el)` → `id(parent_el)` for later resolution.
    """
    for node in nodes:
        if not isinstance(node, dict):
            continue
        ntype = node.get('type', '')

        if ntype == 'heading':
            el = _heading(node, depth)
            _register_child(el, parent_key, parent_refs)
            out.append(el)
            _extract_inline_children(node, out, depth + 1, id(el), parent_refs)

        elif ntype == 'paragraph':
            el = _paragraph(node, depth)
            _register_child(el, parent_key, parent_refs)
            out.append(el)
            _extract_inline_children(node, out, depth + 1, id(el), parent_refs)

        elif ntype == 'block_text':
            el = _block_text(node, depth)
            _register_child(el, parent_key, parent_refs)
            out.append(el)
            _extract_inline_children(node, out, depth + 1, id(el), parent_refs)

        elif ntype == 'list':
            ordered = node.get('attrs', {}).get('ordered', False)
            child_list_type = 'ordered' if ordered else 'unordered'
            _walk(node.get('children', []), out, depth, parent_key,
                  child_list_type, parent_refs)

        elif ntype == 'list_item':
            el = _list_item(node, depth, list_type)
            _register_child(el, parent_key, parent_refs)
            out.append(el)
            _extract_inline_children(node, out, depth + 1, id(el), parent_refs)
            _walk_list_children(node.get('children', []), out, depth + 1,
                               id(el), parent_refs)

        elif ntype == 'block_code':
            el = _block_code(node, depth)
            _register_child(el, parent_key, parent_refs)
            out.append(el)

        elif ntype == 'block_quote':
            el = _block_quote(node, depth)
            _register_child(el, parent_key, parent_refs)
            out.append(el)
            _walk(node.get('children', []), out, depth + 1, parent_key=id(el),
                  list_type=list_type, parent_refs=parent_refs)

        elif ntype == 'table':
            el = _table(node, depth)
            _register_child(el, parent_key, parent_refs)
            out.append(el)

        elif ntype == 'thematic_break':
            el = _thematic_break(node, depth)
            _register_child(el, parent_key, parent_refs)
            out.append(el)

        elif ntype == 'block_html':
            el = _block_html(node, depth)
            _register_child(el, parent_key, parent_refs)
            out.append(el)


def _register_child(el: ParsedElement, parent_key: int | None,
                    parent_refs: dict[int, int] | None) -> None:
    """If `parent_key` is set, register this element as a child."""
    if parent_key is not None and parent_refs is not None:
        parent_refs[id(el)] = parent_key


def _walk_list_children(
    children: list[dict],
    out: list[ParsedElement],
    depth: int,
    parent_key: int | None,
    parent_refs: dict[int, int] | None,
) -> None:
    """Walk children of a list_item that are nested blocks (not the item text)."""
    for child in children:
        if not isinstance(child, dict):
            continue
        ctype = child.get('type', '')
        if ctype == 'block_text':
            continue
        elif ctype == 'paragraph':
            el = _paragraph(child, depth)
            _register_child(el, parent_key, parent_refs)
            out.append(el)
            _extract_inline_children(child, out, depth + 1, id(el), parent_refs)
        elif ctype == 'list':
            ordered = child.get('attrs', {}).get('ordered', False)
            child_lt = 'ordered' if ordered else 'unordered'
            _walk(child.get('children', []), out, depth, parent_key,
                  child_lt, parent_refs)
        elif ctype == 'block_quote':
            el = _block_quote(child, depth)
            _register_child(el, parent_key, parent_refs)
            out.append(el)
            _walk(child.get('children', []), out, depth + 1, parent_key=id(el),
                  parent_refs=parent_refs)
        elif ctype == 'block_code':
            el = _block_code(child, depth)
            _register_child(el, parent_key, parent_refs)
            out.append(el)
        else:
            _walk([child], out, depth, parent_key, parent_refs=parent_refs)


# ── Inline children extraction ───────────────────────────────────────────────

def _extract_inline_children(
    node: dict,
    out: list[ParsedElement],
    depth: int,
    parent_key: int,
    parent_refs: dict[int, int] | None,
) -> None:
    """Extract inline elements from a block node's children as separate rows."""
    children = node.get('children', [])
    _walk_inline(children, out, depth, parent_key, parent_refs)


def _walk_inline(
    nodes: list[dict],
    out: list[ParsedElement],
    depth: int,
    parent_key: int,
    parent_refs: dict[int, int] | None,
) -> None:
    """Recursively walk inline nodes, adding each as a separate ParsedElement."""
    for node in nodes:
        if not isinstance(node, dict):
            continue
        ntype = node.get('type', '')

        if ntype == 'strong':
            el = ParsedElement(
                type='strong',
                content=_flat_raw(node.get('children', [])),
                depth=depth,
            )
            _register_child(el, parent_key, parent_refs)
            out.append(el)
            _walk_inline(node.get('children', []), out, depth + 1, id(el), parent_refs)

        elif ntype == 'emphasis':
            el = ParsedElement(
                type='emphasis',
                content=_flat_raw(node.get('children', [])),
                depth=depth,
            )
            _register_child(el, parent_key, parent_refs)
            out.append(el)
            _walk_inline(node.get('children', []), out, depth + 1, id(el), parent_refs)

        elif ntype == 'codespan':
            el = ParsedElement(
                type='inline_code',
                content=node.get('raw', ''),
                depth=depth,
            )
            _register_child(el, parent_key, parent_refs)
            out.append(el)

        elif ntype == 'link':
            attrs: dict[str, Any] = {'url': node.get('attrs', {}).get('url', '')}
            title = node.get('attrs', {}).get('title', '')
            if title:
                attrs['title'] = title
            el = ParsedElement(
                type='link',
                content=_flat_raw(node.get('children', [])),
                attrs=attrs,
                depth=depth,
            )
            _register_child(el, parent_key, parent_refs)
            out.append(el)
            _walk_inline(node.get('children', []), out, depth, id(el), parent_refs)

        elif ntype == 'image':
            attrs: dict[str, Any] = {
                'url': node.get('attrs', {}).get('url', ''),
                'alt': _flat_raw(node.get('children', [])),
            }
            el = ParsedElement(
                type='image',
                content=_flat_raw(node.get('children', [])),
                attrs=attrs,
                depth=depth,
            )
            _register_child(el, parent_key, parent_refs)
            out.append(el)

        elif ntype == 'strikethrough':
            el = ParsedElement(
                type='strikethrough',
                content=_flat_raw(node.get('children', [])),
                depth=depth,
            )
            _register_child(el, parent_key, parent_refs)
            out.append(el)
            _walk_inline(node.get('children', []), out, depth + 1, id(el), parent_refs)

        elif ntype == 'inline_html':
            el = ParsedElement(
                type='html_inline',
                content=node.get('raw', ''),
                depth=depth,
            )
            _register_child(el, parent_key, parent_refs)
            out.append(el)

        elif ntype in ('text', 'softbreak', 'linebreak'):
            pass  # Just raw text, part of parent content

        elif 'children' in node:
            _walk_inline(node['children'], out, depth, parent_key, parent_refs)


# ── Position assignment ─────────────────────────────────────────────────────

def _assign_positions(elements: list[ParsedElement]) -> None:
    """Assign pos_in_file (1..N) in document order."""
    for i, el in enumerate(elements):
        el.pos_in_file = i + 1


def _resolve_parents(elements: list[ParsedElement],
                     parent_refs: dict[int, int]) -> None:
    """Resolve temporary parent references to real `pos_in_file` values.

    `parent_refs` maps `id(child_el)` → `id(parent_el)`.
    After `_assign_positions`, we know each element's `pos_in_file`.
    """
    if not parent_refs:
        return
    id_to_pos: dict[int, int] = {id(el): el.pos_in_file for el in elements}
    for el in elements:
        parent_key = parent_refs.get(id(el))
        if parent_key is not None:
            parent_pos = id_to_pos.get(parent_key)
            if parent_pos is not None:
                el.parent_pos = parent_pos


# ── Block handlers ──────────────────────────────────────────────────────────

def _heading(node: dict, depth: int) -> ParsedElement:
    text = _flat_raw(node.get('children', []))
    level = node.get('attrs', {}).get('level', 1)
    return ParsedElement(type='heading', content=text, attrs={'level': level}, depth=depth)


def _paragraph(node: dict, depth: int) -> ParsedElement:
    text = _flat_raw(node.get('children', []))
    return ParsedElement(type='paragraph', content=text, depth=depth)


def _block_text(node: dict, depth: int) -> ParsedElement:
    text = _flat_raw(node.get('children', []))
    return ParsedElement(type='paragraph', content=text, depth=depth)


def _list_item(node: dict, depth: int, list_type: str) -> ParsedElement:
    text = ''
    for child in node.get('children', []):
        if isinstance(child, dict):
            ct = child.get('type', '')
            if ct in ('block_text', 'paragraph'):
                text = _flat_raw(child.get('children', []))
                break
    return ParsedElement(
        type='list_item', content=text, depth=depth,
        attrs={'list_type': list_type}, list_type=list_type,
    )


def _block_code(node: dict, depth: int) -> ParsedElement:
    raw = node.get('raw', '')
    if raw.endswith('\n'):
        raw = raw[:-1]
    info = node.get('attrs', {}).get('info', '')
    parts = info.split(maxsplit=1) if info else ['']
    lang = parts[0]
    extra = parts[1] if len(parts) > 1 else ''
    attrs = {'lang': lang}
    if extra:
        attrs['info'] = extra
    return ParsedElement(type='code_block', content=raw, attrs=attrs, depth=depth)


def _block_quote(node: dict, depth: int) -> ParsedElement:
    """Compute blockquote content as join of direct children's text.

    Avoids duplicating nested paragraphs' content — each child keeps
    its own text, and blockquote concatenates them with newlines.
    """
    children = node.get('children', [])
    child_texts: list[str] = []
    for child in children:
        if isinstance(child, dict):
            child_texts.append(_flat_raw(child.get('children', [])))
    text = '\n'.join(child_texts)
    return ParsedElement(type='blockquote', content=text, depth=depth)


def _table(node: dict, depth: int) -> ParsedElement:
    headers, align, rows = _extract_table(node)
    body = _render_table_md(headers, rows)
    return ParsedElement(
        type='table', content=body,
        attrs={'headers': headers, 'align': align, 'rows': rows},
        depth=depth,
    )


def _thematic_break(node: dict, depth: int) -> ParsedElement:
    return ParsedElement(type='hr', depth=depth)


def _block_html(node: dict, depth: int) -> ParsedElement:
    return ParsedElement(type='html', content=node.get('raw', ''), depth=depth)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _flat_raw(nodes: list[dict]) -> str:
    """Recursively extract raw text from AST nodes (strips formatting)."""
    parts = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        ntype = node.get('type', '')
        if 'raw' in node:
            parts.append(node['raw'])
        elif ntype == 'softbreak':
            parts.append(' ')
        elif ntype == 'linebreak':
            parts.append('\n')
        elif ntype == 'codespan':
            parts.append(node.get('raw', ''))
        elif ntype == 'link':
            parts.append(_flat_raw(node.get('children', [])))
        elif ntype == 'image':
            parts.append(_flat_raw(node.get('children', [])))
        elif ntype == 'html_inline':
            parts.append(node.get('raw', ''))
        elif 'children' in node:
            parts.append(_flat_raw(node['children']))
    return ''.join(parts)


def _extract_table(node: dict) -> tuple[list[str], list[str], list[list[str]]]:
    headers: list[str] = []
    align: list[str] = []
    rows: list[list[str]] = []

    for child in node.get('children', []):
        if not isinstance(child, dict):
            continue
        ctype = child.get('type', '')
        if ctype == 'table_head':
            for cell in child.get('children', []):
                if isinstance(cell, dict) and cell.get('type') == 'table_cell':
                    headers.append(_flat_raw(cell.get('children', [])))
                    al = cell.get('attrs', {}).get('align') or 'left'
                    align.append(al)
        elif ctype == 'table_body':
            for row_node in child.get('children', []):
                if isinstance(row_node, dict) and row_node.get('type') == 'table_row':
                    row = []
                    for cell in row_node.get('children', []):
                        if isinstance(cell, dict) and cell.get('type') == 'table_cell':
                            row.append(_flat_raw(cell.get('children', [])))
                    rows.append(row)

    return headers, align, rows


def _render_table_md(headers: list[str], rows: list[list[str]]) -> str:
    if not headers:
        return ''
    hline = '| ' + ' | '.join(headers) + ' |'
    sep = '|' + '|'.join(' --- ' for _ in headers) + '|'
    rlines = ['| ' + ' | '.join(row) + ' |' for row in rows]
    return '\n'.join([hline, sep] + rlines)


# ── Line position detection ─────────────────────────────────────────────────

def _strip_md(text: str) -> str:
    """Remove inline markdown formatting for matching purposes."""
    t = text
    t = re.sub(r'\*\*(.+?)\*\*', r'\1', t)
    t = re.sub(r'__(.+?)__', r'\1', t)
    t = re.sub(r'\*(.+?)\*', r'\1', t)
    t = re.sub(r'_(.+?)_', r'\1', t)
    t = re.sub(r'~~(.+?)~~', r'\1', t)
    t = re.sub(r'`(.+?)`', r'\1', t)
    t = re.sub(r'!\[(.*?)\]\(.+?\)', r'\1', t)
    t = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', t)
    return t


def _find_line_positions(source: str, elements: list[ParsedElement]) -> None:
    """Assign line_start/line_end by scanning source lines.

    Uses cursor-based scanning: walks source lines in order,
    matching each top-level element (and list_items at any depth)
    to its line range.
    Elements with pre-set line_start (e.g. frontmatter) are
    skipped and advance the cursor.
    Other nested elements (strong, emphasis, inline_code, etc.)
    remain unassigned — they share their parent's line range.
    """
    source_lines = source.splitlines()
    cursor = 0

    # Assign lines to all list_items (any depth) and top-level elements.
    # Nested list_items occupy their own source lines and need line_start/line_end
    # for correct block-level operations (DELETE, UPDATE line patching).
    targets = [el for el in elements if el.depth == 0 or el.type == 'list_item']

    for el in targets:
        if el.line_start is not None:
            # Element already has line positions (e.g. frontmatter)
            cursor = el.line_end if el.line_end else cursor
            continue
        start, end = _scan_element(source_lines, cursor, el)
        if start is not None:
            el.line_start = start + 1
            el.line_end = end + 1
            cursor = end + 1


def _scan_element(lines: list[str], cursor: int, el: ParsedElement) -> tuple[int | None, int | None]:
    """Dispatch to the appropriate scanner based on element type."""
    t = el.type
    if t == 'heading':
        s = _scan_heading(lines, cursor, el)
        return (s, s) if s is not None else (None, None)
    elif t == 'code_block':
        return _scan_fence(lines, cursor)
    elif t == 'hr':
        s = _scan_hr(lines, cursor)
        return (s, s) if s is not None else (None, None)
    elif t == 'blockquote':
        return _scan_blockquote(lines, cursor)
    elif t == 'table':
        return _scan_table(lines, cursor)
    elif t == 'list_item':
        s = _scan_list_item(lines, cursor, el)
        return (s, s) if s is not None else (None, None)
    elif t in ('paragraph',):
        s = _scan_paragraph(lines, cursor, el)
        return (s, s) if s is not None else (None, None)
    elif t == 'html':
        s = _scan_html(lines, cursor, el)
        return (s, s) if s is not None else (None, None)
    return (None, None)


def _scan_heading(lines: list[str], cursor: int, el: ParsedElement) -> int | None:
    """Find heading line starting from cursor."""
    level = el.attrs.get('level', 1)
    prefix = '#' * level + ' '
    for i in range(cursor, len(lines)):
        if lines[i].startswith(prefix):
            return i
    return None


def _scan_fence(lines: list[str], cursor: int) -> tuple[int | None, int | None]:
    """Find fenced code block starting from cursor."""
    for i in range(cursor, len(lines)):
        if lines[i].strip().startswith('```') or lines[i].strip().startswith('~~~'):
            start = i
            marker = lines[i].strip()[:3]
            for j in range(i + 1, len(lines)):
                if lines[j].strip().startswith(marker):
                    return (start, j)
            return (start, len(lines) - 1)
    return (None, None)


def _scan_hr(lines: list[str], cursor: int) -> int | None:
    """Find horizontal rule starting from cursor."""
    for i in range(cursor, len(lines)):
        s = lines[i].strip()
        if s and len(s) >= 3 and all(c == s[0] for c in s) and s[0] in '-*_':
            return i
    return None


def _scan_blockquote(lines: list[str], cursor: int) -> tuple[int | None, int | None]:
    """Find blockquote range starting from cursor."""
    for i in range(cursor, len(lines)):
        if lines[i].lstrip().startswith('>'):
            start = i
            end = i
            for j in range(i + 1, len(lines)):
                if lines[j].lstrip().startswith('>') or not lines[j].strip():
                    end = j
                else:
                    break
            return (start, end)
    return (None, None)


def _scan_table(lines: list[str], cursor: int) -> tuple[int | None, int | None]:
    """Find table range starting from cursor."""
    for i in range(cursor, len(lines)):
        if lines[i].strip().startswith('|'):
            start = i
            end = i
            for j in range(i + 1, len(lines)):
                if lines[j].strip().startswith('|'):
                    end = j
                else:
                    break
            return (start, end)
    return (None, None)


def _scan_list_item(lines: list[str], cursor: int, el: ParsedElement) -> int | None:
    """Find list item line starting from cursor."""
    if not el.content:
        return None
    search = _strip_md(el.content[:30])
    markers = ('- ', '* ', '+ ', '\t- ', '\t* ', '\t+ ')
    for i in range(cursor, len(lines)):
        s = lines[i].lstrip()
        stripped = _strip_md(s)
        for m in markers:
            if s.startswith(m) and search in stripped:
                return i
        # Also check for ordered list patterns
        if re.match(r'\s*\d+\.\s', s) and search in stripped:
            return i
    return None


def _scan_paragraph(lines: list[str], cursor: int, el: ParsedElement) -> int | None:
    """Find paragraph line starting from cursor."""
    if not el.content:
        return None
    search = _strip_md(el.content[:30])
    if not search.strip():
        return None
    for i in range(cursor, len(lines)):
        s = lines[i].strip()
        if not s:
            continue
        # Skip known block elements
        if s.startswith('#') or s.startswith('```') or s.startswith('~~~') or s.startswith('>'):
            continue
        if s.startswith('|'):
            continue
        if s.startswith('- ') or s.startswith('* ') or s.startswith('+ '):
            continue
        if re.match(r'\d+\.\s', s):
            continue
        if s in ('---', '***', '___') or (len(s) >= 3 and all(c == s[0] for c in s) and s[0] in '-*_'):
            continue
        stripped = _strip_md(s)
        if search in stripped:
            return i
    return None


def _scan_html(lines: list[str], cursor: int, el: ParsedElement) -> int | None:
    """Find HTML line starting from cursor."""
    if not el.content:
        return None
    for i in range(cursor, len(lines)):
        if lines[i].strip() == el.content.strip():
            return i
    return None
