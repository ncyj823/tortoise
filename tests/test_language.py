import json

import pytest
from fastapi.testclient import TestClient

from agent_team import checks, core
from agent_team.main import app

TID = "acttrident.ai"
HINGLISH_PB = {"mission": "ActTrident ke LinkedIn aur Instagram pe brand awareness ko boost karna, taaki leads aaye."}
BRAIN = {**{k: [] for k in core.BRAIN_SECTIONS}, "company": {"name": "ActTrident Ltd"}, "sources": []}
ENGLISH = "A short English draft about securing every consequential action before it causes harm."
HINGLISH = "Yeh draft ActTrident ke liye hai, aur hum leads ko boost karna chahte hain."


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(core, "web_search", lambda q, n=6: [])
    core.init_db()
    with core.db() as c:
        c.execute("INSERT INTO tenants(id, status, timezone, language, brain, playbooks, welcome) "
                  "VALUES (?,?,?,?,?,?,?)",
                  (TID, "ready", "Asia/Kolkata", "English", json.dumps(BRAIN),
                   json.dumps({a: HINGLISH_PB for a in core.PLAYBOOK_AGENTS}), "Namaste! Aapka swagat hai."))
    return TestClient(app)  # not used as a context manager: skips lifespan/scheduler


# ---------------------------------------------------------------- detection
@pytest.mark.parametrize("text, lang, flagged", [
    (ENGLISH, "English", False),
    (HINGLISH, "English", True),
    ("Human Action Security: Enterprise के लिए नया ढांचा", "English", True),
    ("Human Action Security: Enterprise के लिए नया ढांचा", "Hinglish", True),
    (HINGLISH, "Hinglish", False),
    ("ActTrident आपके संगठन की हर कार्रवाई को सुरक्षित करता है।", "Hindi", False),
    (ENGLISH, "Hindi", True),
    ("[CTA]", "Hindi", False),
])
def test_language_issue(text, lang, flagged):
    assert bool(core.language_issue(text, lang)) is flagged


def test_every_agent_prompt_carries_the_language_rule():
    t = {"timezone": "Asia/Kolkata", "language": "English", "brain": BRAIN, "playbooks": {"blog": HINGLISH_PB}}
    system = core._system_for(t, "blog")
    assert core.language_rule("English") in system and "never copy its language" in system


# ---------------------------------------------------------------- agent outputs
def test_wrong_language_output_is_rewritten_once(client, monkeypatch):
    replies, prompts = [HINGLISH, ENGLISH], []
    monkeypatch.setattr(core, "ask", lambda system, user, **kw: prompts.append(user) or replies.pop(0))
    r = client.post(f"/tenants/{TID}/agents/social/run", json={})
    assert r.status_code == 200 and r.json()["output"] == ENGLISH
    assert "Your previous draft was not in English: it contains Hinglish words" in prompts[1]
    stored = client.get(f"/tenants/{TID}/outputs", params={"agent": "social"}).json()[0]["content"]
    assert stored == ENGLISH


def test_output_still_in_the_wrong_language_is_flagged(client, monkeypatch):
    monkeypatch.setattr(core, "ask", lambda system, user, **kw: HINGLISH)
    out = client.post(f"/tenants/{TID}/agents/social/run", json={}).json()["output"]
    assert out.startswith("**Language check: this draft is meant to be in English, but it contains Hinglish words")


# ---------------------------------------------------------------- changing language
def playbook_ask(prompts):
    def ask(system, user, **kw):
        prompts.append(system + "\n" + user)
        if "welcome_message" in user:
            return json.dumps({"welcome_message": "Welcome to your new team."})
        return json.dumps({"mission": "Grow brand awareness on LinkedIn and Instagram."})
    return ask


def test_change_language_rebuilds_playbooks_and_welcome_in_it(client, monkeypatch):
    with core.db() as c:
        c.execute("UPDATE tenants SET language='Hinglish'")
    prompts = []
    monkeypatch.setattr(core, "ask", playbook_ask(prompts))
    r = client.post(f"/tenants/{TID}/language", json={"language": "english"})
    assert r.status_code == 200 and r.json() == {"status": "changing", "language": "English"}
    t = core.get_tenant(TID)
    assert t["language"] == "English" and t["welcome"] == "Welcome to your new team." and t["error"] is None
    assert set(t["playbooks"]) == set(core.PLAYBOOK_AGENTS)
    assert all(core.language_issue(json.dumps(pb), "English") is None for pb in t["playbooks"].values())
    assert len(prompts) == 6 and all(core.language_rule("English") in p for p in prompts)
    assert client.get(f"/tenants/{TID}").json()["language"] == "English"


def test_failed_change_keeps_the_old_language_and_playbooks(client, monkeypatch):
    def broken(system, user, **kw):
        raise core.LLMError("provider down")
    monkeypatch.setattr(core, "ask", broken)
    client.post(f"/tenants/{TID}/language", json={"language": "Hindi"})
    t = core.get_tenant(TID)
    assert t["language"] == "English" and t["playbooks"]["blog"] == HINGLISH_PB
    assert "Language change to Hindi failed" in t["error"]


def test_change_language_rejects_unknown_language_and_tenant(client):
    assert client.post(f"/tenants/{TID}/language", json={"language": "French"}).status_code == 400
    assert client.post("/tenants/nope.invalid/language", json={"language": "English"}).status_code == 404


# ---------------------------------------------------------------- content agent copy
def test_content_lines_in_another_language_are_flagged():
    part = {"short": {"duration_seconds": 30, "aspect_ratios": ["9:16", "4:5"], "caption": "",
                      "scenes": [{"line": "Your team just pasted a key into a chatbot.", "visual": ""},
                                 {"line": "Ek galat paste aur aapka secret leak ho jata hai.", "visual": ""},
                                 {"line": "हर टीम के लिए", "visual": ""}]}}
    issues = checks.language(checks.lines(part), "English")
    assert [i["where"] for i in issues] == ["short scene 2 voiceover", "short scene 3 voiceover"]
