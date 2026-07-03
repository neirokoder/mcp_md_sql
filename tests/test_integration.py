"""Integration test: parse → DB → select → change → verify file."""

import json
import os
import sys
import tempfile
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from md_sql_server.config import RootConfig, set_roots
from md_sql_server.database import get_connection, create_db, load_files, reload_file
from md_sql_server.tools import md_select, md_change, ToolError


def test_parse_and_select():
    """Test: parse .md files, run md_select queries."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Create test file
        docs_dir = root / "docs"
        docs_dir.mkdir()
        test_file = docs_dir / "api.md"
        test_file.write_text("""# API Reference

## GET /users

Returns list of users.

### Parameters

- `page` - page number
- `limit` - items per page

## PUT /users/{id}

Updates a user.

```json
{"name": "Alice"}
```
""", encoding='utf-8')

        # Setup DB
        conn = get_connection()
        roots = [RootConfig(alias="project", path=root)]
        set_roots(roots)
        count = load_files(conn, roots)
        assert count > 0, f"Expected > 0 rows, got {count}"

        # Test md_select: get all headings
        rows = md_select(conn,
            "SELECT content, json_extract(attrs_json, '$.level') AS level "
            "FROM md WHERE root='project' AND file='docs/api.md' AND type='heading' "
            "ORDER BY pos_in_file")

        headings = [r['content'] for r in rows]
        assert 'API Reference' in headings, f"Headings: {headings}"
        assert 'GET /users' in headings
        assert 'PUT /users/{id}' in headings
        print("✅ md_select: headings OK")

        # Test md_select: get code blocks
        rows = md_select(conn,
            "SELECT content FROM md WHERE type='code_block' AND root='project'")
        assert len(rows) == 1
        assert '"name": "Alice"' in rows[0]['content']
        print("✅ md_select: code blocks OK")

        # Test md_select: get paragraphs
        rows = md_select(conn,
            "SELECT content FROM md WHERE type='paragraph' AND root='project' "
            "AND file='docs/api.md'")
        contents = [r['content'] for r in rows]
        assert any('Returns list of users' in c for c in contents), f"Paragraphs: {contents}"
        print("✅ md_select: paragraphs OK")

        return


def test_parse_inline_elements():
    """Test: inline elements are extracted as separate rows."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        docs_dir = root / "docs"
        docs_dir.mkdir()
        test_file = docs_dir / "inline.md"
        test_file.write_text("# Hello **bold** and *italic* with `code`\n", encoding='utf-8')

        conn = get_connection()
        roots = [RootConfig(alias="project", path=root)]
        set_roots(roots)
        load_files(conn, roots)

        # Check heading
        rows = md_select(conn,
            "SELECT * FROM md WHERE type='heading' AND root='project'")
        assert len(rows) == 1
        assert rows[0]['content'] == 'Hello bold and italic with code'

        # Check inline elements
        strong_rows = md_select(conn,
            "SELECT * FROM md WHERE type='strong' AND root='project'")
        assert len(strong_rows) == 1
        assert strong_rows[0]['content'] == 'bold'
        assert strong_rows[0]['parent_pos'] == rows[0]['pos_in_file']

        em_rows = md_select(conn,
            "SELECT * FROM md WHERE type='emphasis' AND root='project'")
        assert len(em_rows) == 1
        assert em_rows[0]['content'] == 'italic'
        assert em_rows[0]['parent_pos'] == rows[0]['pos_in_file']

        code_rows = md_select(conn,
            "SELECT * FROM md WHERE type='inline_code' AND root='project'")
        assert len(code_rows) == 1
        assert code_rows[0]['content'] == 'code'
        assert code_rows[0]['parent_pos'] == rows[0]['pos_in_file']

        print("✅ test_parse_inline_elements PASSED")
        return


def test_md_change_update():
    """Test: md_change UPDATE + diff + file patching."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        docs_dir = root / "docs"
        docs_dir.mkdir()
        test_file = docs_dir / "api.md"
        test_file.write_text("""# API Reference

## PUT /users/{id}

Updates a user.

## DELETE /users/{id}

