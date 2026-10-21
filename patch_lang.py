path = r'agent_team/core.py'
s = open(path, encoding='utf-8').read()

# Update _system_for to include language
old = '''def _system_for(t: dict, agent: str) -> str:
    today = dt.datetime.now(ZoneInfo(t["timezone"])).strftime("%A, %d %B %Y")
    return render(AGENT_SYSTEM, {"agent": agent, "today": today,
                                 "brain": json.dumps(t["brain"], ensure_ascii=False),
                                 "playbook": json.dumps(t["playbooks"][agent], ensure_ascii=False)})'''

new = '''def _system_for(t: dict, agent: str) -> str:
    today = dt.datetime.now(ZoneInfo(t["timezone"])).strftime("%A, %d %B %Y")
    lang = t.get("language") or "English"
    base = render(AGENT_SYSTEM, {"agent": agent, "today": today,
                                 "brain": json.dumps(t["brain"], ensure_ascii=False),
                                 "playbook": json.dumps(t["playbooks"][agent], ensure_ascii=False)})
    return base + f"\\n\\nLANGUAGE: Respond in {lang} only. Do not mix languages.\\n"'''

if old not in s:
    print('not found')
    raise SystemExit(1)
s2 = s.replace(old, new)
open(path, 'w', encoding='utf-8').write(s2)
print('ok')
