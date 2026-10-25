import json

import pytest
from fastapi.testclient import TestClient

from agent_team import core
from agent_team.main import app
from scripts import demo_seed
from tests.fixtures import TID


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "test.db"))
    core.init_db()
    return TestClient(app)


def test_health_is_a_plain_ok():
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_demo_key_protects_writes_but_not_reads(client, monkeypatch):
    monkeypatch.setenv("DEMO_KEY", "open-sesame")
    assert client.get("/ui").status_code == 200
    assert client.get("/health").status_code == 200
    assert client.get("/tenants").status_code == 200

    for path, method, body in (
        (f"/tenants/{TID}/command", "post", {"text": "A coffee ad"}),
        (f"/tenants/{TID}/outputs/1", "patch", {"status": "approved"}),
        (f"/tenants/{TID}/tidier/run", "post", {}),
    ):
        assert getattr(client, method)(path, json=body).status_code == 401
        assert getattr(client, method)(path, json=body, headers={"X-Demo-Key": "wrong"}).status_code == 401


def test_demo_key_accepts_the_right_header(client, monkeypatch):
    monkeypatch.setenv("DEMO_KEY", "open-sesame")
    headers = {"X-Demo-Key": "open-sesame"}
    assert client.post(f"/tenants/{TID}/command", json={"text": "A coffee ad"},
                       headers=headers).status_code == 404
    assert client.patch(f"/tenants/{TID}/outputs/1", json={"status": "approved"},
                        headers=headers).status_code == 404
    assert client.post(f"/tenants/{TID}/tidier/run", json={}, headers=headers).status_code == 404


def test_generate_is_rate_limited_per_address(client, monkeypatch):
    monkeypatch.setenv("GENERATE_PER_HOUR", "1")
    first = client.post(f"/tenants/{TID}/command", json={"text": "A coffee ad"})
    second = client.post(f"/tenants/{TID}/command", json={"text": "Another coffee ad"})
    assert first.status_code == 404
    assert second.status_code == 429


def test_demo_seed_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "seed.db"))
    seed_log = []
    monkeypatch.setattr(demo_seed, "print", lambda *a, **k: seed_log.append(a), raising=False)
    demo_seed.main()
    demo_seed.main()
    assert len(core.all_tenants()) == 1
    with core.db() as c:
        assert c.execute("SELECT COUNT(*) FROM outputs").fetchone()[0] == 3
        assert c.execute("SELECT COUNT(*) FROM offering_rules").fetchone()[0] == 1
    assert any("already exists" in " ".join(map(str, row)) for row in seed_log)


def test_demo_seed_campaigns_are_well_formed(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "seed.db"))
    demo_seed.main()
    with core.db() as c:
        rows = c.execute("SELECT content FROM outputs WHERE tenant_id=?", (TID,)).fetchall()
    assert len(rows) == 3
    for row in rows:
        content = json.loads(row["content"])
        pkg = content["package"]
        assert pkg["campaign_summary"]["core_message"]
        assert set(pkg["videos"]) == {"hook", "short", "explainer"}
        assert pkg["posts"]
        raw = json.dumps(pkg)
        assert "\u2014" not in raw
        assert "Starbucks" not in raw and "organic certified" not in raw
