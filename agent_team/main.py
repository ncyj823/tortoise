import os
from contextlib import asynccontextmanager
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from pathlib import Path
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import core, content, offerings

scheduler = BackgroundScheduler()


def schedule_tenant(t: dict):
    """Research agent: every day 10:00 in the tenant's timezone. Analytics: Monday 9:00."""
    tz = ZoneInfo(t["timezone"])
    scheduler.add_job(core.run_research, CronTrigger(hour=10, minute=0, timezone=tz),
                      args=[t["id"]], id=f"research:{t['id']}", replace_existing=True)
    scheduler.add_job(core.run_agent, CronTrigger(day_of_week="mon", hour=9, minute=0, timezone=tz),
                      args=[t["id"], "analytics"], id=f"analytics:{t['id']}", replace_existing=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    core.init_db()
    for t in core.all_tenants():
        schedule_tenant(t)
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="AI Marketing Team", lifespan=lifespan)
UI = Path(__file__).parent / "static" / "index.html"


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(UI)


@app.get("/health")
def health():
    """Used by the UI and simple uptime checks. Never returns the key itself."""
    return {"status": "ok",
            "llm": {"provider": core.PROVIDER,
                    "model": os.getenv("LLM_MODEL") or None,
                    "configured": bool((os.getenv("LLM_API_KEY") or "").strip())}}


@app.get("/tenants")
def tenants():
    with core.db() as c:
        return [dict(r) for r in c.execute("SELECT id, status FROM tenants ORDER BY created_at DESC")]


class OnboardIn(BaseModel):
    work_email: str
    user_name: str | None = None
    user_role: str | None = None
    timezone: str | None = "Asia/Kolkata"
    language: str | None = "English"        # e.g. "Hinglish"
    company_name: str | None = None         # needed only for free-email domains
    website: str | None = None


@app.post("/onboard")
def onboard(body: OnboardIn, bg: BackgroundTasks):
    p = body.model_dump()
    if core.needs_company_info(p["work_email"], p["website"]):
        raise HTTPException(422, "Free email domain: send company_name and website.")
    tid = core.start_onboarding(p)
    t = core.get_tenant(tid)
    if t["status"] == "ready":   # same-domain user: reuse the existing brain
        return {"tenant_id": tid, "status": "ready", "welcome_message": t["welcome"]}
    with core.db() as c:         # new, failed (resumes from saved brain) or interrupted
        c.execute("UPDATE tenants SET status='researching', error=NULL WHERE id=?", (tid,))
    bg.add_task(core.run_onboarding, tid, p)
    return {"tenant_id": tid, "status": "researching", "poll": f"/tenants/{tid}"}


@app.get("/tenants/{tid}")
def tenant(tid: str):
    t = core.get_tenant(tid)
    if not t:
        raise HTTPException(404, "Unknown tenant")
    if t["status"] == "ready" and not scheduler.get_job(f"research:{tid}"):
        schedule_tenant(t)
    return {k: t[k] for k in ("id", "status", "error", "brain", "playbooks", "welcome", "language")}


class ConfirmIn(BaseModel):
    corrections: str


@app.post("/tenants/{tid}/confirm")
def confirm(tid: str, body: ConfirmIn, bg: BackgroundTasks):
    t = core.get_tenant(tid)
    if not t:
        raise HTTPException(404, "Unknown tenant")
    if t["status"] != "ready":
        raise HTTPException(409, f"Tenant is {t['status']}, not ready to refine")
    with core.db() as c:             # clear the previous result before refining
        c.execute("UPDATE tenants SET error=NULL WHERE id=?", (tid,))
    bg.add_task(core.refine_brain, tid, body.corrections)
    return {"status": "refining"}


class LanguageIn(BaseModel):
    language: str


@app.post("/tenants/{tid}/language")
def change_language(tid: str, body: LanguageIn, bg: BackgroundTasks):
    """Switch every agent to one language: the playbooks and welcome are rebuilt in it (slow job)."""
    t = core.get_tenant(tid)
    if not t:
        raise HTTPException(404, "Tenant not found")
    lang = next((x for x in core.LANGUAGES if x.lower() == body.language.strip().lower()), None)
    if not lang:
        raise HTTPException(400, f"Language must be one of {', '.join(core.LANGUAGES)}")
    if t["status"] != "ready":
        raise HTTPException(409, f"Tenant is {t['status']}, not ready")
    with core.db() as c:                 # clear the previous result before rebuilding
        c.execute("UPDATE tenants SET error=NULL WHERE id=?", (tid,))
    bg.add_task(core.change_language, tid, lang)
    return {"status": "changing", "language": lang}


