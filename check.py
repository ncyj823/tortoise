"""Self-check: python check.py [--onboard company.com] [--research]
Tests each layer separately so failures are easy to locate."""
import sys, json, traceback
from agent_team import core

def step(name, fn):
    try:
        r = fn(); print(f"PASS  {name}" + (f"  ->  {r}" if r else "")); return True
    except Exception as e:
        print(f"FAIL  {name}  ->  {type(e).__name__}: {e}")
        if "-v" in sys.argv: traceback.print_exc()
        return False

def llm():
    out = core.extract_json(core.ask("Reply with JSON only.", 'Return exactly {"ok": true}', max_tokens=200))
    assert out.get("ok") is True, f"unexpected reply: {out}"
    return f"{core.PROVIDER} / {core.os.getenv('LLM_MODEL') or core.MODEL}"

def search():
    r = core.web_search("Razorpay company about")
    assert r, "0 results (DuckDuckGo blocked or rate-limited). Set TAVILY_API_KEY."
    return f"{len(r)} results, first: {r[0]['url']}"

def onboard(domain):
    def go():
        core.init_db()
        tid = core.start_onboarding({"work_email": f"test@{domain}", "language": "Hinglish"})
        core.run_onboarding(tid, {"work_email": f"test@{domain}"})
        t = core.get_tenant(tid)
        assert t["status"] == "ready", f"status={t['status']} error={t['error']}"
        assert set(t["playbooks"]) == set(core.AGENTS), "playbooks missing agents"
        return f"{t['brain']['company'].get('name')} | open questions: {len(t['brain'].get('open_questions', []))}"
    return go

def research(domain):
    def go():
        items = core.run_research(domain)
        assert items, "0 companies returned"
        return f"{len(items)} new, e.g. {items[0]['name']}"
    return go

if __name__ == "__main__":
    ok = step("LLM connection", llm) and step("Web search", search)
    if ok and "--onboard" in sys.argv:
        d = sys.argv[sys.argv.index("--onboard") + 1]
        ok = step(f"Onboarding {d} (takes a few minutes)", onboard(d))
        if ok and "--research" in sys.argv:
            step("Research agent (10 companies)", research(d))
    print("\nNext: uvicorn agent_team.main:app --reload  ->  http://localhost:8000" if ok else "\nFix the FAIL above first.")
