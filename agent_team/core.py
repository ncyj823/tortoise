import os, re, json, time, logging, sqlite3, datetime as dt
from zoneinfo import ZoneInfo
from contextlib import contextmanager

import anthropic
import httpx
from dotenv import load_dotenv

from .prompts import (render, BRAIN_PROMPT, PLAYBOOK_PROMPT, REFINE_PROMPT,
                      AGENT_SYSTEM, DEFAULT_TASKS, RESEARCH_TASK)

from .prompts import GUARDRAILS
load_dotenv()
log = logging.getLogger("agent_team")
MODEL = os.getenv("MODEL", "claude-sonnet-5-5")
DB_PATH = os.getenv("DB_PATH", "agent_team.db")

FREE_DOMAINS = {"gmail.com", "googlemail.com", "yahoo.com", "yahoo.in", "outlook.com", "hotmail.com",
                "live.com", "icloud.com", "proton.me", "protonmail.com", "rediffmail.com", "aol.com"}
AGENTS = ("strategy", "research", "blog", "social", "analytics", "content")
PLAYBOOK_AGENTS = AGENTS[:5]   # "content" runs from POST /command and has no onboarding playbook
RULES_SOURCE = "Company brief (provided by Varun)"   # source label for product facts the user supplied
LANGUAGES = ("English", "Hinglish", "Hindi")
_LANGUAGE_RULES = {
    "English": "Write every word in English. No Hindi or Hinglish words and no Devanagari script, "
               "not even in headings, examples, greetings or quotes.",
    "Hinglish": "Write in Hinglish: everyday Hindi and English mixed, in Latin script only. Never use Devanagari script.",
    "Hindi": "Write in Hindi, in Devanagari script. Keep brand names, product names and technical terms as they are.",
}


def language_rule(lang: str) -> str:
    """One language instruction for every prompt. Inputs written in another language (an old playbook, a source)
    must be translated, never imitated: that is how mixed-language output happened."""
    return ("LANGUAGE: " + _LANGUAGE_RULES.get(lang, f"Write every word in {lang}.")
            + f" If anything you are given (brief, playbook, sources) is in another language, write about it in {lang}; "
              "never copy its language.")


_DEVANAGARI = re.compile(r"[\u0900-\u097F]")
_HINGLISH = re.compile(r"\b(hai|hain|aur|karo|karein|karna|karta|karte|kariye|nahi|nahin|kya|apna|apne|aapka|aapke|"
                       r"aapki|yeh|woh|mein|bhi|toh|abhi|liye|jaise|kaise|taaki|humne|hamare|sab|ko|ki|ka|ke)\b", re.I)


def language_issue(text: str, lang: str) -> str | None:
    """None if `text` is in `lang`, else a short reason. Placeholders like [CTA] are ignored."""
    text = re.sub(r"\[[^\]]*\]", " ", text or "")
    letters = re.findall(r"[^\W\d_]", text)
    if not letters:
        return None
    dev = len(_DEVANAGARI.findall(text))
    if lang == "Hindi":
        return "it is mostly not in Hindi (Devanagari) script" if dev < 0.5 * len(letters) else None
    if dev:
        return "it contains Hindi (Devanagari) script"
    words = re.findall(r"[A-Za-z]+", text)
    hits = _HINGLISH.findall(text)
    if lang == "English" and len(hits) >= 2 and len(hits) >= 0.03 * len(words):
        return "it contains Hinglish words (" + ", ".join(dict.fromkeys(h.lower() for h in hits[:5])) + ")"
    return None
_client = None


# ---------------------------------------------------------------- LLM
PROVIDER = os.getenv("LLM_PROVIDER", "openai").lower()   # "anthropic" | anything else = OpenAI-compatible
MAX_CTX = 6000   # chars of web results fed to non-Anthropic models
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "180") or 180)


class LLMError(RuntimeError):
    """A failure we can explain to the user (bad config, rate limit, bad reply)."""


