path = r'agent_team/core.py'
s = open(path, encoding='utf-8').read()

# Update run_agent to add language
old = '''def run_agent(tid: str, agent: str, instruction: str = ""):
    if agent == "research":
        return json.dumps(run_research(tid), ensure_ascii=False, indent=2)
    t = get_tenant(tid)
    task = instruction or DEFAULT_TASKS[agent]
    text = ask(_system_for(t, agent), task, search=(agent != "social"))
    with db() as c:
        c.execute("INSERT INTO outputs(tenant_id,agent,content,created_at) VALUES(?,?,?,?)",
                  (tid, agent, text, dt.datetime.utcnow().isoformat()))
    return text'''

new = '''def run_agent(tid: str, agent: str, instruction: str = ""):
    if agent == "research":
        return json.dumps(run_research(tid), ensure_ascii=False, indent=2)
    t = get_tenant(tid)
    lang = t.get("language") or "English"
    task = instruction or DEFAULT_TASKS[agent]
    task = f"LANGUAGE: {lang}. ALL OUTPUT MUST BE IN {lang} ONLY. NO MIXING.\\n\\n{task}"
    text = ask(_system_for(t, agent), task, search=(agent != "social"))
    with db() as c:
        c.execute("INSERT INTO outputs(tenant_id,agent,content,created_at) VALUES(?,?,?,?)",
                  (tid, agent, text, dt.datetime.utcnow().isoformat()))
    return text'''

if old not in s:
    print('not found')
    raise SystemExit(1)
s2 = s.replace(old, new)
open(path, 'w', encoding='utf-8').write(s2)
print('ok')
