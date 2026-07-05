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
    path: Path


# Module-level cache for roots (can be set programmatically for tests)
_roots: list[RootConfig] | None = None


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
                            roots.append(RootConfig(path=path))
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
        path_str = pair[eq_idx + 1:].strip()
        if not path_str:
            continue
        path = Path(path_str).resolve()
        roots.append(RootConfig(path=path))

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
