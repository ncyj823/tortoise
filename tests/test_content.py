import json
import re

import pytest
from fastapi.testclient import TestClient

from agent_team import checks, content, core, offerings
from agent_team.main import app

EM = "—"
TID = "acttrident.ai"
BRAIN = {"company": {"name": "ActTrident Ltd", "one_line_summary": "Action security."},
         "offerings": [{"name": "Protect", "description": "Blocks risky actions."}],
         "positioning": {"value_proposition": "Secures the action itself."},
         "brand_voice": {"tone_adjectives": ["plain-language"]},
         "sources": [{"url": "https://acttrident.ai/products", "used_for": "products"}]}
SECRET_EGRESS = offerings.OfferingRule(
    name="Secret Egress",
    facts=["Secret Egress is a client-side browser extension feature in ActTrident.",
           "It detects and blocks credential-like values when someone pastes them into AI chatbot interfaces."],
    not_yet_available=["Email (Gmail, Outlook) coverage"],
    copy_rules=["Do not describe detection internals or how the mechanisms work."],
    never_claim_terms=["Gmail", "SIEM"],
    claim_bank=["Secret Egress, from ActTrident, blocks credential-like values pasted into AI chatbots, "
                "before they leave the browser."])
BANK = SECRET_EGRESS.claim_bank[0]
PAGES = {"https://acttrident.ai/products": "ActTrident products. Stop risky actions before impact."}


def video(seconds, ratios):
    return {"duration_seconds": seconds, "aspect_ratios": ratios,
            "scenes": [{"line": f"Stop {EM} look at what just left your network.",
                        "on_screen_text": "Stop the leak", "visual": "Close-up of a laptop"}],
            "caption": f"Data leaves quietly {EM} catch it first.", "shot_list": ["laptop close-up"]}


SOURCES = [{"claim": "Protect blocks risky actions", "quote": "Blocks risky actions", "url": ""},
           {"claim": "Made-up stat", "quote": "forty percent fewer leaks", "url": "https://invented.example/x"},
           {"claim": "Stops risky actions", "quote": "Stop risky actions before impact",
            "url": "https://acttrident.ai/products"},
           {"claim": "Works everywhere", "quote": "Works in every browser on earth",
            "url": "https://acttrident.ai/products"}]


def label_lines(rule=lambda key, text: "PROBLEM"):
    """A fake checker: labels every numbered line in the prompt PRODUCT or PROBLEM with rule(part_key, text)."""
    def verify(key, user):
        return [{"id": int(m.group(1)), "c": rule(key, m.group(2))}
                for m in re.finditer(r"^(\d+)\. \[[^\]]*\] (.*)$", user, re.M)]
    return verify


