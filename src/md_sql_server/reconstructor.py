"""Markdown reconstruction from `md` table rows.

Generates normalized Markdown text from a list of rows.
"""

from __future__ import annotations

import json
from typing import Any


def reconstruct(rows: list[dict[str, Any]]) -> str:
    """Reconstruct Markdown from a list of rows (ordered by pos_in_file).

    Only top-level elements (depth=0) produce separate blocks.
    Nested elements are part of their parent's content.
    """
    lines: list[str] = []
    for row in rows:
        if row.get('depth', 0) > 0:
            continue  # Nested — part of parent
        block = _render_row(row)
        if block:
            lines.append(block)
    return '\n\n'.join(lines) + '\n' if lines else ''


def render_single(row: dict[str, Any]) -> str:
    """Render a single row to its normalized Markdown (no blank lines)."""
    return _render_element(row).rstrip('\n')


def _render_row(row: dict[str, Any]) -> str:
    """Render one row to Markdown, trimming trailing newlines."""
    return _render_element(row).rstrip('\n')


def _render_element(row: dict[str, Any]) -> str:
    """Render one row to Markdown text."""
    t = row.get('type', row.get('type', ''))
    content = row.get('content', '') or ''
    attrs = _parse_attrs(row.get('attrs_json', '{}'))
    depth = row.get('depth', 0)

    if t == 'heading':
        return _heading(attrs.get('level', 1), content)
    elif t == 'paragraph':
        return content
    elif t == 'code_block':
        return _code_block(attrs.get('lang', ''), attrs.get('info', ''), content)
    elif t == 'list_item':
        list_type = attrs.get('list_type', 'unordered')
        marker = '1.' if list_type == 'ordered' else '-'
        indent = '  ' * depth
        return f'{indent}{marker} {content}'
    elif t == 'blockquote':
        lines = content.split('\n')
        return '\n'.join(f'> {line}' for line in lines)
    elif t == 'table':
        # content already contains the full table in normalized Markdown
        return content
    elif t == 'hr':
        return '---'
    elif t == 'frontmatter':
        return f'---\n{content}\n---'
    elif t == 'html':
        return content
    elif t == 'inline_code':
        return f'`{content}`'
    elif t == 'strong':
        return f'**{content}**'
    elif t == 'emphasis':
        return f'*{content}*'
    elif t == 'strikethrough':
        return f'~~{content}~~'
    elif t == 'link':
        url = attrs.get('url', '')
        title = attrs.get('title', '')
        if title:
            return f'[{content}]({url} "{title}")'
        return f'[{content}]({url})'
    elif t == 'image':
        url = attrs.get('url', '')
        alt = attrs.get('alt', content)
        return f'![{alt}]({url})'
    else:
        return content


def _heading(level: int, content: str) -> str:
    return f'{"#" * level} {content}'


def _code_block(lang: str, info: str, content: str) -> str:
    fence_info = lang
    if info:
        fence_info += f' {info}'
    return f'```{fence_info}\n{content}\n```'


def _parse_attrs(raw: str | dict | None) -> dict:
    """Parse attrs_json to dict."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return {}
