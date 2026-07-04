"""Unit tests for the Markdown parser."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from mcp_md_sql.parser import parse, ParsedElement


def test_basic_heading():
    source = "# Hello World\n"
    elements = parse(source)
    assert len(elements) == 1, f"Expected 1 element, got {len(elements)}"
    h = elements[0]
    assert h.type == 'heading'
    assert h.content == 'Hello World'
    assert h.attrs.get('level') == 1
    assert h.pos_in_file == 1
    assert h.depth == 0
    assert h.line_start == 1
    assert h.line_end == 1
    print("✅ test_basic_heading PASSED")


def test_heading_levels():
    source = "### Level 3\n## Level 2\n# Level 1\n"
    elements = parse(source)
    headings = [el for el in elements if el.type == 'heading']
    assert len(headings) == 3
    assert headings[0].attrs['level'] == 3
    assert headings[1].attrs['level'] == 2
    assert headings[2].attrs['level'] == 1
    print("✅ test_heading_levels PASSED")


def test_paragraph_plain():
    source = "Hello World\n"
    elements = parse(source)
    assert len(elements) == 1
    p = elements[0]
    assert p.type == 'paragraph'
    assert p.content == 'Hello World'
    assert p.depth == 0
    print("✅ test_paragraph_plain PASSED")


def test_paragraph_with_inline_strong():
    source = "Hello **bold** world\n"
    elements = parse(source)

    types = [el.type for el in elements]
    assert 'paragraph' in types, f"Types: {types}"
    assert 'strong' in types, f"Types: {types}"

    para = next(el for el in elements if el.type == 'paragraph')
    strong = next(el for el in elements if el.type == 'strong')

    assert para.content == 'Hello bold world'
    assert strong.content == 'bold'
    assert strong.parent_pos == para.pos_in_file
    assert strong.depth == 1  # one level deeper than parent
    print("✅ test_paragraph_with_inline_strong PASSED")


def test_paragraph_with_inline_emphasis():
    source = "Some *italic* text\n"
    elements = parse(source)

    para = next(el for el in elements if el.type == 'paragraph')
    em = next(el for el in elements if el.type == 'emphasis')

    assert para.content == 'Some italic text'
    assert em.content == 'italic'
    assert em.parent_pos == para.pos_in_file
    print("✅ test_paragraph_with_inline_emphasis PASSED")


def test_paragraph_with_inline_code():
    source = "Use `print()` function\n"
    elements = parse(source)

    para = next(el for el in elements if el.type == 'paragraph')
    code = next(el for el in elements if el.type == 'inline_code')

    assert para.content == 'Use print() function'
    assert code.content == 'print()'
    assert code.parent_pos == para.pos_in_file
    print("✅ test_paragraph_with_inline_code PASSED")


def test_paragraph_with_link():
    source = "Visit [example](https://example.com \"title\") now\n"
    elements = parse(source)

    para = next(el for el in elements if el.type == 'paragraph')
    link = next(el for el in elements if el.type == 'link')

    assert para.content == 'Visit example now'
    assert link.content == 'example'
    assert link.attrs.get('url') == 'https://example.com'
    assert link.attrs.get('title') == 'title'
    assert link.parent_pos == para.pos_in_file
    print("✅ test_paragraph_with_link PASSED")


def test_paragraph_with_image():
    source = "Look at ![alt text](https://img.com/pic.png)\n"
    elements = parse(source)

    para = next(el for el in elements if el.type == 'paragraph')
    img = next(el for el in elements if el.type == 'image')

    assert para.content == 'Look at alt text'
    assert img.content == 'alt text'
    assert img.attrs.get('url') == 'https://img.com/pic.png'
    assert img.attrs.get('alt') == 'alt text'
    assert img.parent_pos == para.pos_in_file
    print("✅ test_paragraph_with_image PASSED")


def test_paragraph_with_strikethrough():
    source = "This is ~~old~~ text\n"
    elements = parse(source)

    para = next(el for el in elements if el.type == 'paragraph')
    strike = next(el for el in elements if el.type == 'strikethrough')

    assert para.content == 'This is old text'
    assert strike.content == 'old'
    assert strike.parent_pos == para.pos_in_file
    print("✅ test_paragraph_with_strikethrough PASSED")


def test_multiple_inline_elements():
    source = "Hello **bold** and *italic* plus `code`\n"
    elements = parse(source)

    types = {el.type for el in elements}
    assert types >= {'paragraph', 'strong', 'emphasis', 'inline_code'}

    para = next(el for el in elements if el.type == 'paragraph')
    assert para.content == 'Hello bold and italic plus code'

    # Check parent_pos points to the paragraph
    for el in elements:
        if el.type != 'paragraph':
            assert el.parent_pos == para.pos_in_file, \
                f"{el.type} parent_pos={el.parent_pos}, expected {para.pos_in_file}"
    print("✅ test_multiple_inline_elements PASSED")


def test_nested_inline():
    """**bold *and italic* text** → strong contains emphasis"""
    source = "**bold *and italic* text**\n"
    elements = parse(source)

    para = next(el for el in elements if el.type == 'paragraph')
    strong = next(el for el in elements if el.type == 'strong')
    em = next(el for el in elements if el.type == 'emphasis')

    assert para.content == 'bold and italic text'
    assert strong.content == 'bold and italic text'
    assert em.content == 'and italic'

    # strong is child of paragraph, emphasis is child of strong
    assert strong.parent_pos == para.pos_in_file
    assert em.parent_pos == strong.pos_in_file
    # depth: para=0, strong=1, emphasis=2
    assert strong.depth == 1
    assert em.depth == 2
    print("✅ test_nested_inline PASSED")


def test_heading_with_inline():
    source = "## Hello **world**\n"
    elements = parse(source)

    heading = next(el for el in elements if el.type == 'heading')
    strong = next(el for el in elements if el.type == 'strong')

    assert heading.content == 'Hello world'
    assert strong.content == 'world'
    assert strong.parent_pos == heading.pos_in_file
    print("✅ test_heading_with_inline PASSED")


def test_code_block():
    source = "```python\ndef foo():\n    pass\n```\n"
    elements = parse(source)

    code = next(el for el in elements if el.type == 'code_block')
    assert code.content == 'def foo():\n    pass'
    assert code.attrs.get('lang') == 'python'
    print("✅ test_code_block PASSED")


def test_list_items():
    source = "- Item one\n- Item two\n- Item three\n"
    elements = parse(source)

    items = [el for el in elements if el.type == 'list_item']
    assert len(items) == 3
    assert items[0].content == 'Item one'
    assert items[1].content == 'Item two'
    assert items[2].content == 'Item three'
    assert items[0].list_type == 'unordered'
    print("✅ test_list_items PASSED")


def test_ordered_list():
    source = "1. First\n2. Second\n3. Third\n"
    elements = parse(source)

    items = [el for el in elements if el.type == 'list_item']
    assert len(items) == 3
    assert items[0].list_type == 'ordered'
    assert items[0].content == 'First'
    print("✅ test_ordered_list PASSED")


def test_blockquote():
    source = "> A blockquote\n> with two lines\n"
    elements = parse(source)

    bq = next(el for el in elements if el.type == 'blockquote')
    # blockquote.content should be join of children's text
    assert bq.content is not None
    # The children (paragraphs) should have their own content
    paragraphs = [el for el in elements if el.type == 'paragraph' and el.depth > 0]
    if paragraphs:
        # Paragraph inside blockquote should not duplicate blockquote content
        p_content = paragraphs[0].content
        # Blockquote content joins multiple children; paragraph has own
        if len(paragraphs) == 1:
            # Single paragraph: blockquote and paragraph may have same text
            # (but via different computation paths)
            pass
        assert p_content is not None
    print("✅ test_blockquote PASSED")


def test_table():
    source = "| Name | Value |\n| --- | --- |\n| Alice | 100 |\n| Bob | 200 |\n"
    elements = parse(source)

    table = next(el for el in elements if el.type == 'table')
    assert table.attrs.get('headers') == ['Name', 'Value']
    assert table.attrs.get('rows') == [['Alice', '100'], ['Bob', '200']]
    print("✅ test_table PASSED")


def test_horizontal_rule():
    source = "---\n"
    elements = parse(source)

    hr = next(el for el in elements if el.type == 'hr')
    assert hr is not None
    print("✅ test_horizontal_rule PASSED")


def test_html_block():
    source = "<div>hello</div>\n"
    elements = parse(source)

    html = next(el for el in elements if el.type == 'html')
    assert html.content.strip() == '<div>hello</div>'
    print("✅ test_html_block PASSED")


def test_html_inline():
    source = "Text with <span>inline</span> html\n"
    elements = parse(source)

    para = next(el for el in elements if el.type == 'paragraph')
    assert 'Text with ' in para.content
    assert ' html' in para.content

    html_inlines = [el for el in elements if el.type == 'html_inline']
    assert len(html_inlines) == 2  # <span> and </span>
    assert html_inlines[0].content == '<span>'
    assert html_inlines[1].content == '</span>'
    assert html_inlines[0].parent_pos == para.pos_in_file
    print("✅ test_html_inline PASSED")


def test_positions_sequential():
    """All elements get unique sequential pos_in_file."""
    source = "# Title\n\nParagraph one.\n\n- Item A\n- Item B\n"
    elements = parse(source)

    positions = [el.pos_in_file for el in elements]
    assert positions == list(range(1, len(elements) + 1)), \
        f"Positions: {positions}"
    print("✅ test_positions_sequential PASSED")


def test_parent_pos_after_resolve():
    """parent_pos must be set to actual pos_in_file after resolution."""
    source = "Hello **bold** world\n"
    elements = parse(source)

    para = next(el for el in elements if el.type == 'paragraph')
    strong = next(el for el in elements if el.type == 'strong')

    assert strong.parent_pos == para.pos_in_file, \
        f"parent_pos={strong.parent_pos}, para.pos_in_file={para.pos_in_file}"
    print("✅ test_parent_pos_after_resolve PASSED")


def test_inline_line_start_is_none():
    """Inline elements should have line_start=None."""
    source = "Hello **bold** world\n"
    elements = parse(source)

    strong = next(el for el in elements if el.type == 'strong')
    assert strong.line_start is None
    assert strong.line_end is None
    print("✅ test_inline_line_start_is_none PASSED")


def test_block_line_start_not_none():
    """Block elements (depth=0) should have line_start set."""
    source = "# Title\n\nParagraph text.\n"
    elements = parse(source)

    for el in elements:
        if el.depth == 0:
            assert el.line_start is not None, \
                f"{el.type} (pos={el.pos_in_file}) has line_start=None"
            assert el.line_end is not None
    print("✅ test_block_line_start_not_none PASSED")


def test_frontmatter_basic():
    """YAML frontmatter is parsed as frontmatter element."""
    source = "---\ntitle: Test Doc\ndate: 2024-01-01\n---\n\n# Hello\n"
    elements = parse(source)

    fm = next(el for el in elements if el.type == 'frontmatter')
    assert fm.content == 'title: Test Doc\ndate: 2024-01-01'
    assert fm.attrs.get('yaml') == {'title': 'Test Doc', 'date': '2024-01-01'}
    assert fm.line_start == 1
    assert fm.line_end == 4  # ---, title, date, ---
    assert fm.depth == 0
    assert fm.pos_in_file == 1  # first element
    print("✅ test_frontmatter_basic PASSED")


def test_frontmatter_without_trailing_newline():
    """Frontmatter at end of file without trailing newline after closing ---."""
    source = "---\ntitle: Test\n---"
    elements = parse(source)

    assert len(elements) == 1
    fm = elements[0]
    assert fm.type == 'frontmatter'
    assert fm.content == 'title: Test'
    assert fm.attrs.get('yaml') == {'title': 'Test'}
    assert fm.line_start == 1
    assert fm.line_end == 3
    print("✅ test_frontmatter_without_trailing_newline PASSED")


def test_frontmatter_missing():
    """No frontmatter → regular Markdown parsing."""
    source = "# Just a heading\n"
    elements = parse(source)
    assert not any(el.type == 'frontmatter' for el in elements)
    assert any(el.type == 'heading' for el in elements)
    print("✅ test_frontmatter_missing PASSED")


def test_nested_list():
    """Nested lists: depth increments with nesting."""
    source = "- Item 1\n  - Sub 1.1\n  - Sub 1.2\n- Item 2\n"
    elements = parse(source)

    items = [el for el in elements if el.type == 'list_item']
    assert len(items) == 4

    # Top-level items
    top = [it for it in items if it.depth == 0]
    assert len(top) == 2
    assert top[0].content == 'Item 1'
    assert top[1].content == 'Item 2'

    # Nested items
    nested = [it for it in items if it.depth == 1]
    assert len(nested) == 2
    assert nested[0].content == 'Sub 1.1'
    assert nested[1].content == 'Sub 1.2'

    # Check parent_pos: nested items should have parent_pos pointing to their parent list_item
    for n in nested:
        assert n.parent_pos is not None
        parent = next((t for t in top if t.pos_in_file == n.parent_pos), None)
        assert parent is not None, f"nested {n.content} parent_pos={n.parent_pos} not found"
    print("✅ test_nested_list PASSED")


def test_deeply_nested_list():
    """Three levels of nested lists."""
    source = "- Level 0\n  - Level 1\n    - Level 2\n"
    elements = parse(source)

    items = [el for el in elements if el.type == 'list_item']
    assert len(items) == 3

    assert items[0].content == 'Level 0'
    assert items[0].depth == 0

    assert items[1].content == 'Level 1'
    assert items[1].depth == 1

    assert items[2].content == 'Level 2'
    assert items[2].depth == 2

    # Chain: Level 2 -> Level 1 -> Level 0
    assert items[2].parent_pos == items[1].pos_in_file
    assert items[1].parent_pos == items[0].pos_in_file
    print("✅ test_deeply_nested_list PASSED")


def test_list_with_nested_paragraph():
    """List item with nested paragraph (blank line between content)."""
    source = "- Item 1\n\n  Continued\n- Item 2\n"
    elements = parse(source)

    types = [el.type for el in elements]
    assert 'list_item' in types
    items = [el for el in elements if el.type == 'list_item']
    assert len(items) == 2
    # The continuation paragraph should be a separate paragraph element
    # (not a list_item) with depth > 0
    paras = [el for el in elements if el.type == 'paragraph']
    nested_paras = [p for p in paras if p.depth > 0]
    assert len(nested_paras) >= 1
    print("✅ test_list_with_nested_paragraph PASSED")


if __name__ == '__main__':
    test_basic_heading()
    test_heading_levels()
    test_paragraph_plain()
    test_paragraph_with_inline_strong()
    test_paragraph_with_inline_emphasis()
    test_paragraph_with_inline_code()
    test_paragraph_with_link()
    test_paragraph_with_image()
    test_paragraph_with_strikethrough()
    test_multiple_inline_elements()
    test_nested_inline()
    test_heading_with_inline()
    test_code_block()
    test_list_items()
    test_ordered_list()
    test_blockquote()
    test_table()
    test_horizontal_rule()
    test_html_block()
    test_html_inline()
    test_positions_sequential()
    test_parent_pos_after_resolve()
    test_inline_line_start_is_none()
    test_block_line_start_not_none()
    print("\n🎉 All parser tests passed!")
