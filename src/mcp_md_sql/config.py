"""Configuration for md-sql.

Reads DOC_ROOTS from environment variable.
Format: JSON array of paths ["/abs/path1", "/abs/path2"]
Legacy:  alias1=/path1;alias2=/path2
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RootConfig:
    alias: str
    path: Path


# Module-level cache for roots (can be set programmatically for tests)
_roots: list[RootConfig] | None = None


def _generate_alias(path: Path, used: set[str]) -> str:
    """Generate unique alias from path basename."""
    base = path.name or "root"
    alias = base
    i = 1
    while alias in used:
        alias = f"{base}_{i}"
        i += 1
    return alias


def parse_doc_roots() -> list[RootConfig]:
    """Parse DOC_ROOTS env var into list of RootConfig.

    Accepts:
      JSON array of paths:  ["/abs/path1", "/abs/path2"]
      Legacy:               alias1=/path1;alias2=/path2
    """
    raw = os.environ.get("DOC_ROOTS", "")
    if not raw:
        return []

    roots: list[RootConfig] = []
    used_aliases: set[str] = set()

    # Try JSON array of paths
    if raw.strip().startswith('['):
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, str):
                        path_str = item.strip()
                        if path_str:
                            path = Path(path_str).resolve()
                            alias = _generate_alias(path, used_aliases)
                            used_aliases.add(alias)
                            roots.append(RootConfig(alias=alias, path=path))
                if roots:
                    return roots
        except (json.JSONDecodeError, Exception):
            pass

    # Fallback: legacy semicolon-separated format
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
        path = Path(path_str).resolve()
        if alias in used_aliases:
            alias = _generate_alias(path, used_aliases)
        used_aliases.add(alias)
        roots.append(RootConfig(alias=alias, path=path))

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
    return parse_doc_roots()
