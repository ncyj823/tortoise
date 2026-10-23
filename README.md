# AI Marketing Team (multi-agent, any company)

Login with work email -> AI researches the company -> Company Brain -> playbooks for 5 agents -> daily 10 AM research digest.

**AI Marketing Team** is a local-first, multi-agent content system. A plain-English request becomes a campaign
package: a hook, a ~30s script, an explainer video, platform posts and (optionally) a long-form article. Every
product claim comes from a protected claim bank, unverified claims are flagged at the top of the package, nothing
is published without human approval, and approvals/rejections teach the system.

It is entered in the DEV **"Hacktoberfest Open-Source AI Challenge, Week 3: Slow and Steady"**.

## Run

    python -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env        # add LLM_BASE_URL / LLM_API_KEY / LLM_MODEL (and ANTHROPIC_API_KEY for the Anthropic writer + native web search)

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

## The slow, trustworthy way (the product idea)

The system is slow **on purpose**. A campaign package goes through draft -> label -> check -> rewrite -> human
approval, and that is the feature:

1. **Draft**: the content agent turns the plain-English request into one campaign package (summary, hook, short
   video, explainer, posts, optional article), saved as a draft.
2. **Label**: every sentence and visual is classified PRODUCT or PROBLEM by the claim checker.
3. **Check**: every PRODUCT line must be copied word for word from the product's CLAIM BANK in the protected
   offering rules; visuals must be exactly `[WHAT THE USER SEES]`; placeholders must be approved; aspect ratios
   must be right; cross-offering capability leaks are caught. The checker's labels cannot override these
   deterministic checks.
4. **Rewrite**: if anything fails, the part is regenerated once with a "fix these" brief; survivors are listed.
5. **Human approval**: nothing is published. Approve/reject; the note is stored and later approved packages about
   the same offering become style examples (tone and structure only, never a source of product facts).

Unverified product claims are listed at the top of the package under
"Unverified claims, remove before use".

## Decisions

- **Checker backend**: defaults to the OpenAI-compatible endpoint (`CHECKER_MODEL`, default `openai/gpt-oss-120b`)
  when no Anthropic key is set; with `ANTHROPIC_API_KEY` + `CHECKER_PROVIDER=anthropic` it uses Anthropic. Simplest
  option that keeps the command box usable without a second vendor key.
- **Writer backend**: OpenAI-compatible endpoint (`LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL`) by default, Anthropic
  when `LLM_PROVIDER=anthropic`.
- **One regeneration only**: after a failed check the part is rewritten once; anything still bad stays listed as
  unverified rather than looping forever.
- **No new framework**: one FastAPI app + one static UI file, standard library and `requirements.txt` only. No
  LangChain/CrewAI.
- **Simple language handling**: rewrite once, then flag what is left; the final draft is prefixed with the language
  check note if it could not be fixed.

## Safety

- Never open, print, log or commit a real `.env` file. Only `.env.example` is tracked; copy it to `.env` and add keys.
- Product sentences must be word for word from the claim bank; `agent_team/checks.py` and the protected offering
  rules in `agent_team/offerings.py` are the source of truth and are not weakened by features or edits.
- Approvals/rejections teach the system: approved packages become style examples; rejection notes are stored.
