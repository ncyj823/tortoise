import pytest
from fastapi.testclient import TestClient

from agent_team import core
from agent_team.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "test.db"))
    core.init_db()
    with core.db() as c:
        c.execute("INSERT INTO tenants(id, status) VALUES ('with.com','ready'), ('empty.com','ready')")
        c.execute("INSERT INTO outputs(tenant_id, agent, content, created_at) "
                  "VALUES ('with.com','strategy','plan','2026-10-21T00:00:00')")
    return TestClient(app)  # not used as a context manager: skips lifespan/scheduler


def test_tenant_with_outputs_returns_data(client):
    r = client.get("/tenants/with.com/outputs", params={"agent": "Strategy"})
    assert r.status_code == 200
    assert [o["content"] for o in r.json()] == ["plan"]


def test_tenant_without_outputs_returns_empty_list(client):
    r = client.get("/tenants/empty.com/outputs", params={"agent": "strategy"})
    assert r.status_code == 200
    assert r.json() == []


def test_unknown_tenant_returns_404(client):
    r = client.get("/tenants/nope.invalid/outputs", params={"agent": "strategy"})
    assert r.status_code == 404
    assert r.json()["detail"] == "Tenant not found"


def test_invalid_agent_returns_400(client):
    r = client.get("/tenants/with.com/outputs", params={"agent": "strategy_agent"})
    assert r.status_code == 400