class LLMOutputError(LLMError, ValueError):
    """The model replied with something we cannot use (no JSON, wrong shape)."""


class LLMTruncated(LLMError):
    """The reply hit max_tokens (finish_reason=length), even after one retry."""


# One line per LLM call. Own handler: uvicorn's default config drops this package's INFO logs.
llm_log = logging.getLogger("agent_team.llm")
if not llm_log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(levelname)s:     %(message)s"))
    llm_log.addHandler(_h)
    llm_log.setLevel(logging.INFO)
    llm_log.propagate = False


def _log_call(label: str, model: str, r, secs: float, note: str = ""):
    usage, finish = {}, None
    try:
        d = r.json()
        usage = d.get("usage") or {}
        finish = ((d.get("choices") or [{}])[0]).get("finish_reason")
    except Exception:
        pass
    llm_log.info("LLM call part=%s model=%s status=%s prompt_tokens=%s completion_tokens=%s reasoning_tokens=%s "
                 "finish_reason=%s duration=%.1fs%s", label or "-", model, getattr(r, "status_code", "error"),
                 usage.get("prompt_tokens"), usage.get("completion_tokens"),
                 (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"), finish, secs,
                 f" ({note})" if note else "")

def _is_quota_or_server_error(msg: str) -> bool:
    m = (msg or "").lower()
    return any(k in m for k in ("429", "rate limit", "quota", "insufficient", "exhausted",
                                "tokens per minute", "tpm", "requests per minute", "rpm",
                                "503", "502", "500", "504", "overloaded", "capacity",
                                "rate_limit_exceeded"))



def client():
    global _client
    if _client is None:
        if not (os.getenv("ANTHROPIC_API_KEY") or "").strip():
            raise LLMError("ANTHROPIC_API_KEY is not set in .env")
        _client = anthropic.Anthropic()
    return _client


# ---- free web search (DuckDuckGo, no key) with optional Tavily
_search_warned = False


def web_search(query: str, n: int = 6) -> list[dict]:
    """Best-effort search. Never raises: an outage must not break an agent run."""
    global _search_warned
    key = (os.getenv("TAVILY_API_KEY") or "").strip()
    if key:
        try:
            r = httpx.post("https://api.tavily.com/search", timeout=30,
                           json={"api_key": key, "query": query, "max_results": n})
            r.raise_for_status()
            return [{"title": x.get("title", ""), "url": x.get("url", ""), "body": x.get("content", "")}
                    for x in r.json().get("results", []) if x.get("url")]
        except Exception as e:                       # fall through to DuckDuckGo
            log.warning("Tavily search failed for %r: %s", query, e)

    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS

    last = "no attempts"
    for attempt in range(3):                         # DDG rate-limits in bursts; retry briefly
        try:
            rows = list(DDGS().text(query, max_results=n))
            if rows:
                return [{"title": x.get("title", ""), "url": x.get("href", ""), "body": x.get("body", "")}
                        for x in rows if x.get("href")]
            last = "0 results"
        except Exception as e:
            last = e
        time.sleep(1.5 * (attempt + 1))
    if not _search_warned:
        _search_warned = True
        log.warning("Web search is unavailable (%s). Results will be empty and facts will be marked "
                    "'unknown'. Set TAVILY_API_KEY (free tier) in .env for reliable search.", last)
    return []


def gather(queries: list[str]) -> str:
    seen, out = set(), []
    for q in queries:
        for r in web_search(q):
            if r["url"] and r["url"] not in seen:
                seen.add(r["url"])
                out.append(f"[{len(out)+1}] {r['title']}\n{r['url']}\n{r['body'][:500]}")
    return "\n\n".join(out)[:MAX_CTX] or "(no web results found)"


def fetch_page(url: str, limit: int = 5000) -> str:
    try:
        h = httpx.get(url, timeout=15, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}).text
        h = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", h)
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h)).strip()[:limit]
    except Exception:
        return "(could not fetch website)"


