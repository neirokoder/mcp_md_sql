"""Unit tests for the Markdown reconstructor."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from md_sql_server.reconstructor import reconstruct, render_single


def test_render_heading():
    row = {'type': 'heading', 'content': 'Hello World', 'attrs_json': '{"level": 1}'}
    assert render_single(row) == '# Hello World'

    row2 = {'type': 'heading', 'content': 'Section', 'attrs_json': '{"level": 3}'}
    assert render_single(row2) == '### Section'
    print("✅ test_render_heading PASSED")


def test_render_paragraph():
    row = {'type': 'paragraph', 'content': 'Hello World'}
    assert render_single(row) == 'Hello World'
    print("✅ test_render_paragraph PASSED")


def test_render_code_block():
    row = {'type': 'code_block', 'content': 'print("hello")',
           'attrs_json': '{"lang": "python"}'}
    result = render_single(row)
    assert result == '```python\nprint("hello")\n```'
    print("✅ test_render_code_block PASSED")


def test_render_list_item_unordered():
    row = {'type': 'list_item', 'content': 'Item one', 'depth': 0,
           'attrs_json': '{"list_type": "unordered"}'}
    assert render_single(row) == '- Item one'
    print("✅ test_render_list_item_unordered PASSED")


def test_render_list_item_ordered():
    row = {'type': 'list_item', 'content': 'First', 'depth': 0,
           'attrs_json': '{"list_type": "ordered"}'}
    assert render_single(row) == '1. First'
    print("✅ test_render_list_item_ordered PASSED")


def test_render_list_item_indented():
    row = {'type': 'list_item', 'content': 'Sub item', 'depth': 2,
           'attrs_json': '{"list_type": "unordered"}'}
    assert render_single(row) == '    - Sub item'
    print("✅ test_render_list_item_indented PASSED")


def test_render_blockquote():
    row = {'type': 'blockquote', 'content': 'Line one\nLine two'}
    result = render_single(row)
    assert result == '> Line one\n> Line two'
    print("✅ test_render_blockquote PASSED")


def test_render_table():
    md_table = '| Name | Value |\n| --- | --- |\n| Alice | 100 |'
    row = {'type': 'table', 'content': md_table}
    assert render_single(row) == md_table
    print("✅ test_render_table PASSED")


def test_render_hr():
    row = {'type': 'hr'}
    assert render_single(row) == '---'
    print("✅ test_render_hr PASSED")


def test_render_frontmatter():
    row = {'type': 'frontmatter', 'content': 'title: Test\ndate: 2024'}
    result = render_single(row)
    assert result == '---\ntitle: Test\ndate: 2024\n---'
    print("✅ test_render_frontmatter PASSED")


def test_render_html():
    row = {'type': 'html', 'content': '<div>x</div>'}
    assert render_single(row) == '<div>x</div>'
    print("✅ test_render_html PASSED")


def test_render_inline_strong():
    row = {'type': 'strong', 'content': 'bold'}
    assert render_single(row) == '**bold**'
    print("✅ test_render_inline_strong PASSED")


def test_render_inline_emphasis():
    row = {'type': 'emphasis', 'content': 'italic'}
    assert render_single(row) == '*italic*'
    print("✅ test_render_inline_emphasis PASSED")


def test_render_inline_code():
    row = {'type': 'inline_code', 'content': 'print()'}
    assert render_single(row) == '`print()`'
    print("✅ test_render_inline_code PASSED")


def test_render_inline_strikethrough():
    row = {'type': 'strikethrough', 'content': 'old'}
    assert render_single(row) == '~~old~~'
    print("✅ test_render_inline_strikethrough PASSED")


def test_render_link():
    row = {'type': 'link', 'content': 'example',
           'attrs_json': '{"url": "https://example.com"}'}
    assert render_single(row) == '[example](https://example.com)'
    print("✅ test_render_link PASSED")


def test_render_link_with_title():
    row = {'type': 'link', 'content': 'example',
           'attrs_json': '{"url": "https://example.com", "title": "A title"}'}
    assert render_single(row) == '[example](https://example.com "A title")'
    print("✅ test_render_link_with_title PASSED")


def test_render_image():
    row = {'type': 'image', 'content': 'alt text',
           'attrs_json': '{"url": "https://img.com/pic.png", "alt": "alt text"}'}
    assert render_single(row) == '![alt text](https://img.com/pic.png)'
    print("✅ test_render_image PASSED")


def test_reconstruct_multiple_blocks():
    rows = [
        {'type': 'heading', 'content': 'Title', 'attrs_json': '{"level": 1}', 'depth': 0},
        {'type': 'paragraph', 'content': 'First paragraph.', 'depth': 0},
        {'type': 'paragraph', 'content': 'Second paragraph.', 'depth': 0},
    ]
    result = reconstruct(rows)
    expected = '# Title\n\nFirst paragraph.\n\nSecond paragraph.\n'
    assert result == expected, f"Got: {result!r}"
    print("✅ test_reconstruct_multiple_blocks PASSED")


def test_reconstruct_skips_nested():
    """Nested elements (depth > 0) should not appear in reconstruction."""
    rows = [
        {'type': 'heading', 'content': 'Title', 'attrs_json': '{"level": 1}', 'depth': 0},
        {'type': 'strong', 'content': 'bold', 'depth': 1},
    ]
    result = reconstruct(rows)
    assert '**bold**' not in result  # nested inline shouldn't render as block
    assert result == '# Title\n'
    print("✅ test_reconstruct_skips_nested PASSED")


def test_reconstruct_empty():
    assert reconstruct([]) == ''
    print("✅ test_reconstruct_empty PASSED")


def test_render_frontmatter_with_content():
    """Frontmatter renders as ---\ncontent\n---."""
    row = {'type': 'frontmatter', 'content': 'title: Test\ndate: 2024'}
    result = render_single(row)
    assert result == '---\ntitle: Test\ndate: 2024\n---'
    print("✅ test_render_frontmatter_with_content PASSED")


def test_reconstruct_with_frontmatter():
    """Full reconstruction with frontmatter + heading."""
    rows = [
        {'type': 'frontmatter', 'content': 'title: Doc', 'attrs_json': '{}', 'depth': 0},
        {'type': 'heading', 'content': 'Title', 'attrs_json': '{"level": 1}', 'depth': 0},
    ]
    result = reconstruct(rows)
    expected = '---\ntitle: Doc\n---\n\n# Title\n'
    assert result == expected, f"Got: {result!r}"
    print("✅ test_reconstruct_with_frontmatter PASSED")


if __name__ == '__main__':
    test_render_heading()
    test_render_paragraph()
    test_render_code_block()
    test_render_list_item_unordered()
    test_render_list_item_ordered()
    test_render_list_item_indented()
    test_render_blockquote()
    test_render_table()
    test_render_hr()
    test_render_frontmatter()
    test_render_html()
    test_render_inline_strong()
    test_render_inline_emphasis()
    test_render_inline_code()
    test_render_inline_strikethrough()
    test_render_link()
    test_render_link_with_title()
    test_render_image()
    test_reconstruct_multiple_blocks()
    test_reconstruct_skips_nested()
    test_reconstruct_empty()
    print("\n🎉 All reconstructor tests passed!")
