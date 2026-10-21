import re, shutil, sys, py_compile

C = "agent_team/core.py"
M = "agent_team/main.py"
shutil.copy(C, C + ".bak")
shutil.copy(M, M + ".bak")

NEW_CORE = r'''
def _build_playbooks(tid: str, brain: dict, timezone: str, language: str):
    """One small call per agent (+1 for the welcome message) so replies never get truncated."""
    brain_str = json.dumps(_compact_brain_for_playbooks(brain), ensure_ascii=False)
    sys_p = "You design agent playbooks. Output JSON only. " + GUARDRAILS
    playbooks = {}
    for agent in AGENTS:
        prompt = (f"Create the playbook for ONLY this agent: {agent}.\n"
                  f"Company brain (compact):\n{brain_str}\n"
                  f"Timezone: {timezone}. Language: {language}.\n"
                  "Return ONE JSON object with keys: mission, steps, tools, output_format, "
                  "quality_checklist, never_do, schedule, kpis. Be specific to this company. "
                  "Keep it concise (max 6 steps).")
        try:
            pb = ask_json(sys_p, prompt, max_tokens=2500)
        except Exception as e:
            raise LLMOutputError(f"Playbook for {agent} failed: {e}") from e
        if isinstance(pb, dict) and isinstance(pb.get("playbook"), dict):
            pb = pb["playbook"]
        if not isinstance(pb, dict) or not pb.get("mission"):
            raise LLMOutputError(f"Playbook for {agent} has no 'mission'")
        playbooks[agent] = pb
    wprompt = (f"Write a short, friendly welcome_message in {language} for this company.\n"
               f"Company brain (compact):\n{brain_str}\n"
               "Include: 1) what you learned about the company (max 5 bullets) 2) one line per agent "
               "on the plan 3) max 5 confirm/correct questions from open_questions 4) what happens next. "
               'Return JSON: {"welcome_message": "..."}')
    try:
        w = ask_json("Write a short welcome message. Output JSON only. " + GUARDRAILS, wprompt, max_tokens=1500)
    except Exception as e:
        raise LLMOutputError(f"Welcome message failed: {e}") from e
    welcome = (w.get("welcome_message") or w.get("welcome") or "") if isinstance(w, dict) else str(w)
    if not welcome.strip():
        raise LLMOutputError("Welcome message was empty")
    return playbooks, welcome


_RUNNING = set()   # stops the same tenant being onboarded twice at once


def run_onboarding(tid: str, p: dict):
    """Step 1: research -> brain (saved at once). Step 2: playbooks. Resumes if a brain already exists."""
    if tid in _RUNNING:
        return
    _RUNNING.add(tid)
    try:
        t = get_tenant(tid)
        brain = t.get("brain")
        if not brain:
            today = dt.datetime.now(ZoneInfo(t["timezone"])).strftime("%Y-%m-%d")
            base = p.get("company_name") or tid.split(".")[0]
            seed = [f"{base} company about", f"{base} products services pricing", f"{base} LinkedIn company page",
                    f"{base} Instagram", f"{base} competitors alternatives", f"{base} news 2026", f"{base} reviews"]
            brain = ask_json_checked(
                "You are a meticulous company researcher. Use web search. Output JSON only.",
                render(BRAIN_PROMPT, {"work_email": p["work_email"], "company_name": p.get("company_name"),
                                      "website": t["website"], "user_role": p.get("user_role"),
                                      "today_date": today}),
                _check_brain,
                search=True, seed=seed, extra="WEBSITE HOMEPAGE TEXT:\n" + fetch_page(t["website"]))
            with db() as c:
                c.execute("UPDATE tenants SET brain=?, error=NULL WHERE id=?",
                          (json.dumps(brain, ensure_ascii=False), tid))
        playbooks, welcome = _build_playbooks(tid, brain, t["timezone"], t["language"])
        with db() as c:
            c.execute("UPDATE tenants SET playbooks=?,welcome=?,status='ready',error=NULL WHERE id=?",
                      (json.dumps(playbooks, ensure_ascii=False), welcome, tid))
    except Exception as e:
        log.exception("Onboarding failed for %s", tid)
        with db() as c:
            c.execute("UPDATE tenants SET status='failed', error=? WHERE id=?", (str(e), tid))
    finally:
        _RUNNING.discard(tid)
'''

NEW_MAIN = r'''
    tid = core.start_onboarding(p)
    t = core.get_tenant(tid)
    if t["status"] == "ready":   # same-domain user: reuse the existing brain
        return {"tenant_id": tid, "status": "ready", "welcome_message": t["welcome"]}
    with core.db() as c:         # new, failed (resumes from saved brain) or interrupted
        c.execute("UPDATE tenants SET status='researching', error=NULL WHERE id=?", (tid,))
    bg.add_task(core.run_onboarding, tid, p)
    return {"tenant_id": tid, "status": "researching", "poll": f"/tenants/{tid}"}
'''

s = open(C, encoding="utf-8").read()
for name in ("LLMOutputError", "ask_json_checked", "_check_brain",
             "_compact_brain_for_playbooks", "ask_json", "log"):
    if name not in s:
        sys.exit(f"ABORT: '{name}' not found in core.py")
a = s.index("def _build_playbooks")
r = s.index("def run_onboarding")
if a > r:
    sys.exit("ABORT: run_onboarding comes before _build_playbooks, unexpected layout")
pos = s.index("\n", r) + 1
m = re.search(r"^\S", s[pos:], re.M)
b = pos + m.start() if m else len(s)
s = s[:a] + NEW_CORE.strip("\n") + "\n\n\n" + s[b:]
s = s.replace("\nload_dotenv()\n", "\nfrom .prompts import GUARDRAILS\nload_dotenv()\n", 1)
open(C, "w", encoding="utf-8").write(s)

t = open(M, encoding="utf-8").read()
if "    tid = core.start_onboarding(p)" not in t or '@app.get("/tenants/{tid}")' not in t:
    sys.exit("ABORT: markers not found in main.py")
a = t.index("    tid = core.start_onboarding(p)")
b = t.index('@app.get("/tenants/{tid}")')
if a > b:
    sys.exit("ABORT: unexpected order in main.py")
t = t[:a] + NEW_MAIN.strip("\n") + "\n\n\n" + t[b:]
open(M, "w", encoding="utf-8").write(t)

py_compile.compile(C, doraise=True)
py_compile.compile(M, doraise=True)
print("PATCH OK (backups: core.py.bak, main.py.bak)")