def _chat(system: str, user: str, max_tokens: int, options: dict | None = None, label: str = "",
          model: str | None = None) -> str:
    """OpenAI-compatible chat completion with fallback + truncation handling.
    `options` are extra request fields for this call only (e.g. reasoning_effort); `label` names it in the log;
    `model` overrides LLM_MODEL for this call only."""
    base = (os.getenv("LLM_BASE_URL") or "").strip().rstrip("/")
    key = (os.getenv("LLM_API_KEY") or "").strip()
    model = (model or os.getenv("LLM_MODEL") or "").strip()
    if not base:
        raise LLMError("LLM_BASE_URL is empty in .env")
    if not model:
        raise LLMError("LLM_MODEL is empty in .env")
    if not key:
        raise LLMError("LLM_API_KEY is empty in .env")

    fb_base = (os.getenv("LLM_FALLBACK_BASE_URL") or "").strip().rstrip("/")
    fb_key = (os.getenv("LLM_FALLBACK_API_KEY") or "").strip()
    fb_model = (os.getenv("LLM_FALLBACK_MODEL") or "").strip()
    has_fb = bool(fb_base and fb_key and fb_model)

    url = base if base.endswith("/chat/completions") else base + "/chat/completions"
    mt = max_tokens
    truncated_retry = False

    def _post(b, k, m, u, tokens, sys_msg=None, usr_msg=None, note=""):
        s_msg = sys_msg if sys_msg is not None else system
        u_msg = usr_msg if usr_msg is not None else user
        t0 = time.monotonic()
        try:
            r = httpx.post(u, timeout=LLM_TIMEOUT,
                           headers={"Authorization": f"Bearer {k}"},
                           json={"model": m, "max_tokens": tokens, "temperature": 0.4,
                                 "messages": [{"role": "system", "content": s_msg},
                                              {"role": "user", "content": u_msg}],
                                 **(options or {})})
        except httpx.HTTPError as e:
            _log_call(label, m, None, time.monotonic() - t0, f"{note} {type(e).__name__}".strip())
            raise
        _log_call(label, m, r, time.monotonic() - t0, note)
        return r

    def _try_fallback(tokens, sys_msg=None, usr_msg=None):
        if not has_fb:
            return None
        try:
            fb_url = fb_base if fb_base.endswith("/chat/completions") else fb_base + "/chat/completions"
            r2 = _post(fb_base, fb_key, fb_model, fb_url, tokens, sys_msg, usr_msg)
            if r2.status_code < 400:
                data = r2.json()
                choice = (data.get("choices") or [])[0]
                content = (choice.get("message") or {}).get("content") or ""
                if content.strip():
                    log.info("LLM fallback used: %s", fb_model)
                    return content
            return None
        except Exception:
            return None

    for attempt in range(6):
        try:
            r = _post(base, key, model, url, mt)
        except httpx.TimeoutException:
            if attempt < 5:
                time.sleep(3 * (attempt + 1))
                continue
            fb = _try_fallback(mt)
            if fb is not None:
                return fb
            raise LLMError(f"The LLM provider did not answer within {LLM_TIMEOUT:.0f}s.") from None
        except httpx.HTTPError as e:
            if attempt < 5:
                time.sleep(3 * (attempt + 1))
                continue
            if _is_quota_or_server_error(str(e)):
                fb = _try_fallback(mt)
                if fb is not None:
                    return fb
            raise LLMError(f"Could not reach the LLM provider: {e}") from None

        body = r.text[:500]
        if r.status_code == 429:
            retry_header = r.headers.get("retry-after")
            sleep_time = float(retry_header) if retry_header and retry_header.replace(".", "", 1).isdigit() else 10 * (attempt + 1)
            m = re.search(r"try again in (\d+\.?\d*)s", body, re.I)
            if m:
                sleep_time = max(sleep_time, float(m.group(1)) + 1.0)
            if attempt < 5:
                time.sleep(sleep_time)
                continue
            if _is_quota_or_server_error(body):
                fb = _try_fallback(mt)
                if fb is not None:
                    return fb
            raise LLMError("The LLM provider is rate-limiting this key (429). Free tiers have per-minute limits - wait a minute and try again.")
        if r.status_code in (500, 502, 503, 504):
            if attempt < 5:
                time.sleep(5 * (attempt + 1))
                continue
            fb = _try_fallback(mt)
            if fb is not None:
                return fb
            raise LLMError(f"LLM provider error ({r.status_code}). Try again in a moment.")
        if r.status_code == 400 and "max_tokens" in body.lower() and mt > 1024:
            mt = max(1024, mt // 2)
            log.warning("Provider rejected max_tokens=%s, retrying with %s", max_tokens, mt)
            continue
        if r.status_code >= 400:
            if _is_quota_or_server_error(body):
                fb = _try_fallback(mt)
                if fb is not None:
                    return fb
            raise LLMError(f"LLM request failed ({r.status_code}): {body.strip() or r.reason_phrase}")
        try:
            data = r.json()
            choice = (data.get("choices") or [])[0]
            content = (choice.get("message") or {}).get("content") or ""
            finish = choice.get("finish_reason")
        except Exception:
            fb = _try_fallback(mt)
            if fb is not None:
                return fb
            raise LLMError("Unexpected reply from the LLM provider") from None
        if not content.strip() and finish != "length":   # empty because cut off -> truncation path below
            raise LLMError("The model returned an empty reply. Try again.")
        if finish == "length" and not truncated_retry:
            truncated_retry = True
            log.warning("Reply truncated (finish_reason=length); retrying concisely with the same max_tokens")
            sys2 = system + " Be extremely concise. Avoid long descriptions."
            usr2 = user + "\n\nBE CONCISE. Return valid JSON only if asked. Be brief. Max 6 steps per list."
            try:
                r2 = _post(base, key, model, url, mt, sys2, usr2, note="truncation retry")   # never fewer tokens
                if r2.status_code < 400:
                    data2 = r2.json()
                    choice2 = (data2.get("choices") or [])[0]
                    c2 = (choice2.get("message") or {}).get("content") or ""
                    if c2.strip() and choice2.get("finish_reason") != "length":
                        log.info("LLM answered via primary (concise retry): %s", model)
                        return c2
            except Exception:
                pass
            fb = _try_fallback(mt, sys2, usr2)
            if fb is not None:
                return fb
        if finish == "length":
            raise LLMTruncated("truncated reply (finish_reason=length); response cut off")
        log.info("LLM answered via primary: %s", model)
        return content
    raise LLMError("LLM request failed after several retries.")





def ask(system: str, user: str, search: bool = False, max_tokens: int | None = None,
        seed: list[str] | None = None, extra: str = "", label: str = "", llm_options: dict | None = None,
        provider: str | None = None, model: str | None = None) -> str:
    """One LLM call. `seed` = search queries to run; `extra` = extra context (e.g. website text).
    `label` names the call in the log; `llm_options` are extra request fields for this call only
    (OpenAI-compatible providers; ignored on the Anthropic path). `provider` overrides LLM_PROVIDER and `model`
    overrides LLM_MODEL for this call (the model only on OpenAI-compatible providers)."""
    if (provider or PROVIDER).lower() != "anthropic":
        mt = max_tokens or int(os.getenv("LLM_MAX_TOKENS", "8000"))
        if search:
            queries = [q for q in (seed or []) if isinstance(q, str) and q.strip()]
            if not queries:   # let the model plan its own searches (one cheap call)
                try:
                    planned = extract_json(_chat("Output JSON only.",
                        "Task:\n" + user[:800] + "\n\nWrite 5 short web search queries that would best help. "
                        "Return ONLY a JSON array of strings.", 400))
                    queries = [q for q in planned if isinstance(q, str)][:5] if isinstance(planned, list) else []
                except Exception:
                    queries = []
                if not queries:
                    queries = [user[:80]]
            user += ("\n\nWEB SEARCH RESULTS (cite these URLs as sources; mark anything not supported here as unknown):\n"
                     + gather(queries) + (("\n\n" + extra) if extra else ""))
        return _chat(system, user, mt, llm_options, label, model)

    # Anthropic path (native web search tool)
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 15}] if search else []
    messages = [{"role": "user", "content": user + (("\n\n" + extra) if extra else "")}]
    text = ""
    for _ in range(6):
        kwargs = dict(model=MODEL, max_tokens=max_tokens or 16000, system=system, messages=messages)
        if tools:
            kwargs["tools"] = tools
        t0 = time.monotonic()
        try:
            with client().messages.stream(**kwargs) as st:
                msg = st.get_final_message()
        except anthropic.APIError as e:
            llm_log.info("LLM call part=%s model=%s error=%s duration=%.1fs", label or "-", MODEL,
                         type(e).__name__, time.monotonic() - t0)
            raise LLMError(f"Anthropic request failed: {e}") from None
        llm_log.info("LLM call part=%s model=%s prompt_tokens=%s completion_tokens=%s finish_reason=%s "
                     "duration=%.1fs", label or "-", MODEL, msg.usage.input_tokens, msg.usage.output_tokens,
                     msg.stop_reason, time.monotonic() - t0)
        text += "".join(b.text for b in msg.content if b.type == "text")
        if msg.stop_reason == "pause_turn":
            messages = [messages[0], {"role": "assistant", "content": msg.content}]
            continue
        break
    return text