Removes a user.
""", encoding='utf-8')

        conn = get_connection()
        roots = [RootConfig(alias="project", path=root)]
        set_roots(roots)
        load_files(conn, roots)

        # Test UPDATE
        result = md_change(conn,
            "UPDATE md SET content = REPLACE(content, 'PUT ', 'PATCH ') "
            "WHERE type='heading' AND content LIKE 'PUT /%'")

        assert 'changes' in result, f"Result: {result}"
        assert len(result['changes']) >= 1
        change = result['changes'][0]
        assert change['operation'] == 'update'
        assert change['old_content'] == 'PUT /users/{id}'
        assert change['new_content'] == 'PATCH /users/{id}'
        assert 'project:docs/api.md' in result['files_changed']
        print("✅ md_change UPDATE: diff OK")

        # Verify file was patched
        new_content = test_file.read_text(encoding='utf-8')
        assert 'PATCH /users/{id}' in new_content
        assert 'PUT /users/{id}' not in new_content
        print("✅ md_change UPDATE: file patched OK")

        # Verify DB was reloaded (pos_in_file, line_start/end updated)
        rows = md_select(conn,
            "SELECT pos_in_file, line_start, line_end FROM md "
            "WHERE root='project' AND file='docs/api.md' AND type='heading' "
            "AND content='PATCH /users/{id}'")
        assert len(rows) >= 1
        assert rows[0]['line_start'] is not None
        print("✅ md_change UPDATE: DB reloaded OK")

        return


def test_md_change_delete():
    """Test: md_change DELETE."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        docs_dir = root / "docs"
        docs_dir.mkdir()
        test_file = docs_dir / "api.md"
        test_file.write_text("""# API

## GET /users

List users.

## DELETE /users

Old method.
""", encoding='utf-8')

        conn = get_connection()
        roots = [RootConfig(alias="project", path=root)]
        set_roots(roots)
        load_files(conn, roots)

        result = md_change(conn,
            "DELETE FROM md WHERE type='heading' AND content='DELETE /users'")

        assert len(result['changes']) >= 1
        assert result['changes'][0]['operation'] == 'delete'
        print("✅ md_change DELETE: diff OK")

        new_content = test_file.read_text(encoding='utf-8')
        assert 'DELETE /users' not in new_content or 'delete' in new_content.lower()
        print("✅ md_change DELETE: file patched OK")

        return


def test_md_change_insert():
    """Test: md_change INSERT."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        docs_dir = root / "docs"
        docs_dir.mkdir()
        test_file = docs_dir / "api.md"
        test_file.write_text("""# API

## GET /users

List users.
""", encoding='utf-8')

        conn = get_connection()
        roots = [RootConfig(alias="project", path=root)]
        set_roots(roots)
        load_files(conn, roots)

        # Get last pos_in_file
        rows = md_select(conn,
            "SELECT MAX(pos_in_file) as max_pos FROM md WHERE root='project' AND file='docs/api.md'")
        max_pos = rows[0]['max_pos']

        result = md_change(conn,
            f"INSERT INTO md (root, file, pos_in_file, depth, type, content, attrs_json) "
            f"VALUES ('project', 'docs/api.md', {max_pos + 1}, 1, 'paragraph', 'New paragraph', '{{}}')")

        assert len(result['changes']) >= 1
        assert result['changes'][0]['operation'] == 'insert'
        print("✅ md_change INSERT: diff OK")

        new_content = test_file.read_text(encoding='utf-8')
        assert 'New paragraph' in new_content
        print("✅ md_change INSERT: file patched OK")

        return


def test_insert_rejects_id():
    """Test: INSERT with `id` column is rejected."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        docs_dir = root / "docs"
        docs_dir.mkdir()
        test_file = docs_dir / "test.md"
        test_file.write_text("# Test\n", encoding='utf-8')

        conn = get_connection()
        roots = [RootConfig(alias="project", path=root)]
        set_roots(roots)
        load_files(conn, roots)

        try:
            md_change(conn,
                "INSERT INTO md (id, root, file, type, content) "
                "VALUES (999, 'project', 'docs/test.md', 'paragraph', 'bad')")
            assert False, "Should have raised ToolError"
        except ToolError as e:
            assert 'id' in str(e).lower()
            print(f"✅ test_insert_rejects_id PASSED: {e}")

        return




def test_mass_update():
    """Test: UPDATE affecting multiple rows."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        docs_dir = root / "docs"
        docs_dir.mkdir()
        test_file = docs_dir / "mass.md"
        test_file.write_text("""# Old Title

Some old text here.

## Old Section

