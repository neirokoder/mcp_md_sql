"""MCP Inspector-style integration tests.

Tests the server through the actual MCP protocol transport (stdio),
simulating what the MCP Inspector web UI does:
  - list_tools → discover available tools
  - call_tool  → invoke md_select / md_change with various scenarios

Also tests multi-project support by running the server with multiple DOC_ROOTS.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession


# ── helpers ────────────────────────────────────────────────────────────────────


def _create_project(path: Path, alias: str) -> Path:
    """Create a project directory with a sample .md file."""
    docs = path / "docs"
    docs.mkdir(parents=True, exist_ok=True)

    (docs / f"{alias}_readme.md").write_text(
        f"""# {alias.title()} Project

This is the **{alias}** project documentation.

## Quick Start

Run the following command:

```bash
echo "Hello from {alias}"
```

| Param | Value |
|-------|-------|
| Name  | {alias} |
| Port  | 8080   |
""",
        encoding='utf-8',
    )
    return path


def _make_server_params(roots: list[tuple[str, str]]) -> StdioServerParameters:
    """Build stdio server parameters with DOC_ROOTS env pointing to temp dirs.

    Each tuple is (alias, absolute_path).
    """
    roots_json = json.dumps([p for _, p in roots])
    env = dict(os.environ)
    env["DOC_ROOTS"] = roots_json
    env["PYTHONUNBUFFERED"] = "1"
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_md_sql.server"],
        env=env,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def single_project(tmp_path: Path) -> StdioServerParameters:
    """Server with a single project root."""
    root = _create_project(tmp_path / "alpha", "alpha")
    return _make_server_params([("alpha", str(root))])


@pytest.fixture
def multi_project(tmp_path: Path) -> StdioServerParameters:
    """Server with two independent project roots."""
    root1 = _create_project(tmp_path / "project_a", "project_a")
    root2 = _create_project(tmp_path / "project_b", "project_b")
    return _make_server_params([
        ("project_a", str(root1)),
        ("project_b", str(root2)),
    ])


@pytest.fixture
def no_roots_server(tmp_path: Path) -> StdioServerParameters:
    """Server started without DOC_ROOTS — no roots will be available."""
    # Create a .md file in tmp_path (it won't be loaded without a root)
    (tmp_path / "welcome.md").write_text("# Welcome\n\nDefault root document.\n", encoding='utf-8')
    env = dict(os.environ)
    env.pop("DOC_ROOTS", None)
    env["PYTHONUNBUFFERED"] = "1"
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_md_sql.server"],
        cwd=str(tmp_path),
        env=env,
    )


@pytest.fixture
def env_with_empty_docs(tmp_path: Path) -> StdioServerParameters:
    """Server with a root that exists but has no .md files."""
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    env = dict(os.environ)
    env["DOC_ROOTS"] = json.dumps([str(empty_root)])
    env["PYTHONUNBUFFERED"] = "1"
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_md_sql.server"],
        env=env,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Test: list_tools — discover tools via MCP protocol
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_list_tools(single_project: StdioServerParameters):
    """list_tools returns md_select and md_change."""
    async with stdio_client(single_project) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = await session.list_tools()
            tools = {t.name for t in result.tools}

    assert "md_select" in tools, f"Expected md_select, got {tools}"
    assert "md_change" in tools, f"Expected md_change, got {tools}"


# ═══════════════════════════════════════════════════════════════════════════════
# Test: md_select — single project
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_md_select_headings(single_project: StdioServerParameters):
    """md_select returns headings from a single project."""
    async with stdio_client(single_project) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = await session.call_tool("md_select", {
                "sql": "SELECT root, file, type, content FROM md "
                       "WHERE type='heading' ORDER BY pos_in_file",
            })

    text = "".join(c.text for c in result.content)
    assert "Alpha Project" in text, f"Heading not found:\n{text}"
    assert "heading" in text, f"Type not found:\n{text}"
    assert "Quick Start" in text, f"Sub-heading not found:\n{text}"
    assert "alpha_readme.md" in text, f"Filename not found:\n{text}"


@pytest.mark.asyncio
async def test_md_select_code_blocks(single_project: StdioServerParameters):
    """md_select returns code blocks."""
    async with stdio_client(single_project) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = await session.call_tool("md_select", {
                "sql": "SELECT content FROM md "
                       "WHERE type='code_block'",
            })

    text = "".join(c.text for c in result.content)
    assert "Hello from alpha" in text, f"Code content not found:\n{text}"


@pytest.mark.asyncio
async def test_md_select_tables(single_project: StdioServerParameters):
    """md_select returns table rows."""
    async with stdio_client(single_project) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = await session.call_tool("md_select", {
                "sql": "SELECT content FROM md WHERE type='table'",
            })

    text = "".join(c.text for c in result.content)
    assert "Name" in text, f"Table header not found:\n{text}"
    assert "alpha" in text.lower() or "Alpha" in text, f"Table data not found:\n{text}"


@pytest.mark.asyncio
async def test_md_select_inline_elements(single_project: StdioServerParameters):
    """md_select returns inline elements (strong, inline_code)."""
    async with stdio_client(single_project) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = await session.call_tool("md_select", {
                "sql": "SELECT type, content, parent_pos FROM md "
                       "WHERE type IN ('strong', 'inline_code') ORDER BY pos_in_file",
            })

    text = "".join(c.text for c in result.content)
    assert "strong" in text, f"Strong element not found:\n{text}"
    assert "parent" in text, f"parent_pos not shown:\n{text}"


@pytest.mark.asyncio
async def test_md_select_no_results(single_project: StdioServerParameters):
    """md_select returns '(no results)' for empty queries."""
    async with stdio_client(single_project) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = await session.call_tool("md_select", {
                "sql": "SELECT * FROM md WHERE type='blockquote'",
            })

    text = "".join(c.text for c in result.content)
    assert "(no results)" in text


@pytest.mark.asyncio
async def test_md_select_aggregate(single_project: StdioServerParameters):
    """md_select supports aggregate queries (COUNT, GROUP BY)."""
    async with stdio_client(single_project) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = await session.call_tool("md_select", {
                "sql": "SELECT type, COUNT(*) AS cnt FROM md GROUP BY type ORDER BY cnt DESC",
            })

    text = "".join(c.text for c in result.content)
    # Extra aggregate columns appear as JSON blocks
    assert "{" in text and "cnt" in text, f"Aggregate not found:\n{text}"


# ═══════════════════════════════════════════════════════════════════════════════
# Test: md_select — multi-project (two independent doc roots)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_multi_project_select_by_root(multi_project: StdioServerParameters):
    """md_select can query each root independently via WHERE root='...'."""
    async with stdio_client(multi_project) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Query project_a
            result_a = await session.call_tool("md_select", {
                "sql": "SELECT file, type, content FROM md "
                       "WHERE root='project_a' AND type='heading'",
            })
            text_a = "".join(c.text for c in result_a.content)
            assert "Project A" in text_a or "project_a" in text_a, \
                f"project_a heading not found:\n{text_a}"

            # Query project_b
            result_b = await session.call_tool("md_select", {
                "sql": "SELECT file, type, content FROM md "
                       "WHERE root='project_b' AND type='heading'",
            })
            text_b = "".join(c.text for c in result_b.content)
            assert "Project B" in text_b or "project_b" in text_b, \
                f"project_b heading not found:\n{text_b}"

    # Verify they are different
    assert text_a != text_b, "Two projects should have different content"


@pytest.mark.asyncio
async def test_multi_project_select_each(multi_project: StdioServerParameters):
    """md_select can access each project root independently."""
    async with stdio_client(multi_project) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Query project_a directly with root filter
            result_a = await session.call_tool("md_select", {
                "sql": "SELECT file, type, content FROM md "
                       "WHERE root='project_a' AND type='heading' "
                       "ORDER BY pos_in_file LIMIT 1",
            })
            text_a = "".join(c.text for c in result_a.content)
            assert "project_a" in text_a, \
                f"project_a not found:\n{text_a}"

            # Query project_b directly with root filter
            result_b = await session.call_tool("md_select", {
                "sql": "SELECT file, type, content FROM md "
                       "WHERE root='project_b' AND type='heading' "
                       "ORDER BY pos_in_file LIMIT 1",
            })
            text_b = "".join(c.text for c in result_b.content)
            assert "project_b" in text_b, \
                f"project_b not found:\n{text_b}"


@pytest.mark.asyncio
async def test_multi_project_count_per_root(multi_project: StdioServerParameters):
    """Each project root has its own data with correct row counts."""
    async with stdio_client(multi_project) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Count rows for project_a
            result_a = await session.call_tool("md_select", {
                "sql": "SELECT COUNT(*) AS cnt FROM md "
                       "WHERE root='project_a'",
            })
            text_a = "".join(c.text for c in result_a.content)
            assert "cnt" in text_a or "{" in text_a, \
                f"project_a count not found:\n{text_a}"

            # Count rows for project_b
            result_b = await session.call_tool("md_select", {
                "sql": "SELECT COUNT(*) AS cnt FROM md "
                       "WHERE root='project_b'",
            })
            text_b = "".join(c.text for c in result_b.content)
            assert "cnt" in text_b or "{" in text_b, \
                f"project_b count not found:\n{text_b}"


# ═══════════════════════════════════════════════════════════════════════════════
# Test: md_change — INSERT / UPDATE / DELETE through MCP protocol
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_md_change_update(single_project: StdioServerParameters):
    """md_change UPDATE modifies the file and returns a diff."""
    async with stdio_client(single_project) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = await session.call_tool("md_change", {
                "sql": "UPDATE md SET content = '# Updated Title' "
                       "WHERE root='alpha' AND file='docs/alpha_readme.md' "
                       "AND type='heading' AND pos_in_file=1",
            })

    text = "".join(c.text for c in result.content)
    data = json.loads(text)
    assert "changes" in data, f"No changes in response:\n{data}"
    assert data["changes"][0]["operation"] == "update", f"Wrong op:\n{data}"
    assert "Updated Title" in str(data), f"Updated title missing:\n{data}"


@pytest.mark.asyncio
async def test_md_change_insert(single_project: StdioServerParameters):
    """md_change INSERT adds a new element."""
    async with stdio_client(single_project) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = await session.call_tool("md_change", {
                "sql": "INSERT INTO md (root, file, type, content) "
                       "VALUES ('alpha', 'docs/alpha_readme.md', "
                       "'paragraph', 'Inserted paragraph via MCP.')",
            })

    text = "".join(c.text for c in result.content)
    data = json.loads(text)
    assert "changes" in data, f"No changes in response:\n{data}"
    assert data["changes"][0]["operation"] == "insert", f"Wrong op:\n{data}"
    assert "files_changed" in data, f"No files_changed:\n{data}"
    assert "Inserted paragraph" in str(data), f"Content missing:\n{data}"


@pytest.mark.asyncio
async def test_md_change_delete(single_project: StdioServerParameters):
    """md_change DELETE removes an element and patches the file."""
    async with stdio_client(single_project) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = await session.call_tool("md_change", {
                "sql": "DELETE FROM md "
                       "WHERE root='alpha' AND file='docs/alpha_readme.md' "
                       "AND type='heading' AND content LIKE '%Quick Start%'",
            })

    text = "".join(c.text for c in result.content)
    data = json.loads(text)
    assert "changes" in data, f"No changes in response:\n{data}"
    assert data["changes"][0]["operation"] == "delete", f"Wrong op:\n{data}"
    assert "files_changed" in data, f"No files_changed:\n{data}"


# ═══════════════════════════════════════════════════════════════════════════════
# Test: error handling through MCP protocol
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_error_invalid_sql(single_project: StdioServerParameters):
    """md_select returns error for invalid SQL (DDL not allowed)."""
    async with stdio_client(single_project) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = await session.call_tool("md_select", {
                "sql": "DROP TABLE md",
            })

    text = "".join(c.text for c in result.content)
    assert "Error:" in text, f"Expected error, got:\n{text}"


@pytest.mark.asyncio
async def test_error_unknown_tool(single_project: StdioServerParameters):
    """Calling an unknown tool returns an error."""
    async with stdio_client(single_project) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = await session.call_tool("unknown_tool", {
                "sql": "SELECT 1",
            })

    text = "".join(c.text for c in result.content)
    assert "Error:" in text or "Unknown tool" in text, \
        f"Expected error, got:\n{text}"


@pytest.mark.asyncio
async def test_error_empty_sql(single_project: StdioServerParameters):
    """md_select with empty sql returns an error."""
    async with stdio_client(single_project) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = await session.call_tool("md_select", {
                "sql": "",
            })

    text = "".join(c.text for c in result.content)
    assert "Error:" in text, f"Expected error, got:\n{text}"


@pytest.mark.asyncio
async def test_error_no_where_in_update(single_project: StdioServerParameters):
    """md_change UPDATE without WHERE is rejected."""
    async with stdio_client(single_project) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = await session.call_tool("md_change", {
                "sql": "UPDATE md SET content = 'nope'",
            })

    text = "".join(c.text for c in result.content)
    assert "Error:" in text, f"Expected error, got:\n{text}"


@pytest.mark.asyncio
async def test_error_no_where_in_delete(single_project: StdioServerParameters):
    """md_change DELETE without WHERE is rejected."""
    async with stdio_client(single_project) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = await session.call_tool("md_change", {
                "sql": "DELETE FROM md",
            })

    text = "".join(c.text for c in result.content)
    assert "Error:" in text, f"Expected error, got:\n{text}"


# ═══════════════════════════════════════════════════════════════════════════════
# Test: no DOC_ROOTS — error returned to client
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_no_roots_returns_error(no_roots_server: StdioServerParameters):
    """Server without DOC_ROOTS returns error when no IDE roots."""
    async with stdio_client(no_roots_server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            tool_names = {t.name for t in tools.tools}

            result = await session.call_tool("md_select", {
                "sql": "SELECT content FROM md WHERE type='heading'",
            })

    text = "".join(c.text for c in result.content)
    assert "md_select" in tool_names
    assert "md_change" in tool_names
    assert "no document roots" in text.lower(), f"Expected error, got:\n{text}"


# ═══════════════════════════════════════════════════════════════════════════════
# Test: empty docs directory
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_empty_root_select(env_with_empty_docs: StdioServerParameters):
    """md_select on a root with no .md files returns '(no results)'."""
    async with stdio_client(env_with_empty_docs) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = await session.call_tool("md_select", {
                "sql": "SELECT * FROM md",
            })

    text = "".join(c.text for c in result.content)
    assert "(no results)" in text or "Error" not in text, \
        f"Unexpected response:\n{text}"