def extract_json(text: str):
    """Pick the largest valid JSON value in the reply.

    Free models wrap JSON in prose or code fences, so the answer is not
    necessarily the first brace in the text.
    """
    text = re.sub(r"```(?:json)?", "", text)
    dec, best, size = json.JSONDecoder(), None, -1
    for i, ch in enumerate(text):
        if ch not in "{[":
            continue
        try:
            obj, end = dec.raw_decode(text[i:])
        except json.JSONDecodeError:
            # If it starts with { but outer JSON is invalid (likely truncated), fail
            if ch == "{":
                raise LLMOutputError("Truncated or invalid JSON (outer object incomplete)")
            continue
        if i + end >= size:       # furthest complete value wins; an outer object beats its own fields
            best, size = obj, i + end
    if best is None:
        raise LLMOutputError("No JSON found in model output")
    return best


def ask_json(system: str, user: str, **kw):
    text = ask(system, user, **kw)
    try:
        return extract_json(text)
    except LLMOutputError:
        retry = ("\n\nYour previous reply was not valid JSON. Reply with ONLY one valid JSON value, "
                 "no prose and no code fences.")
        return extract_json(ask(system, user + retry, **kw))


# ---------------------------------------------------------------- DB
@contextmanager
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS tenants(
          id TEXT PRIMARY KEY, work_email TEXT, user_name TEXT, user_role TEXT,
          timezone TEXT, language TEXT, website TEXT, status TEXT, error TEXT,
          brain TEXT, playbooks TEXT, welcome TEXT, created_at TEXT);
        CREATE TABLE IF NOT EXISTS suggestions(
          id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT, name TEXT, domain TEXT,
          payload TEXT, suggested_on TEXT, status TEXT DEFAULT 'new');
        CREATE TABLE IF NOT EXISTS outputs(
          id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT, agent TEXT,
          content TEXT, created_at TEXT, status TEXT DEFAULT 'pending_approval');
        CREATE TABLE IF NOT EXISTS offering_rules(
          tenant_id TEXT, name TEXT, rule TEXT, updated_at TEXT, PRIMARY KEY(tenant_id, name));
        """)
        cols = {r["name"] for r in c.execute("PRAGMA table_info(outputs)")}
        for col in ("note", "decided_at"):          # approve/reject decisions (added later)
            if col not in cols:
                c.execute(f"ALTER TABLE outputs ADD COLUMN {col} TEXT")


def get_tenant(tid: str):
    with db() as c:
        r = c.execute("SELECT * FROM tenants WHERE id=?", (tid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    for k in ("brain", "playbooks"):
        d[k] = json.loads(d[k]) if d[k] else None
    return d


def all_tenants():
    with db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM tenants WHERE status='ready'")]


# ---------------------------------------------------------------- Onboarding
def domain_of(email: str) -> str:
    if "@" not in email:
        raise ValueError("Invalid email")
    return email.split("@", 1)[1].strip().lower()


def needs_company_info(email: str, website: str | None) -> bool:
    return domain_of(email) in FREE_DOMAINS and not website


def start_onboarding(p: dict) -> str:
    """Create the tenant row (status=researching). Returns tenant id."""
    tid = domain_of(p["work_email"]) if not p.get("website") else re.sub(r"^https?://|/.*$", "", p["website"]).lower()
    with db() as c:
        c.execute("""INSERT OR IGNORE INTO tenants(id,work_email,user_name,user_role,timezone,language,
                     website,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                  (tid, p["work_email"], p.get("user_name"), p.get("user_role"),
                   p.get("timezone") or "Asia/Kolkata", p.get("language") or "English",
                   p.get("website") or f"https://{tid}", "researching",
                   dt.datetime.now(dt.timezone.utc).isoformat()))
    return tid


