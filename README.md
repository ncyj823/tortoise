# AI Marketing Team (multi-agent, any company)

Login with work email -> AI researches the company -> Company Brain -> playbooks for 5 agents -> daily 10 AM research digest.

## Run
    python -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env        # add ANTHROPIC_API_KEY
    uvicorn agent_team.main:app --reload

## Try it
    curl -X POST localhost:8000/onboard -H 'content-type: application/json' \
      -d '{"work_email":"you@yourcompany.com","user_role":"Founder","language":"Hinglish"}'
    curl localhost:8000/tenants/yourcompany.com          # poll until status=ready
    curl -X POST localhost:8000/tenants/yourcompany.com/agents/research/run
    curl -X POST localhost:8000/tenants/yourcompany.com/agents/blog/run \
      -H 'content-type: application/json' -d '{"instruction":"Blog on <topic>"}'
    curl -X POST localhost:8000/tenants/yourcompany.com/confirm \
      -H 'content-type: application/json' -d '{"corrections":"We target D2C brands, not enterprises."}'

## Flow
1. /onboard: domain from email (free domains need company_name + website), background job runs research (web search) -> brain JSON.
2. Second LLM call turns the brain into 5 agent playbooks + welcome message.
3. /confirm applies user corrections and regenerates playbooks.
4. Scheduler: Research agent daily 10:00 and Analytics Mondays 9:00 in the tenant's timezone. Digest goes to DB, /digest and Telegram (optional).
5. Every output is a draft (status=pending_approval). Nothing is auto-posted.
