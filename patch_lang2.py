path = r'agent_team/core.py'
s = open(path, encoding='utf-8').read()

# Also update playbook/welcome to be stricter
old = '''    sys_p = "You design agent playbooks. Output JSON only. " + GUARDRAILS
    playbooks = {}
    for agent in AGENTS:
        prompt = (f"Create the playbook for ONLY this agent: {agent}.\\n"
                  f"Company brain (compact):\\n{brain_str}\\n"
                  f"Timezone: {timezone}. Language: {language}.\\n"
                  "Return ONE JSON object with keys: mission, steps, tools, output_format, "
                  "quality_checklist, never_do, schedule, kpis. Be specific to this company. "
                  "Keep it concise (max 6 steps).")'''

new = '''    sys_p = f"You design agent playbooks. Output JSON only. Respond in {language} only. " + GUARDRAILS
    playbooks = {}
    for agent in AGENTS:
        prompt = (f"Create the playbook for ONLY this agent: {agent}.\\n"
                  f"Company brain (compact):\\n{brain_str}\\n"
                  f"Timezone: {timezone}. Language: {language}.\\n"
                  f"All text fields (mission, steps, tools, output_format, quality_checklist, never_do, kpis) MUST be in {language}.\\n"
                  "Return ONE JSON object with keys: mission, steps, tools, output_format, "
                  "quality_checklist, never_do, schedule, kpis. Be specific to this company. "
                  "Keep it concise (max 6 steps).")'''

if old not in s:
    print('not found')
    raise SystemExit(1)
s2 = s.replace(old, new)
open(path, 'w', encoding='utf-8').write(s2)
print('ok')