BRAIN_SECTIONS = ("company", "offerings", "icp", "positioning", "brand_voice", "competitors",
                  "social_presence", "content_landscape", "pr_landscape", "goals_inferred",
                  "open_questions", "sources")


def _check_brain(brain):
    """Free models drop keys or answer with prose. Fail loudly so we can retry."""
    if not isinstance(brain, dict) or not isinstance(brain.get("company"), dict):
        raise LLMOutputError("Company Brain is not a JSON object with a 'company' section")
    missing = [k for k in BRAIN_SECTIONS if k not in brain]
    if missing:
        raise LLMOutputError("Company Brain is missing sections: " + ", ".join(missing))
    if not str(brain["company"].get("name") or "").strip():
        raise LLMOutputError("Company Brain has no company.name")
    if not isinstance(brain.get("sources"), list):
        raise LLMOutputError("Company Brain 'sources' is not a list")
    return brain


def _check_playbooks(out):
    if not isinstance(out, dict) or not isinstance(out.get("playbooks"), dict):
        raise LLMOutputError("Playbook reply is not a JSON object with a 'playbooks' section")
    pb = out["playbooks"]
    missing = [a for a in PLAYBOOK_AGENTS if a not in pb]
    if missing:
        raise LLMOutputError("Playbook reply is missing agents: " + ", ".join(missing))
    welcome = out.get("welcome_message")
    if not isinstance(welcome, str) or not welcome.strip():
        raise LLMOutputError("Playbook reply has no welcome_message")
    return {a: pb[a] for a in PLAYBOOK_AGENTS}, welcome      # drop stray keys, keep exactly the 5 agents