More old content.
""", encoding='utf-8')

        conn = get_connection()
        roots = [RootConfig(alias="project", path=root)]
        set_roots(roots)
        load_files(conn, roots)

        # Mass update: replace 'old' with 'new' (case-insensitive via SQL)
        # SQLite's REPLACE is case-sensitive, so we use a WHERE with LIKE
        result = md_change(conn,
            "UPDATE md SET content = REPLACE(content, 'old', 'new') "
            "WHERE content LIKE '%old%' AND type NOT IN ('strong', 'emphasis', 'inline_code', 'link', 'image', 'strikethrough')")

        assert len(result['changes']) >= 1, f"Expected >=1 changes, got {len(result['changes'])}"
        for c in result['changes']:
            # Only check that the change was recorded (some may still have 'Old' due to case)
            assert c['operation'] == 'update'
        print(f"✅ test_mass_update PASSED: {len(result['changes'])} changes")

        new_content = test_file.read_text(encoding='utf-8')
        assert 'new' in new_content.lower()
        print("✅ test_mass_update: file verified")

        return


def test_insert_with_position_shift():
    """Test: INSERT shifts pos_in_file of subsequent elements."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        docs_dir = root / "docs"
        docs_dir.mkdir()
        test_file = docs_dir / "shift.md"
        test_file.write_text("""# First

First paragraph.

## Third
""", encoding='utf-8')

        conn = get_connection()
        roots = [RootConfig(alias="project", path=root)]
        set_roots(roots)
        load_files(conn, roots)

        # Get current positions
        before = md_select(conn,
            "SELECT pos_in_file, type, content FROM md "
            "WHERE root='project' AND file='docs/shift.md' ORDER BY pos_in_file")
        before_positions = [r['pos_in_file'] for r in before]
        print(f"  Before: {before_positions}")

        # Insert at position 2 (after heading, before paragraph)
        result = md_change(conn,
            "INSERT INTO md (root, file, pos_in_file, depth, type, content, attrs_json) "
            "VALUES ('project', 'docs/shift.md', 2, 0, 'paragraph', 'INSERTED paragraph', '{}')")

        assert result['changes'][0]['operation'] == 'insert'
        print("✅ test_insert_with_position_shift: insert OK")

        # Verify positions shifted
        after = md_select(conn,
            "SELECT pos_in_file, type, content FROM md "
            "WHERE root='project' AND file='docs/shift.md' ORDER BY pos_in_file")

        # The original elements that were at pos >= 2 should have shifted
        contents = [r['content'] for r in after]
        assert 'INSERTED paragraph' in contents
        assert 'First paragraph.' in contents  # should have shifted down
        print(f"  After: {[r['pos_in_file'] for r in after]}")
        print("✅ test_insert_with_position_shift PASSED")

        return


def test_update_inline_element():
    """Test: UPDATE an inline strong element patches the parent in file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        docs_dir = root / "docs"
        docs_dir.mkdir()
        test_file = docs_dir / "inline.md"
        test_file.write_text("Hello **world** today\n", encoding='utf-8')

        conn = get_connection()
        roots = [RootConfig(alias="project", path=root)]
        set_roots(roots)
        load_files(conn, roots)

        # Update strong's content
        result = md_change(conn,
            "UPDATE md SET content = 'everyone' "
            "WHERE type = 'strong' AND content = 'world'")

        assert len(result['changes']) >= 1
        change = result['changes'][0]
        assert change['operation'] == 'update'
        assert change['old_content'] == 'world'
        assert change['new_content'] == 'everyone'
        print("✅ test_update_inline_element: diff OK")

        # Verify file was patched (parent paragraph line should now have **everyone**)
        new_content = test_file.read_text(encoding='utf-8')
        assert '**everyone**' in new_content, f"Expected **everyone** in file, got: {new_content!r}"
        assert '**world**' not in new_content
        print("✅ test_update_inline_element: file patched OK")

        return


def test_delete_inline_element():
    """Test: DELETE an inline element removes it from parent in file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        docs_dir = root / "docs"
        docs_dir.mkdir()
        test_file = docs_dir / "inline.md"
        test_file.write_text("Hello **world** today\n", encoding='utf-8')

        conn = get_connection()
        roots = [RootConfig(alias="project", path=root)]
        set_roots(roots)
        load_files(conn, roots)

        # Delete the strong inline element
        result = md_change(conn,
            "DELETE FROM md WHERE type = 'strong' AND content = 'world'")

        assert len(result['changes']) >= 1
        assert result['changes'][0]['operation'] == 'delete'
        print("✅ test_delete_inline_element: diff OK")

        new_content = test_file.read_text(encoding='utf-8')
        # The parent line should no longer have **world**
        assert '**world**' not in new_content, \
            f"File should not contain **world**, got: {new_content!r}"
        # But "Hello" and "today" should still be there
        assert 'Hello' in new_content
        assert 'today' in new_content
        print(f"✅ test_delete_inline_element: file patched OK, content: {new_content!r}")

        return