class RunIn(BaseModel):
    instruction: str = ""


@app.post("/tenants/{tid}/agents/{agent}/run")
def run_agent(tid: str, agent: str, body: RunIn = RunIn()):
    t = core.get_tenant(tid)
    if not t or t["status"] != "ready":
        raise HTTPException(409, "Tenant not ready")
    if agent not in core.AGENTS:
        raise HTTPException(404, f"Agent must be one of {core.AGENTS}")
    if agent == "content":
        raise HTTPException(400, f"The content agent runs from POST /tenants/{tid}/command")
    try:
        return {"agent": agent, "output": core.run_agent(tid, agent, body.instruction)}
    except core.LLMError as e:
        raise HTTPException(502, str(e)) from e


@app.get("/tenants/{tid}/digest")
def digest(tid: str, limit: int = 30):
    with core.db() as c:
        rows = c.execute("SELECT payload, suggested_on, status FROM suggestions WHERE tenant_id=? "
                         "ORDER BY id DESC LIMIT ?", (tid, limit)).fetchall()
    return [{"suggested_on": r["suggested_on"], "status": r["status"], **__import__("json").loads(r["payload"])}
            for r in rows]


@app.get("/tenants/{tid}/outputs")
def outputs(tid: str, agent: str | None = None):
    if not core.get_tenant(tid):
        raise HTTPException(404, "Tenant not found")
    q, a = "SELECT id,agent,content,created_at,status,note,decided_at FROM outputs WHERE tenant_id=?", [tid]
    if agent:
        agent = agent.lower()
        if agent not in core.AGENTS:
            raise HTTPException(400, f"Agent must be one of {core.AGENTS}")
        q, a = q + " AND agent=?", a + [agent]
    with core.db() as c:
        return [dict(r) for r in c.execute(q + " ORDER BY id DESC", a).fetchall()]


class CommandIn(BaseModel):
    text: str


@app.post("/tenants/{tid}/command")
def command(tid: str, body: CommandIn):
    """Plain-English request -> one campaign content package, stored as a draft."""
    if not core.get_tenant(tid):
        raise HTTPException(404, "Tenant not found")
    if not body.text.strip():
        raise HTTPException(400, "Type a request, for example: LinkedIn and YouTube content for our launch next week")
    try:
        return content.run_command(tid, body.text.strip())
    except content.UnsupportedRequest as e:
        raise HTTPException(400, str(e)) from e
    except core.LLMError as e:
        raise HTTPException(502, str(e)) from e


class DecisionIn(BaseModel):
    status: str
    note: str = ""


@app.patch("/tenants/{tid}/outputs/{output_id}")
def decide(tid: str, output_id: int, body: DecisionIn):
    """Approve or reject a draft. The note is stored so later agents can learn from it."""
    if not core.get_tenant(tid):
        raise HTTPException(404, "Tenant not found")
    status = body.status.strip().lower()
    if status not in ("approved", "rejected"):
        raise HTTPException(400, 'status must be "approved" or "rejected"')
    with core.db() as c:
        n = c.execute("UPDATE outputs SET status=?, note=?, decided_at=? WHERE id=? AND tenant_id=?",
                      (status, body.note.strip(), core.dt.datetime.utcnow().isoformat(), output_id, tid)).rowcount
    if not n:
        raise HTTPException(404, "Output not found")
    return {"id": output_id, "status": status, "note": body.note.strip()}


# Protected product rules. "Teach your team" (refine_brain) only rewrites the brief, never these.
@app.get("/tenants/{tid}/offerings")
def list_offerings(tid: str):
    if not core.get_tenant(tid):
        raise HTTPException(404, "Tenant not found")
    return offerings.all_rules(tid)


@app.post("/tenants/{tid}/offerings/preview")
def preview_offering(tid: str, rule: offerings.OfferingRule):
    """Show the diff a save would make. Pass its base_version to the save."""
    if not core.get_tenant(tid):
        raise HTTPException(404, "Tenant not found")
    return offerings.preview(tid, rule)


class OfferingSaveIn(BaseModel):
    rule: offerings.OfferingRule
    base_version: str | None = None


@app.put("/tenants/{tid}/offerings")
def save_offering(tid: str, body: OfferingSaveIn):
    if not core.get_tenant(tid):
        raise HTTPException(404, "Tenant not found")
    try:
        return offerings.save(tid, body.rule, body.base_version)
    except offerings.StaleEdit:
        raise HTTPException(409, f"The rules for {body.rule.name} changed since the diff was shown. "
                                 "Preview again before saving.") from None
