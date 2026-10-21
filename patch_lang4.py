from agent_team import core
with core.db() as c:
    c.execute("UPDATE tenants SET language='English' WHERE id='acttrident.ai'")
t = core.get_tenant('acttrident.ai')
print(t['language'])