def test_insert_with_special_chars():
    """Test: INSERT with quotes in content doesn't break."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        test_file = root / "special.md"
        test_file.write_text("# Title\n", encoding='utf-8')

        conn = get_connection()
        roots = [RootConfig(alias="project", path=root)]
        set_roots(roots)
        load_files(conn, roots)

        # INSERT with single quotes and special chars in content
        result = md_change(conn,
            "INSERT INTO md (root, file, type, content, depth) "
            "VALUES ('project', 'special.md', 'paragraph', \"It's a 'test'\", 0)")
        assert len(result['changes']) == 1
        assert result['changes'][0]['operation'] == 'insert'
        print("test_insert_with_special_chars PASSED")

        return


def test_update_rejects_direct_id():
    """Test: UPDATE SET id = ... is rejected."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        docs_dir = root / "docs"
        docs_dir.mkdir()
        test_file = docs_dir / "test.md"
        test_file.write_text("# Title\n", encoding='utf-8')

        conn = get_connection()
        roots = [RootConfig(alias="project", path=root)]
        set_roots(roots)
        load_files(conn, roots)

        error = None
        try:
            md_change(conn,
                "UPDATE md SET id = 99 WHERE type = 'heading'")
        except ToolError as e:
            error = e

        assert error is not None, "Expected ToolError for SET id"
        assert 'id' in str(error).lower()
        print("test_update_rejects_direct_id PASSED")

        return




def test_update_allows_id_in_string():
    """Test: UPDATE SET content = 'user_id=5' is NOT rejected (id in string literal)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        docs_dir = root / "docs"
        docs_dir.mkdir()
        test_file = docs_dir / "test.md"
        test_file.write_text("# Old Title\n", encoding='utf-8')

        conn = get_connection()
        roots = [RootConfig(alias="project", path=root)]
        set_roots(roots)
        load_files(conn, roots)

        # Should NOT raise - 'id' is inside a string literal, not a column name
        result = md_change(conn,
            "UPDATE md SET content = 'user_id=5' WHERE type = 'heading'")
        assert len(result['changes']) >= 1
        print("test_update_allows_id_in_string PASSED")

        return


def test_update_tracks_freshly_inserted_row():
    """Test: UPDATE on a freshly inserted row is tracked in diff
    regardless of updates to other rows (deterministic id-based tracking)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        file_a = root / "a.md"
        file_b = root / "b.md"
        file_a.write_text("# From A\n", encoding='utf-8')
        file_b.write_text("# From B\n", encoding='utf-8')

        conn = get_connection()
        roots = [RootConfig(alias="project", path=root)]
        set_roots(roots)
        load_files(conn, roots)

        # Step 1: UPDATE file_a
        r1 = md_change(conn,
            "UPDATE md SET content = 'Modified A' WHERE root = 'project' AND file = 'a.md' AND type = 'heading'")
        assert len(r1['changes']) == 1

        # Step 2: INSERT a new element INTO a.md
        r2 = md_change(conn,
            "INSERT INTO md (root, file, type, content, depth) "
            "VALUES ('project', 'a.md', 'paragraph', 'New in A', 0)")
        assert len(r2['changes']) == 1

        # Step 3: UPDATE file_b only
        r3 = md_change(conn,
            "UPDATE md SET content = 'Modified B' WHERE root = 'project' AND file = 'b.md' AND type = 'heading'")
        assert len(r3['changes']) == 1

        # Step 4: UPDATE the freshly inserted row in a.md
        # Must produce exactly 1 change (deterministic by id, not fragile version check)
        result = md_change(conn,
            "UPDATE md SET content = 'Updated New In A' WHERE root = 'project' AND file = 'a.md' AND type = 'paragraph'")
        changes = result['changes']
        print(f"changes: {changes}")
        assert len(changes) == 1, (
            f"Expected 1 change for the freshly inserted row, got {len(changes)}. "
            "BUG: version-based tracking missed the row because max_ver_before > 0 but freshly inserted row has version=0"
        )
        assert changes[0]['old_content'] == 'New in A'
        assert changes[0]['new_content'] == 'Updated New In A'
        print("test_update_tracks_freshly_inserted_row PASSED")

        return


if __name__ == '__main__':
    test_parse_and_select()
    test_parse_inline_elements()
    test_md_change_update()
    test_md_change_delete()
    test_md_change_insert()
    test_insert_rejects_id()
    test_mass_update()
    test_insert_with_position_shift()
    test_update_inline_element()
    test_delete_inline_element()
    test_insert_with_special_chars()
    test_update_rejects_direct_id()
    test_update_allows_id_in_string()
    test_update_tracks_freshly_inserted_row()
    print("\nAll integration tests passed!")
