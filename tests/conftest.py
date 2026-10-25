import pytest

from agent_team import main


@pytest.fixture(autouse=True)
def clear_rate_limit():
    """Each test starts with an empty in-memory generate limiter."""
    main._rate_hits.clear()
    yield
    main._rate_hits.clear()
