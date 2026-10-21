"""Content agent: a plain-English request -> one campaign content package (draft only).

Flow: parse the request into an Intent, pull the brief facts (protected product rules + the
researched brief), the latest strategy and a few web results, generate the package in small
LLM calls (the provider has a per-minute token limit), verify every product claim against the
brief, regenerate a part once if it has unsupported claims, check every cited source, clean
up, and store it as one output row. Nothing is published or sent anywhere.
"""
import json, os, re, unicodedata, datetime as dt
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from . import checks, core, offerings
from .prompts import GUARDRAILS

ROUTES = ("content",)
PLATFORMS = ("linkedin", "instagram", "x", "youtube", "facebook", "blog")
_ALIASES = {"twitter": "x", "x (twitter)": "x", "fb": "facebook", "meta": "facebook", "yt": "youtube",
            "ig": "instagram", "article": "blog", "website": "blog", "youtube description": "youtube"}
RATIOS = ("9:16", "1:1", "4:5", "16:9")
POST_LIMITS = {"x": 280, "instagram": 2200, "linkedin": 3000, "youtube": 5000, "facebook": 2000}
POST_STYLE = {
    "linkedin": "LinkedIn post: 120-250 words, short paragraphs, a clear first line, 3-5 hashtags at the end.",
    "instagram": "Instagram caption: first line hooks before the 'more' cut, 60-150 words, 5-10 hashtags.",
    "x": "X post: one post under 280 characters including hashtags, 0-2 hashtags.",
    "youtube": "YouTube description: 2-line summary first, then what the video covers, a link line, 3 hashtags.",
    "facebook": "Facebook ad: primary text under 125 characters for the first line (up to 3 short lines), "
                "plus 'headline' (under 40 characters) and 'description' (under 30 characters) in notes.",
}
LENGTHS = {"hook": (3, 6), "short": (20, 45), "explainer": (90, 150)}
PART_NAMES = {"A": "campaign summary, hook and short video", "B": "explainer video", "C": "posts", "D": "article"}
RESEARCHED_SOURCE = "Company brief (researched from public sources)"
PLACEHOLDERS = "[CTA], [PRICE], [WHERE TO GET IT], [PLATFORMS], [WHAT THE USER SEES], [DATA HANDLING]"
LLM_OPTIONS = {"reasoning_effort": "low"}       # content agent's writer calls; other agents keep the default
CHECKER_PROVIDER = os.getenv("CHECKER_PROVIDER", "anthropic")   # the claim checker; the writer stays on LLM_PROVIDER
FAILED = "failed, regenerate"
SHORTER = ("\n\nYour previous reply was cut off because it was too long. Write a shorter version: fewer scenes "
           "(hook 1-2, short 4-6, explainer 6-8), one sentence per line, at most 5 shot list items, "
           "at most 3 sources and at most 3 open questions.")


class UnsupportedRequest(ValueError):
    """The request is not something the command endpoint can do yet."""


# ---------------------------------------------------------------- schemas
def _platform(p: str) -> str:
    p = str(p or "").strip().lower()
    return _ALIASES.get(p, p)


class Intent(BaseModel):
    route: str
    platforms: list[str] = []
    goal: str = ""
    topic: str = ""
    audience: str = ""
    constraints: list[str] = []
    long_form: bool = False

    @field_validator("route")
    @classmethod
    def _route(cls, v):
        return str(v).strip().lower()

    @field_validator("platforms")
    @classmethod
    def _platforms(cls, v):
        return list(dict.fromkeys(_platform(p) for p in v if str(p).strip()))


class Summary(BaseModel):
    goal: str
    audience: str
    core_message: str
    call_to_action: str


class Scene(BaseModel):
    line: str                 # spoken words / voiceover
    on_screen_text: str = ""
    visual: str               # shot or visual direction


class Video(BaseModel):
    duration_seconds: int
    aspect_ratios: list[str] = Field(min_length=1)
    scenes: list[Scene] = Field(min_length=1)
    caption: str
    shot_list: list[str] = []

    @field_validator("aspect_ratios")
    @classmethod
    def _ratios(cls, v):
        v = [str(r).replace(" ", "") for r in v]
        bad = [r for r in v if r not in RATIOS]
        if bad:
            raise ValueError(f"aspect ratios must be from {RATIOS}, got {bad}")
        return list(dict.fromkeys(v))


class FailedPart(BaseModel):
    """A part that could not be generated. The rest of the package is still saved."""
    status: str = FAILED
    error: str


