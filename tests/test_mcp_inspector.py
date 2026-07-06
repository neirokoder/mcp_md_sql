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


def _make_server_params(paths: list[str]) -> StdioServerParameters:
    """Build stdio server parameters with DOC_ROOTS env pointing to temp dirs."""
    env = dict(os.environ)
    env["DOC_ROOTS"] = json.dumps(paths)
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
def single_project(tmp_path: Path) -> tuple[StdioServerParameters, str]:
    """Server with a single project root.
    Returns (server_params, root_path).
    """
    root = _create_project(tmp_path / "alpha", "alpha")
    return _make_server_params([str(root)]), str(root)


@pytest.fixture
def multi_project(tmp_path: Path) -> tuple[StdioServerParameters, str, str]:
    """Server with two independent project roots.
    Returns (server_params, project_a_root, project_b_root).
    """
    root1 = _create_project(tmp_path / "project_a", "project_a")
    root2 = _create_project(tmp_path / "project_b", "project_b")
    params = _make_server_params([str(root1), str(root2)])
    return params, str(root1), str(root2)


@pytest.fixture
def write_project(tmp_path: Path) -> tuple[StdioServerParameters, str]:
    """Server with a single project for write tests.
    Returns (server_params, root_path).
    """
    root = _create_project(tmp_path / "alpha", "alpha")
    return _make_server_params([str(root)]), str(root)


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
# Read tests — one session for all read-only checks
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_read_operations(single_project: tuple[StdioServerParameters, str],
                              multi_project: tuple[StdioServerParameters, str, str]):
    """All read-only checks in 2 subprocesses (single + multi)."""
    # ── Single project ──
    params, _ = single_project
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = {t.name for t in (await session.list_tools()).tools}
            assert "md_select" in tools
            assert "md_change" in tools

            r = await session.call_tool("md_select", {
                "sql": "SELECT root, file, type, content FROM md "
                       "WHERE type='heading' ORDER BY pos_in_file",
            })
            text = "".join(c.text for c in r.content)
            assert "Alpha Project" in text
            assert "Quick Start" in text

            r = await session.call_tool("md_select", {
                "sql": "SELECT content FROM md WHERE type='code_block'",
            })
            assert "Hello from alpha" in "".join(c.text for c in r.content)

            r = await session.call_tool("md_select", {
                "sql": "SELECT content FROM md WHERE type='table'",
            })
            text = "".join(c.text for c in r.content)
            assert "Name" in text
            assert "alpha" in text.lower()

            r = await session.call_tool("md_select", {
                "sql": "SELECT type, content, parent_pos FROM md "
                       "WHERE type IN ('strong', 'inline_code') ORDER BY pos_in_file",
            })
            text = "".join(c.text for c in r.content)
            assert "strong" in text
            assert "parent" in text

            r = await session.call_tool("md_select", {
                "sql": "SELECT * FROM md WHERE type='blockquote'",
            })
            assert "(no results)" in "".join(c.text for c in r.content)

            r = await session.call_tool("md_select", {
                "sql": "SELECT type, COUNT(*) AS cnt FROM md "
                       "GROUP BY type ORDER BY cnt DESC",
            })
            text = "".join(c.text for c in r.content)
            assert "{" in text and "cnt" in text

            # errors
            r = await session.call_tool("md_select", {"sql": "DROP TABLE md"})
            assert "Error:" in "".join(c.text for c in r.content)
            r = await session.call_tool("unknown_tool", {"sql": "SELECT 1"})
            assert "Error:" in "".join(c.text for c in r.content) or "Unknown tool" in "".join(c.text for c in r.content)
            r = await session.call_tool("md_select", {"sql": ""})
            assert "Error:" in "".join(c.text for c in r.content)
            r = await session.call_tool("md_change", {"sql": "UPDATE md SET content = 'nope'"})
            assert "Error:" in "".join(c.text for c in r.content)
            r = await session.call_tool("md_change", {"sql": "DELETE FROM md"})
            assert "Error:" in "".join(c.text for c in r.content)

    # ── Multi project ──
    params, root_a, root_b = multi_project
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            r_a = await session.call_tool("md_select", {
                "sql": f"SELECT file, type, content FROM md "
                       f"WHERE root='{root_a}' AND type='heading'",
            })
            text_a = "".join(c.text for c in r_a.content)
            assert "project_a" in text_a

            r_b = await session.call_tool("md_select", {
                "sql": f"SELECT file, type, content FROM md "
                       f"WHERE root='{root_b}' AND type='heading'",
            })
            text_b = "".join(c.text for c in r_b.content)
            assert "project_b" in text_b
            assert text_a != text_b

            r_a = await session.call_tool("md_select", {
                "sql": f"SELECT file, type, content FROM md "
                       f"WHERE root='{root_a}' AND type='heading' "
                       f"ORDER BY pos_in_file LIMIT 1",
            })
            assert "project_a" in "".join(c.text for c in r_a.content)

            r_b = await session.call_tool("md_select", {
                "sql": f"SELECT file, type, content FROM md "
                       f"WHERE root='{root_b}' AND type='heading' "
                       f"ORDER BY pos_in_file LIMIT 1",
            })
            assert "project_b" in "".join(c.text for c in r_b.content)

            r_a = await session.call_tool("md_select", {
                "sql": f"SELECT COUNT(*) AS cnt FROM md WHERE root='{root_a}'",
            })
            assert "cnt" in "".join(c.text for c in r_a.content) or "{" in "".join(c.text for c in r_a.content)

            r_b = await session.call_tool("md_select", {
                "sql": f"SELECT COUNT(*) AS cnt FROM md WHERE root='{root_b}'",
            })
            assert "cnt" in "".join(c.text for c in r_b.content) or "{" in "".join(c.text for c in r_b.content)


