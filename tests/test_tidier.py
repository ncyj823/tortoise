import json

import pytest
from fastapi.testclient import TestClient

from agent_team import core, offerings, tidier
from agent_team.main import app
from tests.fixtures import BRIGHTLEAF_SUB, COMPANY, TID

EM = "—"


def package(core_message, call_to_action="[CTA]"):
    return {"campaign_summary": {"goal": "Leads", "audience": "coffee lovers", "core_message": core_message,
                                 "call_to_action": call_to_action},
            "videos": {}, "posts": [], "article": None, "sources": [], "open_questions": []}


def store(pkg, status="draft", agent="content"):
    with core.db() as c:
        return c.execute("INSERT INTO outputs(tenant_id,agent,content,created_at,status) VALUES(?,?,?,?,?)",
                         (TID, agent, json.dumps({"request": "test", "package": pkg}),
                          core.dt.datetime.utcnow().isoformat(), status)).lastrowid


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "test.db"))
    core.init_db()
    with core.db() as c:
        c.execute("INSERT INTO tenants(id,status,timezone,language,brain) VALUES(?,?,?,?,?)",
                  (TID, "ready", "Asia/Kolkata", "English", json.dumps({"company": {"name": COMPANY}})))
    offerings.save(TID, BRIGHTLEAF_SUB, None)
    return TestClient(app)


def test_clean_draft_is_approved(client):
    store(package("Coffee arrives quietly. Catch it fresh."))
    report = tidier.run(TID)
    assert report.total_items_processed == 1
    assert report.approved == 1
    assert report.items_sanitized == 0
    assert report.audit_logs[0]["status"] == tidier.STATUS_APPROVED


def test_writing_rules_and_never_claim_terms_are_fixed_in_code(client):
    store(package(f"Coffee arrives quietly {EM} catch it fresh. Starbucks is covered too."))
    report = tidier.run(TID)
    log = report.audit_logs[0]
    assert log["status"] == tidier.STATUS_SANITIZED
    changed = {c["after"] for c in log["changes"]}
    assert "Coffee arrives quietly, catch it fresh." in changed
    assert not any("Starbucks" in c["after"] for c in log["changes"])


def test_product_claim_outside_the_claim_bank_is_flagged_not_rewritten(client):
    store(package("Brightleaf Subscription roasts every bean twice."))
    report = tidier.run(TID)
    log = report.audit_logs[0]
    assert log["status"] == tidier.STATUS_FLAGGED
    assert any("claim bank" in v["reason"] for v in log["violations"])
    assert log["changes"] == []


def test_only_drafts_are_audited_and_latest_report_is_stored(client):
    store(package("Brightleaf Subscription roasts every bean twice."), status="approved")
    store(package("Brightleaf Subscription roasts every bean twice."))
    report = tidier.run(TID)
    assert report.total_items_processed == 1
    latest = tidier.latest(TID)
    assert latest["tenant_id"] == TID
    assert len(latest["audit_logs"]) == 1


def test_run_endpoint_and_latest_endpoint(client):
    store(package("Brightleaf Subscription roasts every bean twice."))
    r = client.post(f"/tenants/{TID}/tidier/run", json={})
    assert r.status_code == 200, r.text
    assert r.json()["flagged_for_human"] == 1
    r2 = client.get(f"/tenants/{TID}/tidier/latest")
    assert r2.status_code == 200
    assert r2.json()["audit_logs"][0]["status"] == tidier.STATUS_FLAGGED


def test_unknown_tenant_is_rejected(client):
    assert client.post("/tenants/nope/tidier/run", json={}).status_code == 404
    assert client.get("/tenants/nope/tidier/latest").status_code == 404


def row(output_id):
    with core.db() as c:
        return dict(c.execute("SELECT content, original_text FROM outputs WHERE id=?", (output_id,)).fetchone())


def test_normalisation_runs_first_and_is_recorded_as_a_change(client):
    output_id = store(package(f"Coffee arrives quietly {EM} catch it fresh."))
    before = row(output_id)["content"]
    report = tidier.run(TID)
    log = report.audit_logs[0]
    assert log["status"] == tidier.STATUS_SANITIZED
    change = next(c for c in log["changes"] if c["path"] == "part A.campaign_summary.core_message")
    assert change["before"] == f"Coffee arrives quietly {EM} catch it fresh."
    assert change["after"] == "Coffee arrives quietly, catch it fresh."
    assert row(output_id)["content"] == before      # apply defaults to false


def test_needs_review_drafts_are_in_scope(client):
    store(package("Coffee arrives quietly. Catch it fresh."), status="needs_review")
    report = tidier.run(TID)
    assert report.total_items_processed == 1
    assert report.approved == 1


def test_apply_true_writes_back_only_sanitized_drafts(client):
    sanitized_id = store(package(f"Coffee arrives quietly {EM} catch it fresh."))
    flagged_id = store(package("Brightleaf Subscription roasts every bean twice."))
    original = row(sanitized_id)["content"]

    r = client.post(f"/tenants/{TID}/tidier/run", json={})
    assert r.status_code == 200, r.text
    assert r.json()["applied"] == []
    assert row(sanitized_id)["content"] == original

    r = client.post(f"/tenants/{TID}/tidier/run?apply=true", json={})
    assert r.status_code == 200, r.text
    assert r.json()["applied"] == [str(sanitized_id)]
    written = row(sanitized_id)
    assert EM not in written["content"]
    assert "Coffee arrives quietly, catch it fresh." in written["content"]
    assert written["original_text"] == original
    assert row(flagged_id)["content"] == json.dumps({"request": "test",
                                                     "package": package("Brightleaf Subscription roasts every bean twice.")})
    assert row(flagged_id)["original_text"] is None