class Videos(BaseModel):
    hook: Video | FailedPart
    short: Video | FailedPart
    explainer: Video | FailedPart

    @model_validator(mode="after")
    def _lengths(self):
        for name, (lo, hi) in LENGTHS.items():
            v = getattr(self, name)
            if isinstance(v, FailedPart):
                continue
            s = v.duration_seconds
            if not lo <= s <= hi:
                raise ValueError(f"{name} video must be {lo}-{hi} seconds, got {s}")
        return self


class Post(BaseModel):
    platform: str
    text: str
    hashtags: list[str] = []
    notes: str = ""

    @field_validator("platform")
    @classmethod
    def _p(cls, v):
        return _platform(v)

    @model_validator(mode="after")
    def _size(self):
        limit = POST_LIMITS.get(self.platform)
        if limit and len(self.text) > limit:
            raise ValueError(f"{self.platform} post is {len(self.text)} characters; the limit is {limit}")
        return self


class Article(BaseModel):
    title: str
    outline: list[str] = Field(min_length=1)
    draft: str


class RawSource(BaseModel):     # what the model returns
    claim: str
    quote: str = ""
    url: str = ""


class Source(BaseModel):        # what the package keeps, after the quote was found
    claim: str
    quote: str
    source: str                 # a URL, or which brief the quote is from


class UnverifiedClaim(BaseModel):
    part: str
    where: str = ""
    claim: str
    reason: str


class Package(BaseModel):
    unverified_claims: list[UnverifiedClaim] = []   # "Unverified claims, remove before use"
    campaign_summary: Summary | FailedPart
    videos: Videos
    posts: list[Post] | FailedPart
    article: Article | FailedPart | None = None
    sources: list[Source] = []
    open_questions: list[str] = []
    notes: list[str] = []


# Partial replies, one per LLM call
class _SummaryPart(BaseModel):
    campaign_summary: Summary
    hook: Video
    short: Video
    sources: list[RawSource] = []
    open_questions: list[str] = []


class _ExplainerPart(BaseModel):
    explainer: Video
    sources: list[RawSource] = []
    open_questions: list[str] = []


class _PostsPart(BaseModel):
    posts: list[Post]
    sources: list[RawSource] = []
    open_questions: list[str] = []


class _ArticlePart(BaseModel):
    article: Article
    sources: list[RawSource] = []
    open_questions: list[str] = []


class _Label(BaseModel):
    id: int
    c: str                  # PRODUCT or PROBLEM


class _Verify(BaseModel):
    labels: list[_Label] = []


def _checker(model, extra=None):
    """Turn a Pydantic model into an ask_json_checked check (validation error -> one retry)."""
    def check(data):
        try:
            obj = model.model_validate(data)
        except ValidationError as e:
            raise core.LLMOutputError(_short_errors(e)) from None
        if extra:
            extra(obj)
        return obj
    return check


def _short_errors(e: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()[:6])


# ---------------------------------------------------------------- writing rules
_DASH = re.compile(r"[ \t]*[—―][ \t]*|[ \t]+–[ \t]+|[ \t]+--[ \t]+")


def plain(s: str) -> str:
    """Replace em dashes (and spaced en dashes / double hyphens used as dashes) with commas."""
    s = _DASH.sub(", ", s)
    s = re.sub(r"(?m)^, ", "", s)                     # a line that started with a dash
    s = re.sub(r",\s*([,.;:!?)])", r"\1", s)          # ", ." -> "."
    return re.sub(r"(?m),[ \t]*$", "", s)             # a line that ended with a dash


def clean(obj):
    if isinstance(obj, str):
        return plain(obj)
    if isinstance(obj, list):
        return [clean(x) for x in obj]
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    return obj


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, list):
        for x in obj:
            yield from _strings(x)
    elif isinstance(obj, dict):
        for x in obj.values():
            yield from _strings(x)


def _words(s: str) -> str:
    """Lowercase words only, so quotes match across dashes, hyphen variants and punctuation."""
    s = unicodedata.normalize("NFKC", s or "").lower()
    return " ".join(re.findall(r"[a-z0-9]+", s))


def contains(text: str, quote: str, min_words: int = 3) -> bool:
    q = _words(quote)
    return len(q.split()) >= min_words and f" {q} " in f" {_words(text)} "


_FIGURE = re.compile(r"\b\d+(?:[.,]\d+)?\s?(?:%|x\b|times\b|percent\b)", re.I)
_URL = re.compile(r"https?://[^\s)\]\"'>,]+")