def fake_ask(route="content", prompts=None, verify=None, posts_text=None, edit=None):
    """Stands in for the LLM. Replies by task, with em dashes the agent must strip.
    verify(part_key, prompt) -> labels for the checker (default: every line PROBLEM).
    edit(task, reply_dict) changes a generated part before it is returned."""
    verify = verify or label_lines()

    def ask(system, user, **kw):
        if prompts is not None:
            prompts.append(user)
        if "TASK: intent" in user:
            return json.dumps({"route": route, "platforms": ["Facebook"], "goal": "Leads",
                               "topic": "Secret Egress", "audience": "CISOs", "constraints": [],
                               "long_form": False})
        if "TASK: verify claims" in user:
            key = re.search(r"verify claims \(part (\w)\)", user).group(1)
            return json.dumps({"labels": verify(key, user)})
        if "TASK: summary and short videos" in user:
            task, d = "A", {"campaign_summary": {"goal": "Leads", "audience": "CISOs",
                                                 "core_message": f"See it {EM} stop it.",
                                                 "call_to_action": "[CTA]"},
                            "hook": video(5, ["9:16", "4:5"]), "short": video(30, ["9:16", "4:5"]),
                            "sources": SOURCES,
                            "open_questions": ["What exactly does Secret Egress detect?"]}
        elif "TASK: explainer video" in user:
            task, d = "B", {"explainer": video(120, ["16:9"]), "sources": [], "open_questions": []}
        elif "TASK: platform posts" in user:
            text = posts_text(user) if posts_text else f"Your data {EM} protected."
            task, d = "C", {"posts": [{"platform": "facebook", "text": text}], "sources": [], "open_questions": []}
        else:
            raise AssertionError("unexpected prompt: " + user[:80])
        if edit:
            edit(task, d)
        return json.dumps(d)
    return ask


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")      # the checker's provider is mocked with core.ask
    for k in ("CHECKER_PROVIDER", "CHECKER_MODEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(core, "ask", fake_ask())
    monkeypatch.setattr(core, "web_search", lambda q, n=6: [])
    monkeypatch.setattr(core, "fetch_page", lambda url, limit=5000: PAGES.get(url, ""))
    core.init_db()
    with core.db() as c:
        c.execute("INSERT INTO tenants(id, status, timezone, language, brain, playbooks) VALUES (?,?,?,?,?,?)",
                  (TID, "ready", "Asia/Kolkata", "English", json.dumps(BRAIN), "{}"))
    return TestClient(app)  # not used as a context manager: skips lifespan/scheduler


def command(client, text="Facebook ad campaign for Secret Egress. Give me the videos and structure."):
    return client.post(f"/tenants/{TID}/command", json={"text": text})


def add_secret_egress():
    offerings.save(TID, SECRET_EGRESS, None)


# ---------------------------------------------------------------- command endpoint
def test_valid_command_returns_package_with_three_videos(client):
    r = command(client)
    assert r.status_code == 200, r.text
    body = r.json()
    videos = body["package"]["videos"]
    assert set(videos) == {"hook", "short", "explainer"}
    assert [videos[k]["duration_seconds"] for k in ("hook", "short", "explainer")] == [5, 30, 120]
    assert [p["platform"] for p in body["package"]["posts"]] == ["facebook"]
    assert list(body["package"])[0] == "unverified_claims" and body["package"]["unverified_claims"] == []
    rows = client.get(f"/tenants/{TID}/outputs", params={"agent": "content"}).json()
    assert len(rows) == 1 and rows[0]["status"] == "draft" and rows[0]["id"] == body["output_id"]
    assert any("No Strategy output" in n for n in body["package"]["notes"])


def test_unsupported_route_returns_400(client, monkeypatch):
    monkeypatch.setattr(core, "ask", fake_ask(route="unsupported"))
    r = command(client, "Post our last blog to LinkedIn now")
    assert r.status_code == 400
    assert "only creates campaign content drafts" in r.json()["detail"]


def test_unknown_tenant_returns_404(client):
    r = client.post("/tenants/nope.invalid/command", json={"text": "LinkedIn post"})
    assert r.status_code == 404
    assert r.json()["detail"] == "Tenant not found"


def test_output_contains_no_em_dash(client):
    r = command(client)
    assert r.status_code == 200
    assert EM not in r.text and EM not in json.dumps(r.json(), ensure_ascii=False)
    stored = client.get(f"/tenants/{TID}/outputs", params={"agent": "content"}).json()[0]["content"]
    assert EM not in stored
    assert "See it, stop it." in stored


def test_topic_missing_from_context_is_flagged(client, monkeypatch):
    prompts = []
    monkeypatch.setattr(core, "ask", fake_ask(prompts=prompts))
    pkg = command(client).json()["package"]
    assert any("Secret Egress" in n and "placeholders" in n for n in pkg["notes"])
    assert all("UNKNOWN TOPIC" in u for u in prompts if "TASK: intent" not in u and "TASK: verify" not in u)


# ---------------------------------------------------------------- sources
def test_sources_are_kept_only_when_their_quote_is_found(client):
    pkg = command(client).json()["package"]
    assert pkg["sources"] == [
        {"claim": "Protect blocks risky actions", "quote": "Blocks risky actions",
         "source": content.RESEARCHED_SOURCE},
        {"claim": "Stops risky actions", "quote": "Stop risky actions before impact",
         "source": "https://acttrident.ai/products"}]
    # an unretrieved URL and a retrieved URL whose page lacks the quote are both rejected
    assert any("Made-up stat" in q for q in pkg["open_questions"])
    assert any("Works everywhere" in q for q in pkg["open_questions"])


def test_supplied_facts_are_sourced_to_the_company_brief(client, monkeypatch):
    add_secret_egress()
    ask = fake_ask()

    def with_fact_source(system, user, **kw):
        reply = ask(system, user, **kw)
        if "TASK: explainer video" in user:
            d = json.loads(reply)
            d["sources"] = [{"claim": "It blocks pasted credentials", "url": "",
                             "quote": "detects and blocks credential-like values when someone pastes them"}]
            reply = json.dumps(d)
        return reply
    monkeypatch.setattr(core, "ask", with_fact_source)
    pkg = command(client).json()["package"]
    assert {"claim": "It blocks pasted credentials",
            "quote": "detects and blocks credential-like values when someone pastes them",
            "source": "Company brief (provided by Varun)"} in pkg["sources"]


# ---------------------------------------------------------------- claim verification
def test_unsupported_claim_is_regenerated_away(client, monkeypatch):
    prompts, seen = [], {"A": 0}

    def rule(key, text):
        if key == "A" and text == "Close-up of a laptop":
            seen["A"] += 1
            if seen["A"] == 1:                    # a product visual on the first check only
                return "PRODUCT"
        return "PROBLEM"
    monkeypatch.setattr(core, "ask", fake_ask(prompts=prompts, verify=label_lines(rule)))
    pkg = command(client).json()["package"]
    regen = [u for u in prompts if "TASK: summary and short videos" in u and "A check of your previous reply" in u]
    assert len(regen) == 1 and "[hook scene 1 visual] Close-up of a laptop (rule: shows the product" in regen[0]
    assert pkg["unverified_claims"] == []


def test_claims_still_unsupported_are_listed_at_the_top(client, monkeypatch):
    add_secret_egress()

    def rule(key, text):
        return "PRODUCT" if text in ("Close-up of a laptop", "It works in every browser.") else "PROBLEM"
    monkeypatch.setattr(core, "ask", fake_ask(verify=label_lines(rule), posts_text=lambda u: "It works in every browser."))
    pkg = command(client).json()["package"]
    assert list(pkg)[0] == "unverified_claims"
    got = [(u["part"], u["where"], u["reason"]) for u in pkg["unverified_claims"]]
    assert ("campaign summary, hook and short video", "hook scene 1 visual",
            "rule: shows the product; a product visual must be exactly [WHAT THE USER SEES]") in got
    assert ("posts", "facebook post", "rule: describes the product but is not word for word from the claim bank") in got
    assert pkg["notes"][0].startswith(f"{len(got)} unverified product claim(s)")


def test_a_line_the_checker_leaves_unlabelled_is_unsupported(client, monkeypatch):
    def skip_short_visual(key, user):
        return [x for x in label_lines()(key, user) if not re.search(rf"^{x['id']}\. \[short scene 1 visual\]",
                                                                      user, re.M)]
    monkeypatch.setattr(core, "ask", fake_ask(verify=skip_short_visual))
    pkg = command(client).json()["package"]
    assert {"part": "campaign summary, hook and short video", "where": "short scene 1 visual",
            "claim": "Close-up of a laptop", "reason": "rule: the checker did not label this line"} in pkg["unverified_claims"]


def test_checker_uses_its_own_provider_and_the_writer_groq_low(client, monkeypatch):
    calls, base = [], fake_ask()

    def ask(system, user, **kw):
        calls.append((user, kw))
        return base(system, user, **kw)
    monkeypatch.setattr(core, "ask", ask)
    command(client)
    checker = [k for u, k in calls if "TASK: verify" in u]
    writer = [k for u, k in calls if "TASK: verify" not in u]
    assert checker and all(k["provider"] == "anthropic" and not k.get("llm_options") for k in checker)
    assert writer and all(k.get("provider") is None and k["llm_options"] == {"reasoning_effort": "low"} for k in writer)


def test_without_an_anthropic_key_the_checker_runs_on_groq_at_medium(client, monkeypatch, caplog):
    calls, base = [], fake_ask()

    def ask(system, user, **kw):
        calls.append((user, kw))
        return base(system, user, **kw)
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setattr(core, "ask", ask)
    with caplog.at_level("INFO", logger="agent_team.llm"):
        r = command(client)
    assert r.status_code == 200, r.text
    checker = [k for u, k in calls if "TASK: verify" in u]
    writer = [k for u, k in calls if "TASK: verify" not in u]
    assert checker and all(k["provider"] == "groq" and k["model"] == "openai/gpt-oss-120b"
                           and k["llm_options"] == {"reasoning_effort": "medium"} for k in checker)
    assert writer and all(k.get("provider") is None and k["llm_options"] == {"reasoning_effort": "low"} for k in writer)
    assert any("checker provider=groq model=openai/gpt-oss-120b reasoning_effort=medium" in m for m in caplog.messages)


def test_checker_forced_to_anthropic_without_a_key_fails_before_any_llm_call(client, monkeypatch):
    calls = []
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setenv("CHECKER_PROVIDER", "anthropic")
    monkeypatch.setattr(core, "ask", lambda *a, **k: calls.append(a) or "{}")
    r = command(client)
    assert r.status_code == 502 and "ANTHROPIC_API_KEY" in r.json()["detail"] and calls == []


# ---------------------------------------------------------------- claim bank
def bank_case(client, monkeypatch, post, product_lines=()):
    """Run with Secret Egress rules; the post text is `post`; lines in product_lines are labelled PRODUCT."""
    add_secret_egress()
    rule = lambda key, text: "PRODUCT" if text in product_lines else "PROBLEM"
    monkeypatch.setattr(core, "ask", fake_ask(verify=label_lines(rule), posts_text=lambda u: post))
    pkg = command(client).json()["package"]
    return [(u["where"], u["claim"], u["reason"]) for u in pkg["unverified_claims"] if u["where"] == "facebook post"]


def test_paraphrased_product_sentence_is_flagged(client, monkeypatch):
    para = "Secret Egress stops credential-like values pasted into AI chatbots before they leave the browser."
    assert bank_case(client, monkeypatch, para, [para]) == [
        ("facebook post", para, "rule: describes the product but is not word for word from the claim bank")]


def test_exact_claim_bank_sentence_passes(client, monkeypatch):
    variant = BANK.replace("-", "\u2011")        # the writer often emits a non-breaking hyphen
    assert bank_case(client, monkeypatch, BANK, [BANK]) == []
    assert content.checks.canon(variant) == content.checks.canon(BANK)


def test_pronoun_product_sentence_is_flagged(client, monkeypatch):
    line = "It blocks every paste before it leaves the browser."
    assert bank_case(client, monkeypatch, line, [line]) == [
        ("facebook post", line, "rule: describes the product but is not word for word from the claim bank")]


def test_problem_sentence_passes(client, monkeypatch):
    assert bank_case(client, monkeypatch, "One accidental paste can expose a production API key.") == []


def test_naming_the_product_is_product_even_if_the_checker_says_problem(client, monkeypatch):
    line = "Teams trust Secret Egress."
    assert bank_case(client, monkeypatch, line) == [
        ("facebook post", line, "rule: describes the product but is not word for word from the claim bank")]


# ---------------------------------------------------------------- deterministic checks (the checker says PROBLEM to all)
def rule_issues(pkg, reason_part):
    return [(u["where"], u["claim"]) for u in pkg["unverified_claims"] if reason_part in u["reason"]]


def test_check1_cross_offering_capability_is_flagged(client, monkeypatch):
    add_secret_egress()
    brain = {**BRAIN, "offerings": BRAIN["offerings"] + [
        {"name": "Prove", "description": "Creates tamper-evident receipts as proof of each action."}]}
    with core.db() as c:
        c.execute("UPDATE tenants SET brain=?", (json.dumps(brain),))
    terms = content.checks.cross_offering_terms(SECRET_EGRESS, [SECRET_EGRESS], brain)
    assert {"proof", "receipts", "tamper"} <= {w for w, _ in terms.values()}   # built from the brief, not by hand

    def edit(task, d):
        if task == "A":
            d["short"]["scenes"].append({"line": "Get tamper-evident proof of every block.", "visual": ""})
    monkeypatch.setattr(core, "ask", fake_ask(edit=edit))
    pkg = command(client).json()["package"]
    assert ("short scene 2 voiceover", "Get tamper-evident proof of every block.") in rule_issues(pkg, "capability of Prove")


def test_product_visual_must_be_the_placeholder(client, monkeypatch):
    def edit(task, d):
        if task == "A":
            d["short"]["scenes"][0]["visual"] = "A red warning overlay appears over the paste."
            d["hook"]["scenes"][0]["visual"] = "[WHAT THE USER SEES]"
    product = lambda key, text: "PRODUCT" if "overlay" in text or "WHAT THE USER SEES" in text else "PROBLEM"
    monkeypatch.setattr(core, "ask", fake_ask(edit=edit, verify=label_lines(product)))
    pkg = command(client).json()["package"]
    assert rule_issues(pkg, "a product visual must be exactly") == [
        ("short scene 1 visual", "A red warning overlay appears over the paste.")]


def test_check4_invented_asset_is_flagged(client, monkeypatch):
    def edit(task, d):
        if task == "A":
            d["campaign_summary"]["call_to_action"] = "Download the one-pager."
    monkeypatch.setattr(core, "ask", fake_ask(edit=edit))
    pkg = command(client).json()["package"]
    assert rule_issues(pkg, "offers 'one-pager'") == [("summary call to action", "Download the one-pager.")]


def test_check5_required_aspect_ratios(client, monkeypatch):
    def edit(task, d):
        if task == "A":
            d["hook"]["aspect_ratios"] = ["9:16"]
        if task == "B":
            d["explainer"]["aspect_ratios"] = ["1:1"]
    monkeypatch.setattr(core, "ask", fake_ask(edit=edit))
    pkg = command(client).json()["package"]
    reasons = [u["reason"] for u in pkg["unverified_claims"]]
    assert "rule: the hook video must include 9:16 and 4:5; missing 4:5" in reasons
    assert "rule: the explainer video must include 16:9; missing 16:9" in reasons


def test_only_approved_placeholders_with_nothing_inside(client, monkeypatch):
    def edit(task, d):
        if task == "A":
            d["campaign_summary"]["call_to_action"] = "[CTA: Request a demo]"
            d["short"]["caption"] = "Protect every paste. [CTA]"
    monkeypatch.setattr(core, "ask", fake_ask(edit=edit))
    pkg = command(client).json()["package"]
    assert rule_issues(pkg, "is not an approved placeholder") == [("summary call to action", "[CTA: Request a demo]")]


def test_named_offering_is_a_known_topic(client, monkeypatch):
    add_secret_egress()
    prompts, base = [], fake_ask()

    def ask(system, user, **kw):
        prompts.append(user)
        if "TASK: intent" in user:               # the topic is the angle, not the product name
            return json.dumps({"route": "content", "platforms": ["facebook"], "topic": "team-wide security risk"})
        return base(system, user, **kw)
    monkeypatch.setattr(core, "ask", ask)
    pkg = command(client).json()["package"]
    assert not any("UNKNOWN TOPIC" in u for u in prompts)
    assert not any("not described" in n for n in pkg["notes"])


def test_duplicate_open_questions_are_removed():
    qs = ["What specific CTA wording and link should be used for the demo request or one-pager download?",
          "What specific CTA text and link should be used for the demo request or one-pager download?",
          "Which browsers does Secret Egress support?"]
    assert content.checks.dedupe_questions(qs) == [qs[0], qs[2]]


# ---------------------------------------------------------------- banned terms (second line of defence)
def test_banned_claim_is_regenerated_away(client, monkeypatch):
    add_secret_egress()
    prompts = []
    monkeypatch.setattr(core, "ask", fake_ask(prompts=prompts, posts_text=lambda u: (
        "Blocks pasted secrets." if "Rewrite without mentioning" in u else "Now in Gmail and your SIEM too.")))
    pkg = command(client).json()["package"]
    assert "gmail" not in json.dumps(pkg).lower()
    assert any("FOCUS PRODUCT: Secret Egress" in u for u in prompts)
    assert any("mentioned Gmail, SIEM" in u for u in prompts)
    assert not any("not described" in n for n in pkg["notes"])


def test_banned_claim_that_persists_is_flagged(client, monkeypatch):
    add_secret_egress()
    monkeypatch.setattr(core, "ask", fake_ask(posts_text=lambda u: "Now in Gmail and your SIEM too."))
    pkg = command(client).json()["package"]
    assert pkg["notes"][0].startswith("Remove before using: the copy mentions Gmail, SIEM")


# ---------------------------------------------------------------- truncation and partial success
def truncating(times, prompts=None, kws=None):
    """The explainer reply is cut off for length `times` times (None = always), then succeeds."""
    base, seen = fake_ask(), {"n": 0}

    def ask(system, user, **kw):
        if prompts is not None:
            prompts.append(user)
        if kws is not None:
            kws.append(kw)
        if "TASK: explainer video" in user and (times is None or seen["n"] < times):
            seen["n"] += 1
            raise core.LLMTruncated("truncated reply (finish_reason=length); response cut off")
        return base(system, user, **kw)
    return ask


def test_truncated_part_is_retried_with_a_shorter_brief(client, monkeypatch):
    prompts, kws = [], []
    monkeypatch.setattr(core, "ask", truncating(1, prompts, kws))
    r = command(client)
    assert r.status_code == 200 and r.json()["warning"] is None
    retry = [u for u in prompts if "TASK: explainer video" in u and "cut off because it was too long" in u]
    assert len(retry) == 1
    assert r.json()["package"]["videos"]["explainer"]["duration_seconds"] == 120
    # every content-agent call is labelled for the log; writer calls ask for low reasoning effort
    assert kws and all(k.get("label") for k in kws)
    assert all(k["llm_options"] == {"reasoning_effort": "low"} for k in kws if k.get("provider") is None)


def test_part_that_still_fails_is_saved_with_the_rest(client, monkeypatch):
    prompts = []
    monkeypatch.setattr(core, "ask", truncating(None, prompts))
    r = command(client)
    assert r.status_code == 200
    body = r.json()
    assert "explainer video" in body["warning"]
    pkg = body["package"]
    assert pkg["videos"]["explainer"]["status"] == "failed, regenerate"
    assert "truncated" in pkg["videos"]["explainer"]["error"]
    assert pkg["videos"]["hook"]["duration_seconds"] == 5 and pkg["posts"][0]["platform"] == "facebook"
    assert body["warning"] in pkg["notes"]
    assert not any("verify claims (part B)" in u for u in prompts)      # nothing to check for a failed part
    stored = client.get(f"/tenants/{TID}/outputs", params={"agent": "content"}).json()[0]
    assert stored["id"] == body["output_id"] and "failed, regenerate" in stored["content"]


def test_every_part_failing_returns_502(client, monkeypatch):
    base = fake_ask()

    def ask(system, user, **kw):
        if "TASK: intent" in user:
            return base(system, user, **kw)
        raise core.LLMTruncated("truncated reply (finish_reason=length); response cut off")
    monkeypatch.setattr(core, "ask", ask)
    r = command(client)
    assert r.status_code == 502 and "Every part of the package failed" in r.json()["detail"]
    assert client.get(f"/tenants/{TID}/outputs", params={"agent": "content"}).json() == []


# ---------------------------------------------------------------- approved packages as style examples
def add_package(request, status="approved", line="Your developer just pasted an API key into a chatbot.",
                decided_at="2026-10-22T00:00:00"):
    pkg = {"videos": {"short": {"duration_seconds": 30, "aspect_ratios": ["9:16"],
                                "scenes": [{"line": line, "visual": ""}]}}}
    with core.db() as c:
        return c.execute("INSERT INTO outputs(tenant_id,agent,content,created_at,status,note,decided_at) "
                         "VALUES(?,?,?,?,?,?,?)",
                         (TID, "content", json.dumps({"request": request, "intent": {}, "package": pkg}),
                          decided_at, status, "Good pacing", decided_at)).lastrowid


def test_fact_only_in_an_example_package_is_still_unsupported(client, monkeypatch):
    add_secret_egress()
    add_package("Hand-written reference for Secret Egress", line="Secret Egress works in Safari and Firefox.")
    claim = "Secret Egress works in Safari and Firefox."
    fooled = label_lines(lambda key, text: "PROBLEM")             # the checker misses it entirely
    calls, base = [], fake_ask(posts_text=lambda u: claim, verify=fooled)

    def ask(system, user, **kw):
        calls.append((system, user))
        reply = base(system, user, **kw)
        if "TASK: explainer video" in user:            # and the copy cites the example as a source
            d = json.loads(reply)
            d["sources"] = [{"claim": claim, "quote": claim, "url": ""}]
            reply = json.dumps(d)
        return reply
    monkeypatch.setattr(core, "ask", ask)
    pkg = command(client).json()["package"]

    gen_systems = [s for s, u in calls if "TASK: summary and short videos" in u]
    assert "STYLE EXAMPLES" in gen_systems[0] and "NOT a source of product facts" in gen_systems[0]
    assert claim in gen_systems[0]                                   # the example reached the writer...
    checker_ctx = [s + u.split("COPY TO CHECK")[0] for s, u in calls if "TASK: verify" in u]
    assert checker_ctx and not any("Safari" in f for f in checker_ctx)          # ...but never the checker's context
    assert {"part": "posts", "where": "facebook post", "claim": claim,
            "reason": "rule: describes the product but is not word for word from the claim bank"} in pkg["unverified_claims"]
    assert claim not in json.dumps(pkg["sources"])
    assert any(claim in q for q in pkg["open_questions"])


def test_style_examples_are_the_3_latest_approved_for_the_same_offering(client):
    ids = [add_package(f"Secret Egress ad {i}", decided_at=f"2026-10-0{i}T00:00:00") for i in range(1, 5)]
    add_package("Secret Egress ad, rejected", status="rejected", decided_at="2026-10-09T00:00:00")
    add_package("Secret Egress ad, draft", status="draft", decided_at="2026-10-09T00:00:00")
    add_package("Protect launch ad", decided_at="2026-10-09T00:00:00")
    assert [e["id"] for e in content.style_examples(TID, [SECRET_EGRESS])] == ids[::-1][:3]
    assert content.style_examples(TID, []) == []


# ---------------------------------------------------------------- approve / reject
def test_patch_approve_and_reject(client):
    oid = command(client).json()["output_id"]
    url = f"/tenants/{TID}/outputs/{oid}"

    r = client.patch(url, json={"status": "approved", "note": "Good hook"})
    assert r.status_code == 200
    row = client.get(f"/tenants/{TID}/outputs", params={"agent": "content"}).json()[0]
    assert (row["status"], row["note"]) == ("approved", "Good hook") and row["decided_at"]

    r = client.patch(url, json={"status": "rejected", "note": "Too salesy"})
    assert r.status_code == 200
    row = client.get(f"/tenants/{TID}/outputs", params={"agent": "content"}).json()[0]
    assert (row["status"], row["note"]) == ("rejected", "Too salesy")


def test_patch_invalid_status_returns_400(client):
    oid = command(client).json()["output_id"]
    r = client.patch(f"/tenants/{TID}/outputs/{oid}", json={"status": "published"})
    assert r.status_code == 400


# ---------------------------------------------------------------- protected offering rules
def test_teach_your_team_leaves_offering_rules_byte_identical(client, monkeypatch):
    add_secret_egress()
    before = offerings.get_text(TID, "Secret Egress")
    rewritten = {**{k: [] for k in core.BRAIN_SECTIONS}, **BRAIN,
                 "offerings": BRAIN["offerings"] + [{"name": "Secret Egress",
                                                     "description": "Now covers Gmail and SIEM."}],
                 "open_questions": [], "sources": []}

    def ask(system, user, **kw):
        if "USER CORRECTIONS" in user:
            return json.dumps(rewritten)
        if "Create the playbook" in user:
            return json.dumps({"mission": "m"})
        if "welcome_message" in user:
            return json.dumps({"welcome_message": "hi"})
        raise AssertionError("unexpected prompt: " + user[:80])
    monkeypatch.setattr(core, "ask", ask)

    r = client.post(f"/tenants/{TID}/confirm", json={"corrections": "Secret Egress now covers Gmail and SIEM."})
    assert r.status_code == 200
    t = core.get_tenant(TID)
    assert t["error"] is None and "Now covers Gmail" in json.dumps(t["brain"])   # the rewrite did run
    assert offerings.get_text(TID, "Secret Egress") == before                    # byte-for-byte unchanged
    # and the content agent ignores what the rewrite wrote about a protected offering
    assert "Gmail" not in content._researched_facts(t["brain"], {"secret egress"})


def test_offering_edit_shows_diff_and_refuses_stale_save(client):
    rule = SECRET_EGRESS.model_dump()
    p = client.post(f"/tenants/{TID}/offerings/preview", json=rule).json()
    assert p["changed"] and p["base_version"] is None and '+  "name": "Secret Egress",' in p["diff"]
    saved = client.put(f"/tenants/{TID}/offerings", json={"rule": rule, "base_version": None}).json()

    edited = {**rule, "facts": rule["facts"] + ["It works in Chrome."]}
    p = client.post(f"/tenants/{TID}/offerings/preview", json=edited).json()
    assert p["base_version"] == saved["version"] and '+    "It works in Chrome."' in p["diff"]
    stale = client.put(f"/tenants/{TID}/offerings", json={"rule": edited, "base_version": "deadbeef0000"})
    assert stale.status_code == 409
    ok = client.put(f"/tenants/{TID}/offerings", json={"rule": edited, "base_version": p["base_version"]})
    assert ok.status_code == 200
    assert client.get(f"/tenants/{TID}/offerings").json()[0]["facts"][-1] == "It works in Chrome."


def test_protected_claim_bank_registry_validation_and_forbidden_terms():
    registry = offerings.ProtectedClaimBankRegistry()
    offering = registry.get_offering("core_agent_suite_v1")
    assert offering is not None
    assert offering.name == "Core Agent Suite"

    # Authorized claim validation
    valid_claim = "Multi-tenant SQLite persistence with thread-safe connection pooling"
    assert registry.validate_claim("core_agent_suite_v1", valid_claim)
    assert not registry.validate_claim("core_agent_suite_v1", "Made up marketing boast")
    assert not registry.validate_claim("non_existent_offering", valid_claim)

    # Forbidden terms scanning
    dirty_text = "Our solution guarantees 100% autonomous operation with unlimited scaling and zero human oversight."
    flagged = registry.scan_forbidden_terms("core_agent_suite_v1", dirty_text)
    assert "100% autonomous" in flagged
    assert "unlimited scaling" in flagged
    assert "zero human oversight" in flagged

    clean_text = "Standard human oversight with deterministic rule checks."
    assert registry.scan_forbidden_terms("core_agent_suite_v1", clean_text) == []


def test_content_verifier_audit_engine():
    v = checks.ContentVerifier("core_agent_suite_v1")

    # 1. Perfectly compliant content
    valid_claim = "Deterministic multi-stage drafting pipeline with claim-bank validation"
    valid_text = "Deploy our platform at $49.00 per month for foundational agent orchestration."
    res = v.verify_content(valid_text, extracted_claims=[valid_claim])
    assert res.passed
    assert res.forbidden_terms_found == []
    assert res.unauthorized_claims == []
    assert res.pricing_violations == []
    assert "All verification checks passed" in res.details

    # 2. Forbidden terms violation
    bad_term_text = "Experience 100% autonomous operation with instantaneous execution."
    res_bad_terms = v.verify_content(bad_term_text)
    assert not res_bad_terms.passed
    assert "100% autonomous" in res_bad_terms.forbidden_terms_found
    assert "instantaneous execution" in res_bad_terms.forbidden_terms_found

    # 3. Pricing boundary violation
    bad_price_text = "Get started today for only $19.99 or $999.00 enterprise tier."
    res_price = v.verify_content(bad_price_text)
    assert not res_price.passed
    assert len(res_price.pricing_violations) == 2
    assert any("$19.99" in p for p in res_price.pricing_violations)
    assert any("$999.0" in p for p in res_price.pricing_violations)

    # 4. Unauthorized claim extraction
    res_claim = v.verify_content("Generic text", extracted_claims=["Unverified boast that was never in claim bank."])
    assert not res_claim.passed
    assert res_claim.unauthorized_claims == ["Unverified boast that was never in claim bank."]

    # 5. Non-existent offering
    v_missing = checks.ContentVerifier("non_existent_offering")
    res_missing = v_missing.verify_content("Text")
    assert not res_missing.passed
    assert "not found in registry" in res_missing.details


def test_pipeline_pricing_verification_and_rewrite_flow(client, monkeypatch):
    # Setup offering with pricing boundaries
    pricing_rule = offerings.ProductOffering(
        offering_id="secret_egress_v1",
        name="Secret Egress",
        authorized_claims=[
            "Secret Egress, from ActTrident, blocks credential-like values pasted into AI chatbots, before they leave the browser."
        ],
        pricing_boundaries={"standard_monthly": 49.00, "enterprise_monthly": 199.00},
        forbidden_terms=["unlimited scaling"],
    )
    offerings.save(TID, pricing_rule, None)

    # 1. First run: unauthorized price $25.00 triggers rewrite, fixed to $49.00 on rewrite
    rewrite_triggered = []
    def dynamic_posts(prompt):
        if "A check of your previous reply" in prompt:
            rewrite_triggered.append(True)
            return "Get standard security at $49.00 monthly."
        return "Special deal for only $25.00 this month."

    monkeypatch.setattr(core, "ask", fake_ask(posts_text=dynamic_posts))
    res = command(client).json()
    pkg = res["package"]
    assert rewrite_triggered == [True]
    assert pkg["unverified_claims"] == []
    assert res["output_id"] is not None

    # Verify output in database has draft status (pending human sign-off)
    outputs = client.get(f"/tenants/{TID}/outputs", params={"agent": "content"}).json()
    assert outputs[0]["status"] == "draft"

    # 2. Second run: rewrite does NOT fix unauthorized price, survives to unverified claims
    monkeypatch.setattr(core, "ask", fake_ask(posts_text=lambda u: "Still unauthorized at $25.00 forever."))
    res2 = command(client).json()
    pkg2 = res2["package"]
    assert any("Unauthorized price detected: $25.0" in u["reason"] for u in pkg2["unverified_claims"])
    assert any("survived the rewrite" in n for n in pkg2["notes"])