def ask_json_checked(system: str, user: str, check, **kw):
    """ask_json + shape validation, with one corrective retry."""
    try:
        return check(ask_json(system, user, **kw))
    except LLMOutputError as e:
        log.warning("Model output rejected (%s); retrying once", e)
        return check(ask_json(
            system,
            user + f"\n\nYour previous reply was rejected: {e}. Return the COMPLETE schema, "
                   "every key present, JSON only.",
            **kw))


def _compact_brain_for_playbooks(brain: dict) -> dict:
    """Send a compact brain to playbook calls to save tokens."""
    if not isinstance(brain, dict):
        return brain
    compact = {}
    for k in ("company", "offerings", "icp", "positioning", "brand_voice",
              "competitors", "social_presence", "content_landscape",
              "pr_landscape", "goals_inferred", "open_questions"):
        if k in brain:
            compact[k] = brain[k]
    # keep sources minimal if present
    return compact


def _build_playbooks(tid: str, brain: dict, timezone: str, language: str):
    """One small call per agent (+1 for the welcome message) so replies never get truncated."""
    brain_str = json.dumps(_compact_brain_for_playbooks(brain), ensure_ascii=False)
    sys_p = f"You design agent playbooks. Output JSON only. {language_rule(language)} " + GUARDRAILS
    playbooks = {}
    for agent in PLAYBOOK_AGENTS:
        prompt = (f"Create the playbook for ONLY this agent: {agent}.\n"
                  f"Company brain (compact):\n{brain_str}\n"
                  f"Timezone: {timezone}. Language: {language}.\n"
                  f"All text fields (mission, steps, tools, output_format, quality_checklist, never_do, kpis) MUST be in {language}.\n"
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
        time.sleep(1.0)  # Gentle pacing between playbook calls to respect provider RPM/TPM limits
    wprompt = (f"Write a short, friendly welcome_message in {language} for this company.\n"
               f"Company brain (compact):\n{brain_str}\n"
               "Include: 1) what you learned about the company (max 5 bullets) 2) one line per agent "
               "on the plan 3) max 5 confirm/correct questions from open_questions 4) what happens next. "
               'Return JSON: {"welcome_message": "..."}')
    try:
        w = ask_json(f"Write a short welcome message. Output JSON only. {language_rule(language)} " + GUARDRAILS,
                     wprompt, max_tokens=1500)
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


def refine_brain(tid: str, corrections: str):
    """Slow job. Same rule as onboarding: a failure is recorded on the tenant
    instead of disappearing into a background task."""
    try:
        t = get_tenant(tid)
        if not t:
            return
        brain = ask_json_checked(
            "You edit JSON precisely. Output JSON only.",
            render(REFINE_PROMPT, {"brain": json.dumps(t["brain"], ensure_ascii=False),
                                   "corrections": corrections}),
            _check_brain)
        playbooks, welcome = _build_playbooks(tid, brain, t["timezone"], t["language"])
        with db() as c:
            c.execute("UPDATE tenants SET brain=?,playbooks=?,welcome=?,error=NULL WHERE id=?",
                      (json.dumps(brain, ensure_ascii=False), json.dumps(playbooks, ensure_ascii=False),
                       welcome, tid))
    except Exception as e:
        log.exception("Refine failed for %s", tid)
        with db() as c:
            c.execute("UPDATE tenants SET error=? WHERE id=?", (str(e), tid))


# ---------------------------------------------------------------- Runtime agents
def _system_for(t: dict, agent: str) -> str:
    today = dt.datetime.now(ZoneInfo(t["timezone"])).strftime("%A, %d %B %Y")
    lang = t.get("language") or "English"
    base = render(AGENT_SYSTEM, {"agent": agent, "today": today,
                                 "brain": json.dumps(t["brain"], ensure_ascii=False),
                                 "playbook": json.dumps(t["playbooks"][agent], ensure_ascii=False)})
    return base + f"\n\n{language_rule(lang)}\n"


def _host(url: str) -> str:
    return re.sub(r"^https?://(www\.)?|[/?#].*$", "", (url or "").lower())


def run_research(tid: str) -> list[dict]:
    t = get_tenant(tid)
    with db() as c:
        seen_rows = c.execute("SELECT name, domain FROM suggestions WHERE tenant_id=?", (tid,)).fetchall()
    seen_names = {(r["name"] or "").lower() for r in seen_rows}
    seen_domains = {r["domain"] for r in seen_rows if r["domain"]}
    prompt = render(RESEARCH_TASK, {"seen": ", ".join(sorted(n for n in seen_names if n)) or "none yet"})
    items = ask_json(_system_for(t, "research"), prompt, search=True)
    if isinstance(items, dict):   # some models wrap the array in an object
        items = next((items[k] for k in ("companies", "results", "items", "data")
                      if isinstance(items.get(k), list)), None)
    if not isinstance(items, list):
        raise LLMOutputError(f"Expected a JSON array of companies, got {type(items).__name__}")

    fresh, today = [], dt.date.today().isoformat()
    with db() as c:
        for it in items:
            if not isinstance(it, dict):
                continue
            name = str(it.get("name") or "").strip()
            if not name or name.lower() in seen_names:
                continue
            url = str(it.get("url") or "").strip()
            dom = _host(url)
            if dom and dom in seen_domains:
                continue          # dedupe against history and earlier rows in this batch
            try:
                score = int(float(it.get("relevance_score") or 0))
            except (TypeError, ValueError):
                score = 0
            row = {"name": name, "url": url,
                   "why_relevant": str(it.get("why_relevant") or "unknown"),
                   "outreach_angle": str(it.get("outreach_angle") or "unknown"),
                   "relevance_score": max(0, min(10, score)),
                   "source": str(it.get("source") or url or "unknown")}
            c.execute("INSERT INTO suggestions(tenant_id,name,domain,payload,suggested_on) VALUES(?,?,?,?,?)",
                      (tid, name, dom, json.dumps(row, ensure_ascii=False), today))
            seen_names.add(name.lower())
            if dom:
                seen_domains.add(dom)
            fresh.append(row)
    if not fresh:
        log.warning("Research for %s returned no new companies", tid)
    try:
        send_telegram(format_digest(t, fresh))
    except Exception as e:            # a Telegram outage must not fail the agent run
        log.warning("Telegram digest failed: %s", e)
    return fresh


def run_agent(tid: str, agent: str, instruction: str = "") -> str:
    if agent == "research":
        return json.dumps(run_research(tid), ensure_ascii=False, indent=2)
    t = get_tenant(tid)
    lang = t.get("language") or "English"
    task = instruction or DEFAULT_TASKS[agent]
    task = f"{language_rule(lang)}\n\n{task}"
    text = ask(_system_for(t, agent), task, search=(agent != "social"))
    issue = language_issue(text, lang)
    if issue:                                   # one rewrite in the right language, then flag what is left
        log.warning("%s output for %s was not in %s (%s); rewriting once", agent, tid, lang, issue)
        text = ask(_system_for(t, agent), f"{task}\n\nYour previous draft was not in {lang}: {issue}. "
                                          f"Write the whole draft again in {lang} only.", search=False)
        issue = language_issue(text, lang)
        if issue:
            text = f"**Language check: this draft is meant to be in {lang}, but {issue}. Review before use.**\n\n{text}"
    with db() as c:
        c.execute("INSERT INTO outputs(tenant_id,agent,content,created_at) VALUES(?,?,?,?)",
                  (tid, agent, text, dt.datetime.now(dt.timezone.utc).isoformat()))
    return text


def change_language(tid: str, language: str):
    """Switch a tenant's language and rebuild everything written in the old one (playbooks, welcome).
    The new language is saved only together with the rebuilt playbooks, so they cannot drift apart again."""
    try:
        t = get_tenant(tid)
        if not t:
            return
        playbooks, welcome = _build_playbooks(tid, t["brain"], t["timezone"], language)
        with db() as c:
            c.execute("UPDATE tenants SET language=?, playbooks=?, welcome=?, error=NULL WHERE id=?",
                      (language, json.dumps(playbooks, ensure_ascii=False), welcome, tid))
    except Exception as e:
        log.exception("Language change failed for %s", tid)
        with db() as c:
            c.execute("UPDATE tenants SET error=? WHERE id=?", (f"Language change to {language} failed: {e}", tid))


def format_digest(t: dict, items: list[dict]) -> str:
    name = (t.get("brain") or {}).get("company", {}).get("name", t["id"])
    lines = [f"Daily research for {name}: {len(items)} new companies"]
    for i, it in enumerate(items, 1):
        lines.append(f"{i}. {it['name']} ({it.get('relevance_score','?')}/10) {it.get('url','')}\n   {it.get('why_relevant','')}\n   Angle: {it.get('outreach_angle','')}")
    return "\n".join(lines)


def send_telegram(text: str):
    tok, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (tok and chat):
        return
    for i in range(0, len(text), 3900):
        httpx.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                   json={"chat_id": chat, "text": text[i:i + 3900]}, timeout=20)