def _norm_url(u: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", u.strip().rstrip("/.").lower())


def check_sources(raw: list[RawSource], ctx: dict) -> tuple[list[dict], list[str]]:
    """Keep a source only if its quote is really in the brief, or in the text of the page it cites."""
    allowed = {_norm_url(u) for u in ctx["allowed_urls"]}
    kept, questions, pages = [], [], {}
    for s in raw:
        src = None
        if s.url:
            if _norm_url(s.url) in allowed:
                if s.url not in pages:
                    pages[s.url] = ctx["snippets"].get(s.url, "") + " " + core.fetch_page(s.url, 20000)
                if contains(pages[s.url], s.quote):
                    src = s.url
        else:
            src = next((r.source for r in ctx["rules"] if contains(" ".join(r.facts), s.quote)), None)
            if not src and contains(ctx["researched_facts"], s.quote):
                src = RESEARCHED_SOURCE
        entry = {"claim": s.claim, "quote": s.quote, "source": src}
        if not src:
            questions.append(f'Find a real source for this claim, or remove it: "{s.claim}"')
        elif entry not in kept:
            kept.append(entry)
    return kept, questions


def flag_links_and_figures(pkg: dict, allowed_urls: set[str], corpus: str) -> dict:
    """Links not from a retrieved source and figures not found in the context go to open_questions."""
    allowed = {_norm_url(u) for u in allowed_urls}
    questions = list(pkg.get("open_questions") or [])
    body = {k: v for k, v in pkg.items() if k not in ("unverified_claims", "sources", "open_questions", "notes")}
    low = corpus.lower()
    for text in _strings(body):
        for url in _URL.findall(text):
            if _norm_url(url) not in allowed:
                questions.append(f"Check this link before using it (not from a retrieved source): {url}")
        for fig in _FIGURE.findall(text):
            if fig.lower() not in low:
                questions.append(f"Check this figure before publishing, no source was found for it: \"{fig}\"")
    pkg["open_questions"] = list(dict.fromkeys(questions))
    return pkg


WRITING_RULES = f"""\
WRITING RULES (apply to every text field):
- Simple, plain language that reads as written by a person. Short sentences. No hype, no buzzword stacks.
- Never use em dashes. Use a comma, a period or "and" instead.
- PRODUCT CLAIMS ARE ALLOW-LISTED. Any statement about what an offering does, what it costs, where to get it,
  which platforms or browsers it supports, what the user sees, or how it handles data must come from BRIEF FACTS.
  This covers voiceover, on-screen text, captions, visual directions, shot lists and calls to action.
  If BRIEF FACTS do not cover it, write one of these placeholders exactly as shown, with nothing inside the
  brackets: {PLACEHOLDERS}. Add the question to "open_questions". Never fill the gap with a plausible guess,
  and never write any other bracketed text.
- CLAIM BANK: every sentence or on-screen text that describes a product (what it does, how, how fast, for whom,
  where, what the user sees) must be copied word for word from that product's CLAIM BANK in BRIEF FACTS. Do not
  shorten, extend or paraphrase it. Every other sentence must be about the problem or the audience and must not
  mention the product, by name or as "it". Any visual of the product or its interface is exactly
  [WHAT THE USER SEES].
- Do not offer downloads or deliverables (one-pagers, checklists, whitepapers, guides, trials, ebooks, webinars)
  unless BRIEF FACTS name them. Use [CTA] instead.
- Never use a capability of a different offering to describe the focus product.
- Do not invent statistics, numbers, customer names, logos, testimonials, quotes, awards or competitor claims.
- "sources": one entry per factual claim: {{"claim": "...", "quote": "the exact supporting text, copied word for
  word", "url": ""}}. Leave url empty when the quote comes from BRIEF FACTS. Use a URL from RETRIEVED SOURCES only
  if that page's text contains the quote. If nothing supports a claim, leave it out and ask in "open_questions"."""


# ---------------------------------------------------------------- context
PROFILE_KEYS = ("company", "icp", "brand_voice")


def _researched_facts(brain: dict, protected: set[str]) -> str:
    """Product and company facts from the researched brief. Protected offerings are skipped:
    their rules table wins over anything the brief rewrite may have written about them."""
    lines = []
    c, pos = brain.get("company") or {}, brain.get("positioning") or {}
    if c.get("one_line_summary"):
        lines.append(f"- {c.get('name', 'The company')}: {c['one_line_summary']}")
    if pos.get("value_proposition"):
        lines.append(f"- Value proposition: {pos['value_proposition']}")
    lines += [f"- Differentiator: {d}" for d in pos.get("differentiators") or []]
    lines += [f"- Proof point: {d}" for d in pos.get("proof_points") or []]
    for o in brain.get("offerings") or []:
        if isinstance(o, dict) and o.get("name") and o["name"].lower() not in protected:
            lines.append(f"- {o['name']}: {o.get('description', '')}")
    return "\n".join(lines)


def _facts_block(rules: list[offerings.OfferingRule], researched: str) -> str:
    out = []
    for r in rules:
        out.append(f"{r.name} (source: {r.source}):")
        out += [f"- {f}" for f in r.facts]
        if r.not_yet_available:
            out.append("  NOT YET AVAILABLE, never claim or imply: " + "; ".join(r.not_yet_available))
        out += [f"  RULE: {x}" for x in r.copy_rules]
        if r.claim_bank:
            out.append(f"  CLAIM BANK (the only sentences that may describe {r.name}; copy word for word):")
            out += [f"  > {c}" for c in r.claim_bank]
    out.append("Other offerings and company facts (researched):\n" + (researched or "- none"))
    return "\n".join(out)


def load_context(tid: str, t: dict, intent: Intent) -> dict:
    brain = t.get("brain") or {}
    rules = offerings.load(tid)
    researched = _researched_facts(brain, {r.name.lower() for r in rules})
    profile = {k: brain[k] for k in PROFILE_KEYS if brain.get(k)}
    with core.db() as c:
        row = c.execute("SELECT content, created_at FROM outputs WHERE tenant_id=? AND agent='strategy' "
                        "ORDER BY id DESC LIMIT 1", (tid,)).fetchone()
    strategy = row["content"][:2500] if row else None

    results, seen = [], set()
    name = (brain.get("company") or {}).get("name") or tid
    for q in (f"{name} {intent.topic}".strip(), f"site:{tid} {intent.topic}".strip()):
        for r in core.web_search(q, n=4):
            if r["url"] not in seen:
                seen.add(r["url"])
                results.append(r)
    results = results[:6]

    brain_urls = [s.get("url") for s in brain.get("sources") or [] if isinstance(s, dict) and s.get("url")]
    allowed = set(brain_urls) | {r["url"] for r in results}
    retrieved = "\n".join(f"- {u}" for u in brain_urls[:12])
    retrieved += "".join(f"\n- {r['url']}\n  {r['title']}: {r['body'][:300]}" for r in results)
    facts = _facts_block(rules, researched)
    corpus = " ".join([facts, json.dumps(profile, ensure_ascii=False), strategy or "",
                       " ".join(r["title"] + " " + r["body"] for r in results)])
    return {"profile": profile, "strategy": strategy, "results": results, "corpus": corpus,
            "rules": rules, "facts": facts, "researched_facts": researched,
            "snippets": {r["url"]: r["title"] + " " + r["body"] for r in results},
            "allowed_urls": allowed, "retrieved": retrieved.strip() or "(none)"}


_FILLER = re.compile(r"\b(the|our|new|feature|features|product|launch|campaign|release|update)\b")


def topic_known(topic: str, corpus: str) -> bool:
    """True if the topic (minus filler words) appears somewhere in the context the agent was given."""
    key = re.sub(r"\s+", " ", _FILLER.sub(" ", (topic or "").lower())).strip()
    return not key or key in corpus.lower()


def product_rules(rules: list[offerings.OfferingRule], text: str) -> list[offerings.OfferingRule]:
    """Protected offerings named in the request."""
    low = text.lower()
    return [r for r in rules if r.name.lower() in low]


MAX_EXAMPLES = 3


def style_examples(tid: str, products: list[offerings.OfferingRule]) -> list[dict]:
    """Up to 3 most recently approved packages about the same offering. Used for tone and structure
    only: they never enter BRIEF FACTS, the verifier's facts or the source checks."""
    names = [r.name.lower() for r in products]
    if not names:
        return []
    with core.db() as c:
        rows = c.execute("SELECT id, content, note FROM outputs WHERE tenant_id=? AND agent='content' "
                         "AND status='approved' ORDER BY COALESCE(decided_at, created_at) DESC, id DESC",
                         (tid,)).fetchall()
    out = []
    for r in rows:
        try:
            d = json.loads(r["content"])
        except ValueError:
            continue
        about = f"{d.get('request', '')} {(d.get('intent') or {}).get('topic', '')}".lower()
        if any(n in about for n in names):
            out.append({"id": r["id"], "note": r["note"] or "", "package": d.get("package") or {}})
        if len(out) == MAX_EXAMPLES:
            break
    return out


def _example_text(ex: dict) -> str:
    p, videos = ex["package"], ex["package"].get("videos") or {}
    lines = [f"Example #{ex['id']}" + (f" (why it was approved: {ex['note']})" if ex["note"] else "")]
    for k in ("hook", "short", "explainer"):
        v = videos.get(k)
        if v:
            lines.append(f"{k} ({v.get('duration_seconds', '?')}s, {', '.join(v.get('aspect_ratios') or [])}):")
            lines += [f"  - {s.get('line') or '(no voiceover)'}" + (f" | Visual: {s['visual']}" if s.get("visual") else "")
                      for s in v.get("scenes") or []]
    lines += [f"{q.get('platform')} post: {q.get('text', '')}" for q in p.get("posts") or []]
    return "\n".join(lines)[:1500]


def banned_hits(obj, terms: list[str]) -> list[str]:
    """Terms from never_claim_terms that appear (as whole words) in any text field."""
    text = " ".join(_strings(obj))
    return [t for t in terms if re.search(r"(?<!\w)" + re.escape(t) + r"(?!\w)", text, re.I)]


def _copy(part) -> dict:
    return part.model_dump(exclude={"sources", "open_questions"})


def _llm(system: str, prompt: str, check, label: str, options: dict = LLM_OPTIONS, **kw):
    return core.ask_json_checked(system, prompt, check, label=label, llm_options=options, **kw)


def _ask_part(system: str, prompt: str, check, banned: list[str], label: str):
    """ask_json_checked with two retries: a shorter brief if the reply was cut off for length,
    and one regeneration if the reply uses a term it must never use (kept as is if that fails)."""
    try:
        part = _llm(system, prompt, check, label)
    except core.LLMTruncated:
        part = _llm(system, prompt + SHORTER, check, f"{label} (shorter)")
    hits = banned_hits(_copy(part), banned)
    if hits:
        try:
            part = _llm(system, prompt + (
                f"\n\nYour previous reply mentioned {', '.join(hits)}. That is not true of this product or not "
                "allowed in copy. Rewrite without mentioning it at all."), check, f"{label} (banned terms)")
        except core.LLMError as e:
            core.log.warning("Banned-terms rewrite of %s failed (%s); the final check will flag it", label, e)
    return part


def _system(t: dict, ctx: dict, examples: list[dict]) -> str:
    name = ((t.get("brain") or {}).get("company") or {}).get("name") or t["id"]
    today = dt.datetime.now(ZoneInfo(t.get("timezone") or "UTC")).strftime("%A, %d %B %Y")
    lang = t.get("language") or "English"
    ex = ""
    if examples:
        ex = ("STYLE EXAMPLES (approved by the company). Use them ONLY as examples of tone, pacing and structure. "
              "They are NOT a source of product facts: never repeat a product claim from them unless BRIEF FACTS "
              "state it, and never cite them in sources.\n" + "\n\n".join(_example_text(e) for e in examples) + "\n\n")
    return (f"You are the content agent of an AI marketing team working for {name}. Today is {today}.\n"
            "You write DRAFTS for human approval. Nothing is published, posted or sent.\n\n"
            f"BRIEF FACTS (the only allowed source for product claims):\n{ctx['facts']}\n\n"
            f"COMPANY PROFILE (audience and voice):\n{json.dumps(ctx['profile'], ensure_ascii=False)}\n\n"
            f"LATEST STRATEGY (excerpt, not a source for product claims):\n{ctx['strategy'] or 'None yet.'}\n\n"
            f"RETRIEVED SOURCES (the only URLs you may cite):\n{ctx['retrieved']}\n\n" + ex +
            f"{WRITING_RULES}\n\n{GUARDRAILS}\n\n"
            f"{core.language_rule(lang)} Output JSON only, no prose and no code fences.")


# ---------------------------------------------------------------- verification
VERIFY_PROMPT = """\
TASK: verify claims (part {key})
PRODUCTS: {products}

Classify EVERY numbered line below as PRODUCT or PROBLEM. Each is one sentence, on-screen text or visual direction.
PRODUCT = the line describes or depicts a product or offering: what it does, how, how fast, for whom, where it
works, what the user sees, its interface, or a promise of results. A line where "it", "this", "the extension" or
similar refers to a product is PRODUCT. A visual that shows a product acting, or its interface, is PRODUCT.
PROBLEM = the line is only about the problem, the risk, the audience or their situation, or is a [PLACEHOLDER],
and does not mention or depict a product.

COPY TO CHECK:
{lines}

Return ONLY JSON with one entry per line, in order:
{{"labels":[{{"id":1,"c":"PROBLEM"}},{{"id":2,"c":"PRODUCT"}}]}}"""


def classify(key: str, part, product_names: list[str]) -> tuple[list[dict], dict[int, str]]:
    """The checker (CHECKER_PROVIDER, Anthropic by default) labels every sentence and visual PRODUCT or PROBLEM.
    The claim bank check in checks.py decides what a PRODUCT line may say."""
    ls = checks.lines(_copy(part))
    if not ls:
        return ls, {}
    listing = "\n".join(f"{i}. [{x['where']}] {x['text']}" for i, x in enumerate(ls, 1))
    out = _llm("You classify marketing copy line by line. Output JSON only.",
               VERIFY_PROMPT.format(key=key, products=", ".join(product_names) or "the company's offerings",
                                    lines=listing),
               _checker(_Verify), f"check {PART_NAMES[key]}", options=None, provider=CHECKER_PROVIDER,
               max_tokens=8000 if CHECKER_PROVIDER.lower() == "anthropic" else 5000)   # Groq: 8k tokens/minute
    return ls, {x.id: ("PRODUCT" if x.c.strip().upper().startswith("PROD") else "PROBLEM") for x in out.labels}


def _fix_note(bad: list[dict]) -> str:
    return ("\n\nA check of your previous reply found these problems:\n"
            + "\n".join(f"- [{b.get('where', '')}] {b['claim']} ({b['reason']})" for b in bad)
            + f"\nRewrite this part and fix every one. A sentence that describes the product must be copied word "
              "for word from its CLAIM BANK; otherwise write about the problem or the audience without mentioning the "
              f"product. Use [WHAT THE USER SEES] for any visual of the product, approved placeholders exactly as "
              f"written ({PLACEHOLDERS}), and the required aspect ratios. Add questions to open_questions.")


# ---------------------------------------------------------------- steps
INTENT_PROMPT = """\
TASK: intent
Turn this marketing request into structured intent.

REQUEST: {text}

route: "content" if the user wants marketing content written (ads, video scripts, posts, captions, articles,
campaign assets). Anything else (publishing or posting for them, sending email, analytics, research, budgets,
scheduling) is "unsupported".
platforms: any of linkedin, instagram, x, youtube, facebook, blog. Only the ones asked for or clearly implied.
long_form: true only if the request asks for an article, blog post or other long-form piece.

Return ONLY JSON:
{{"route":"content|unsupported","platforms":[],"goal":"","topic":"","audience":"","constraints":[],"long_form":false}}"""

VIDEO_SPEC = """{"duration_seconds":0,"aspect_ratios":[],
  "scenes":[{"line":"spoken words or voiceover","on_screen_text":"","visual":"shot or visual direction"}],
  "caption":"caption text to post with the video","shot_list":["..."]}"""
SOURCES_SPEC = '"sources":[{"claim":"","quote":"","url":""}],"open_questions":[]'


def parse_intent(text: str) -> Intent:
    t0 = "You turn marketing requests into structured intent. Output JSON only."
    intent = _llm(t0, INTENT_PROMPT.format(text=text), _checker(Intent), "intent", max_tokens=1500)
    if intent.route not in ROUTES:
        raise UnsupportedRequest(
            f"Unsupported request (understood as '{intent.route}'). Right now the command box only creates "
            "campaign content drafts: video scripts, platform posts (LinkedIn, Instagram, X, YouTube, Facebook) "
            "and articles. It does not publish, post, send or analyse anything.")
    return intent


def _ratio_hint(platforms: list[str]) -> str:
    return ("aspect_ratios: the hook and short MUST include 9:16 and 4:5; the explainer MUST include 16:9 "
            f"(add others if useful). Requested platforms: {', '.join(platforms) or 'not specified'}.")


def build_package(tid: str, t: dict, text: str, intent: Intent) -> dict:
    ctx = load_context(tid, t, intent)
    products = product_rules(ctx["rules"], f"{text} {intent.topic}")
    examples = style_examples(tid, products)
    leak_terms = {p.name: checks.cross_offering_terms(p, ctx["rules"], t.get("brain") or {}) for p in products}
    all_facts = ctx["facts"]
    system = _system(t, ctx, examples)
    head = f"REQUEST: {text}\nINTENT: {intent.model_dump_json()}\n"
    unknown_topic = not products and not topic_known(intent.topic, ctx["corpus"])   # a named offering is known
    if unknown_topic:
        head += (f'UNKNOWN TOPIC: "{intent.topic}" is not described in any source above. Do not say what it does, '
                 "how it works, what it connects to or what it produces. Refer to it by name and use placeholders "
                 "wherever a product detail is needed. Ask about those details in open_questions.\n")
    banned = list(dict.fromkeys(x for r in products for x in r.never_claim_terms))
    if products:
        head += ("FOCUS PRODUCT: " + ", ".join(r.name for r in products) + ". Describe it ONLY with sentences "
                 "copied word for word from its CLAIM BANK. Do not borrow capabilities from other offerings.\n")
    post_platforms = [p for p in intent.platforms if p in PLATFORMS and p != "blog"]
    unsupported = [p for p in intent.platforms if p not in PLATFORMS]
    if not post_platforms:
        post_platforms = ["linkedin"]

    parts, prompts, checks_, failed = {}, {}, {}, {}

    def gen(key, prompt, check):
        prompts[key], checks_[key] = prompt, check
        try:
            parts[key] = _ask_part(system, prompt, check, banned, PART_NAMES[key])
        except core.LLMError as e:            # save the rest of the package; this part is marked failed
            core.log.warning("Content part %s failed: %s", PART_NAMES[key], e)
            failed[key] = str(e)

    gen("A", "TASK: summary and short videos\n" + head +
        "Write the campaign summary (goal, audience, core_message, call_to_action) and two video scripts:\n"
        "- hook: 3 to 6 seconds. The single opening line or visual that stops the scroll. 1-2 scenes.\n"
        "- short: about 30 seconds (20-45). An ad-length script, scene by scene.\n"
        + _ratio_hint(intent.platforms) + "\n"
        'Return ONLY JSON: {"campaign_summary":{"goal":"","audience":"","core_message":"","call_to_action":""},'
        f'"hook":{VIDEO_SPEC},"short":{VIDEO_SPEC},{SOURCES_SPEC}}}',
        _checker(_SummaryPart, _videos_check))
    summary = (parts["A"].campaign_summary.model_dump_json() if "A" in parts
               else "(not available, write from the request and BRIEF FACTS)")

    gen("B", "TASK: explainer video\n" + head + f"CAMPAIGN SUMMARY: {summary}\n"
        "Write the explainer video: about 2 minutes (90-150 seconds), a full walkthrough, scene by scene, "
        "6-12 scenes. " + _ratio_hint(intent.platforms) + "\n"
        f'Return ONLY JSON: {{"explainer":{VIDEO_SPEC},{SOURCES_SPEC}}}',
        _checker(_ExplainerPart, lambda p: _in_range("explainer", p.explainer)))

    def need_all(p):
        missing = [x for x in post_platforms if x not in {q.platform for q in p.posts}]
        if missing:
            raise core.LLMOutputError("missing posts for: " + ", ".join(missing))

    gen("C", "TASK: platform posts\n" + head + f"CAMPAIGN SUMMARY: {summary}\n"
        "Write one post draft per platform, sized to that platform:\n"
        + "\n".join(f"- {POST_STYLE[p]}" for p in post_platforms) + "\n"
        'Return ONLY JSON: {"posts":[{"platform":"' + post_platforms[0] + '","text":"","hashtags":[],"notes":""}],'
        + SOURCES_SPEC + "}",
        _checker(_PostsPart, need_all))

    if intent.long_form or "blog" in intent.platforms:
        gen("D", "TASK: article\n" + head + f"CAMPAIGN SUMMARY: {summary}\n"
            "Write a long-form article: a title, an outline (5-8 points) and the full draft (600-900 words, "
            "markdown headings allowed).\n"
            'Return ONLY JSON: {"article":{"title":"","outline":[],"draft":""},' + SOURCES_SPEC + "}",
            _checker(_ArticlePart))

    if not parts:
        raise core.LLMError("Every part of the package failed: " + "; ".join(dict.fromkeys(failed.values())))

    product_names = list(dict.fromkeys([r.name for r in ctx["rules"]] + [
        o["name"] for o in (t.get("brain") or {}).get("offerings") or [] if isinstance(o, dict) and o.get("name")]))

    def issues(key) -> list[dict]:
        """Checker labels first, then the claim bank and the other deterministic checks, which it cannot override.
        If the checker fails, the whole part is listed as unverified and the name check still runs on every line."""
        bad = []
        try:
            ls, labels = classify(key, parts[key], product_names)
        except core.LLMError as e:
            ls = checks.lines(_copy(parts[key]))
            labels = {i: "PROBLEM" for i in range(1, len(ls) + 1)}   # only the name check can flag a line now
            bad.append({"part": PART_NAMES[key], "where": "(whole part)", "claim": "(every line in this part)",
                        "reason": f"the claim check failed ({e}), so nothing in this part is verified"})
        bad += [{"part": PART_NAMES[key], **r} for r in checks.claim_bank(ls, labels, products)]
        rules = checks.run_all(_copy(parts[key]), products, leak_terms, all_facts, t.get("language") or "English")
        return bad + [{"part": PART_NAMES[key], **r} for r in rules]

    unverified = []
    for key in list(parts):
        bad = issues(key)
        if bad:                                   # one regeneration, then whatever is left is listed
            try:
                parts[key] = _ask_part(system, prompts[key] + _fix_note(bad), checks_[key], banned,
                                       f"{PART_NAMES[key]} (fact-check rewrite)")
                bad = issues(key)
            except core.LLMError as e:            # keep the checked version; its issues stay listed
                core.log.warning("Fact-check rewrite of %s failed: %s", PART_NAMES[key], e)
        unverified += [b for i, b in enumerate(bad) if b not in bad[:i]]

    a, b, c, d = parts.get("A"), parts.get("B"), parts.get("C"), parts.get("D")
    fail = lambda k: FailedPart(error=failed[k])
    sources, source_questions = check_sources([s for p in parts.values() for s in p.sources], ctx)

    notes = []
    warning = None
    if failed:
        warning = (f"Part(s) failed and were saved as '{FAILED}': " + ", ".join(PART_NAMES[k] for k in failed)
                   + ". The rest of the package was saved. Run the request again to regenerate them.")
        notes.append(warning)
    if unknown_topic:
        notes.append(f'"{intent.topic}" is not described in the brief, the strategy or the search results, '
                     "so product details are left as placeholders. Answer the questions below and run "
                     "the request again.")
    if not ctx["strategy"]:
        notes.append("No Strategy output exists yet, so this package is based on the company brief only. "
                     "Run the Strategy agent to align future packages with a plan.")
    if not ctx["results"]:
        notes.append("Web search returned nothing, so only the company brief's own sources could be cited.")
    if unsupported:
        notes.append("No post drafts for unsupported platforms: " + ", ".join(unsupported))
    if not [p for p in intent.platforms if p in PLATFORMS and p != "blog"]:
        notes.append("No platform was named, so the post draft is for LinkedIn.")

    pkg = Package(
        unverified_claims=unverified,
        campaign_summary=a.campaign_summary if a else fail("A"),
        videos=Videos(hook=a.hook if a else fail("A"), short=a.short if a else fail("A"),
                      explainer=b.explainer if b else fail("B")),
        posts=[p for p in c.posts if p.platform in post_platforms] if c else fail("C"),
        article=(d.article if d else fail("D")) if "D" in prompts else None,
        sources=sources,
        open_questions=list(dict.fromkeys([q for p in parts.values() for q in p.open_questions]
                                          + source_questions)),
        notes=notes).model_dump()

    hits = banned_hits({k: v for k, v in pkg.items() if k not in ("open_questions", "notes")}, banned)
    if hits:
        pkg["notes"].insert(0, "Remove before using: the copy mentions " + ", ".join(hits) +
                            ", which the company says must not be claimed for " +
                            ", ".join(r.name for r in products) + ".")
    if unverified:
        pkg["notes"].insert(0, f"{len(unverified)} unverified product claim(s) survived the rewrite. "
                               "They are listed under 'Unverified claims, remove before use'.")

    pkg = flag_links_and_figures(clean(pkg), ctx["allowed_urls"], ctx["corpus"] + " " + text)
    pkg["open_questions"] = checks.dedupe_questions(pkg["open_questions"])
    return pkg, warning


_PLACEHOLDER = {"duration_seconds": 120, "aspect_ratios": ["16:9"], "scenes": [{"line": ".", "visual": "."}],
                "caption": "."}


def _in_range(name: str, v: Video):
    lo, hi = LENGTHS[name]
    if not lo <= v.duration_seconds <= hi:
        raise core.LLMOutputError(f"{name} video must be {lo}-{hi} seconds, got {v.duration_seconds}")


def _videos_check(p):
    try:
        Videos.model_validate({"hook": p.hook, "short": p.short, "explainer": _PLACEHOLDER})
    except ValidationError as e:
        raise core.LLMOutputError(_short_errors(e)) from None


def run_command(tid: str, text: str) -> dict:
    """Parse the request, build the package, store it as one draft output row."""
    if CHECKER_PROVIDER.lower() == "anthropic" and not (os.getenv("ANTHROPIC_API_KEY") or "").strip():
        raise core.LLMError("The claim checker runs on Anthropic (CHECKER_PROVIDER), but ANTHROPIC_API_KEY is not "
                            "set in .env. Add it and restart the server.")
    t = core.get_tenant(tid)
    intent = parse_intent(text)
    pkg, warning = build_package(tid, t, text, intent)
    stored = {"request": text, "intent": clean(intent.model_dump()), "package": pkg}
    with core.db() as c:
        oid = c.execute("INSERT INTO outputs(tenant_id,agent,content,created_at,status) VALUES(?,?,?,?,?)",
                        (tid, "content", json.dumps(stored, ensure_ascii=False),
                         dt.datetime.utcnow().isoformat(), "draft")).lastrowid
    return {"output_id": oid, **stored, "warning": warning}
