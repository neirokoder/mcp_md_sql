"""Configuration for md-sql-server.

Reads DOC_ROOTS from environment variable.
Format: alias1=/absolute/path1;alias2=C:/path2;...
"""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RootConfig:
    alias: str
    path: Path


# Module-level cache for roots (can be set programmatically for tests)
_roots: list[RootConfig] | None = None


def parse_doc_roots() -> list[RootConfig]:
    """Parse DOC_ROOTS env var into list of RootConfig."""
    raw = os.environ.get("DOC_ROOTS", "")
    if not raw:
        return []

    roots = []
    for pair in raw.split(";"):
        if not pair.strip():
            continue
        if "=" not in pair:
            continue
        eq_idx = pair.index("=")
        alias = pair[:eq_idx].strip()
        path_str = pair[eq_idx + 1:].strip()
        if not alias or not path_str:
            continue
        roots.append(RootConfig(alias=alias, path=Path(path_str).resolve()))

    return roots


def set_roots(roots: list[RootConfig]) -> None:
    """Set the document roots programmatically (for testing)."""
    global _roots
    _roots = roots


def get_doc_roots() -> list[RootConfig]:
    """Get configured document roots."""
    global _roots
    if _roots is not None:
        return _roots
    roots = parse_doc_roots()
    if not roots:
        cwd = Path.cwd()
        roots = [RootConfig(alias="default", path=cwd)]
    return roots