# ═══════════════════════════════════════════════════════════════════════════════
# Edge-case tests — separate subprocesses each
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_no_roots_error(no_roots_server: StdioServerParameters):
    """Server without DOC_ROOTS returns error."""
    async with stdio_client(no_roots_server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            r = await session.call_tool("md_select", {
                "sql": "SELECT content FROM md WHERE type='heading'",
            })
            assert "md_initialize" in "".join(c.text for c in r.content).lower()


@pytest.mark.asyncio
async def test_empty_root_select(env_with_empty_docs: StdioServerParameters):
    """Root with no .md files returns '(no results)'."""
    async with stdio_client(env_with_empty_docs) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            r = await session.call_tool("md_select", {"sql": "SELECT * FROM md"})
            text = "".join(c.text for c in r.content)
            assert "(no results)" in text or "Error" not in text


# ═══════════════════════════════════════════════════════════════════════════════
# Write tests — one session for all mutations
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_write_operations(write_project: tuple[StdioServerParameters, str]):
    """All write operations in one session."""
    params, root_path = write_project
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # UPDATE
            r = await session.call_tool("md_change", {
                "sql": f"UPDATE md SET content = '# Updated Title' "
                       f"WHERE root='{root_path}' AND file='docs/alpha_readme.md' "
                       f"AND type='heading' AND pos_in_file=1",
            })
            data = json.loads("".join(c.text for c in r.content))
            assert data["changes"][0]["operation"] == "update"
            assert "Updated Title" in str(data)

            # INSERT
            r = await session.call_tool("md_change", {
                "sql": f"INSERT INTO md (root, file, type, content) "
                       f"VALUES ('{root_path}', 'docs/alpha_readme.md', "
                       f"'paragraph', 'Inserted paragraph via MCP.')",
            })
            data = json.loads("".join(c.text for c in r.content))
            assert data["changes"][0]["operation"] == "insert"
            assert "files_changed" in data
            assert "Inserted paragraph" in str(data)

            # DELETE
            r = await session.call_tool("md_change", {
                "sql": f"DELETE FROM md "
                       f"WHERE root='{root_path}' AND file='docs/alpha_readme.md' "
                       f"AND type='heading' AND content LIKE '%Quick Start%'",
            })
            data = json.loads("".join(c.text for c in r.content))
            assert data["changes"][0]["operation"] == "delete"
            assert "files_changed" in data
