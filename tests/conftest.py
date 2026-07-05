"""Global test fixtures."""
import pytest
from mcp_md_sql.tools import clear_root_configs


@pytest.fixture(autouse=True)
def _reset_root_configs():
    """Clear per-root config cache before/after each test.

    Does NOT reset config._roots — module-scoped fixtures (e.g. `db`)
    set those once and expect them to persist across tests.
    """
    clear_root_configs()
    yield
    clear_root_configs()
