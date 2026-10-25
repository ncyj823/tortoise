"""Overnight tidier: sweep saved content drafts, fix what can be fixed deterministically,
and quarantine the rest for a human.

Everything here runs in code, with no LLM call, so the result is reproducible and a
nightly run cannot invent a fact. It reuses the same rules the content agent is checked
with in checks.py and the same protected offerings table, so the tidier can never be
more lenient than the agent that wrote the draft.

Three kinds of fix are applied, and only these:
- normalisation: normalize.fix_text repairs mojibake, dashes, near-miss placeholders and double spaces
- writing rules: em dashes and double hyphens become commas (content.plain)
- never-claim terms: a sentence that uses one is deleted, so the claim stops being made

Anything left (a product line that is not word for word from the claim bank, a missing
aspect ratio, an unapproved bracket, the wrong language) is reported, not rewritten:
a deterministic pass cannot paraphrase a claim safely.
"""
import json, re, datetime as dt
from typing import Any

from pydantic import BaseModel, Field

from . import checks, content, core, normalize, offerings

STATUS_APPROVED = "APPROVED"
STATUS_SANITIZED = "SANITIZED"
STATUS_FLAGGED = "FLAGGED_FOR_HUMAN"

_SENT = re.compile(r"(?<=[.!?])\s+")


class TidierItem(BaseModel):
    """One piece of copy to audit: an id, and one package part or whole package."""
    id: str
    part: dict


class TidierReport(BaseModel):
    """Result of one sweep. Stored as JSON so the UI can show the last run."""
    tenant_id: str
    created_at: str
    total_items_processed: int = Field(description="Number of drafts audited.")
    approved: int = Field(description="Drafts that passed every deterministic check unchanged.")
    items_sanitized: int = Field(description="Drafts fixed in code and re-checked clean.")
    flagged_for_human: int = Field(description="Drafts still breaking a rule; listed for review.")
    applied: list[str] = Field(default_factory=list,
                               description="Draft ids whose sanitized text was written back (apply=true only).")
    audit_logs: list[dict] = Field(default_factory=list, description="One entry per draft.")


# ---------------------------------------------------------------- context
def _context(tid: str) -> tuple[list, dict, str, str]:
    """(rules, leak terms, facts block, language) for a tenant, without any web search."""
    t = core.get_tenant(tid) or {}
    rules = offerings.load(tid)
    researched = content._researched_facts(t.get("brain") or {}, {r.name.lower() for r in rules})
    facts = content._facts_block(rules, researched)
    leak = {r.name: checks.cross_offering_terms(r, rules, t.get("brain") or {}) for r in rules}
    return rules, leak, facts, t.get("language") or "English"


def _parts(pkg: dict) -> list[tuple[str, dict]]:
    """Split a stored package into the same parts the content agent checks (A-D)."""
    videos = pkg.get("videos") or {}
    out = [("A", {"campaign_summary": pkg.get("campaign_summary"),
                  "hook": videos.get("hook"), "short": videos.get("short")}),
           ("B", {"explainer": videos.get("explainer")}),
           ("C", {"posts": pkg.get("posts")}),
           ("D", {"article": pkg.get("article")})]
    return [(k, {n: v for n, v in p.items() if v is not None}) for k, p in out]


# ---------------------------------------------------------------- audit
_DASH = re.compile(r"[—―]|[ \t]+[–-][ \t]+")


def writing_issues(ls: list[dict], banned: list[str]) -> list[dict]:
    """Rules the content agent is held to but checks.py does not test: em dashes and never-claim terms.
    They are checked here so the tidier can also fix them (see _sanitize)."""
    issues = []
    for x in ls:
        hits = [b for b in banned if re.search(r"(?<!\w)" + re.escape(b) + r"(?!\w)", x["text"], re.I)]
        if hits:
            issues.append({"where": x["where"], "claim": x["text"],
                           "reason": f"rule: uses a term the company says must never be claimed ({', '.join(hits)})"})
        if _DASH.search(x["text"]):
            issues.append({"where": x["where"], "claim": x["text"], "reason": "rule: uses an em dash"})
    return issues


def _normalize(obj: Any, changes: list[dict], path: str = "") -> Any:
    """Apply normalize.fix_text to every string and record each change (path, before, after)."""
    if isinstance(obj, str):
        new = normalize.fix_text(obj)
        if new != obj:
            changes.append({"path": path or "(text)", "before": obj, "after": new})
        return new
    if isinstance(obj, list):
        return [_normalize(x, changes, f"{path}[{i}]") for i, x in enumerate(obj)]
    if isinstance(obj, dict):
        return {k: _normalize(v, changes, f"{path}.{k}" if path else k) for k, v in obj.items()}
    return obj


def audit_part(part: dict, rules: list, leak: dict, facts: str, lang: str,
               changes: list[dict] | None = None, prefix: str = "") -> tuple[list[dict], dict]:
    """Normalise the text first (normalize.fix_text), then find every deterministic issue in the part.
    No checker labels: without the LLM only a line that names a product (or a visual of it) can be
    held to the claim bank. Returns the issues and the normalised part."""
    changes = changes if changes is not None else []
    part = _normalize(part, changes, prefix)
    ls = checks.lines(part)
    labels = {i: "PROBLEM" for i in range(1, len(ls) + 1)}
    issues = writing_issues(ls, _banned(rules))
    issues += checks.claim_bank(ls, labels, rules)
    issues += checks.run_all(part, rules, leak, facts, lang)
    return issues, part


def _banned(rules: list) -> list[str]:
    return list(dict.fromkeys(x for r in rules for x in r.never_claim_terms))


def _drop_sentences(text: str, banned: list[str]) -> tuple[str, list[str]]:
    """Remove sentences that use a never-claim term. Removing the sentence is the only rewrite
    that cannot change what the copy claims."""
    if not banned:
        return text, []
    kept, dropped = [], []
    for s in _SENT.split(text):
        hit = [b for b in banned if re.search(r"(?<!\w)" + re.escape(b) + r"(?!\w)", s, re.I)]
        if hit:
            dropped.append(s.strip())
        else:
            kept.append(s)
    return " ".join(kept), dropped


def _sanitize(obj: Any, banned: list[str], changes: list[dict], path: str = "") -> Any:
    if isinstance(obj, str):
        new, dropped = _drop_sentences(content.plain(obj), banned)
        new = re.sub(r"[ \t]{2,}", " ", new).strip()
        if new != obj:
            prior = next((c for c in reversed(changes) if c["path"] == (path or "(text)")), None)
            if prior is not None:            # merge with the normalisation entry for the same field
                prior["after"] = new
                if dropped:
                    prior["dropped"] = dropped
            else:
                changes.append({"path": path or "(text)", "before": obj, "after": new, "dropped": dropped})
        return new
    if isinstance(obj, list):
        return [_sanitize(x, banned, changes, f"{path}[{i}]") for i, x in enumerate(obj)]
    if isinstance(obj, dict):
        return {k: _sanitize(v, banned, changes, f"{path}.{k}" if path else k) for k, v in obj.items()}
    return obj


# ---------------------------------------------------------------- stored drafts
def _drafts(tid: str, output_ids: list[int] | None) -> list[dict]:
    q = ("SELECT id, content FROM outputs WHERE tenant_id=? AND agent='content' "
         "AND status IN ('draft', 'needs_review')")
    args: list = [tid]
    if output_ids:
        q += " AND id IN (" + ",".join("?" * len(output_ids)) + ")"
        args += output_ids
    with core.db() as c:
        rows = c.execute(q + " ORDER BY id", args).fetchall()
    out = []
    for r in rows:
        try:
            out.append({"id": str(r["id"]), "raw": r["content"], "stored": json.loads(r["content"])})
        except ValueError:
            core.log.warning("Tidier skipped output %s: content is not JSON", r["id"])
    return out


def _audit_item(item: dict, rules: list, leak: dict, facts: str, lang: str) -> tuple[dict, dict]:
    """Audit one item, try the deterministic fixes, audit again. Returns the log and the
    sanitized parts that a write-back would need."""
    changes: list[dict] = []
    banned = _banned(rules)
    before, after = [], []
    fixed_parts: dict[str, dict] = {}

    for name, part in _parts(item["part"]):
        seen = len(changes)
        issues, part = audit_part(part, rules, leak, facts, lang, changes, f"part {name}")
        fixed = part
        if issues:
            before += [{"part": name, **x} for x in issues]
            fixed = _sanitize(part, banned, changes, f"part {name}")
            after += [{"part": name, **x} for x in audit_part(fixed, rules, leak, facts, lang)[0]]
        if len(changes) > seen:
            fixed_parts[name] = fixed

    if not before and not changes:
        return ({"id": item["id"], "status": STATUS_APPROVED, "violations": [], "changes": [],
                 "details": "Passed every deterministic check unchanged."}, fixed_parts)
    if not after:
        return ({"id": item["id"], "status": STATUS_SANITIZED, "violations": [], "changes": changes,
                 "details": "Fixed in code (normalisation, writing rules and never-claim terms), re-checked clean."},
                fixed_parts)
    return ({"id": item["id"], "status": STATUS_FLAGGED, "violations": after, "changes": changes,
             "details": "Needs a human: the remaining violations cannot be fixed without rewriting copy."},
            fixed_parts)


def _apply_parts(pkg: dict, fixed: dict[str, dict]) -> dict:
    """Put the sanitized A-D parts back into the stored package shape."""
    out = dict(pkg)
    videos = dict(out.get("videos") or {})
    a = fixed.get("A")
    if a is not None:
        if "campaign_summary" in a:
            out["campaign_summary"] = a["campaign_summary"]
        for key in ("hook", "short"):
            if key in a:
                videos[key] = a[key]
    b = fixed.get("B")
    if b is not None and "explainer" in b:
        videos["explainer"] = b["explainer"]
    if videos:
        out["videos"] = videos
    c = fixed.get("C")
    if c is not None and "posts" in c:
        out["posts"] = c["posts"]
    d = fixed.get("D")
    if d is not None and "article" in d:
        out["article"] = d["article"]
    return out


def _write_back(tid: str, drafts: list[dict], logs: list[dict], fixed_by_id: dict[str, dict]) -> list[str]:
    """Write a sanitized package back to its draft, but only for SANITIZED results. The first
    original content is kept in original_text so the change can be undone."""
    applied = []
    for draft, log in zip(drafts, logs):
        if log["status"] != STATUS_SANITIZED:
            continue
        fixed = fixed_by_id.get(draft["id"]) or {}
        if not fixed:
            continue
        stored = draft["stored"]
        updated = {**stored, "package": _apply_parts(stored.get("package") or {}, fixed)}
        with core.db() as c:
            c.execute("UPDATE outputs SET content=?, original_text=COALESCE(original_text, ?) "
                      "WHERE id=? AND tenant_id=?",
                      (json.dumps(updated, ensure_ascii=False), draft["raw"], int(draft["id"]), tid))
        applied.append(draft["id"])
    return applied


def run(tid: str, output_ids: list[int] | None = None, apply: bool = False) -> TidierReport:
    """Audit the tenant's saved drafts (all, or only output_ids). By default it never writes to a
    draft. With apply=True it writes back only the results that are SANITIZED, keeping the original."""
    if not core.get_tenant(tid):
        raise ValueError(f"Unknown tenant: {tid}")
    rules, leak, facts, lang = _context(tid)
    drafts = _drafts(tid, output_ids)
    logs, fixed_by_id = [], {}
    for d in drafts:
        log, fixed = _audit_item({"id": d["id"], "part": d["stored"].get("package") or {}},
                                 rules, leak, facts, lang)
        logs.append(log)
        fixed_by_id[d["id"]] = fixed
    applied = _write_back(tid, drafts, logs, fixed_by_id) if apply else []
    report = TidierReport(
        tenant_id=tid, created_at=dt.datetime.utcnow().isoformat(),
        total_items_processed=len(logs),
        approved=sum(1 for x in logs if x["status"] == STATUS_APPROVED),
        items_sanitized=sum(1 for x in logs if x["status"] == STATUS_SANITIZED),
        flagged_for_human=sum(1 for x in logs if x["status"] == STATUS_FLAGGED),
        applied=applied,
        audit_logs=logs)
    core.log.info("Tidier for %s: %s drafts, %s clean, %s fixed, %s flagged, %s written back", tid,
                  report.total_items_processed, report.approved, report.items_sanitized,
                  report.flagged_for_human, len(report.applied))
    save_report(report)
    return report


# ---------------------------------------------------------------- storage
def save_report(report: TidierReport) -> None:
    with core.db() as c:
        c.execute("INSERT INTO tidier_reports(tenant_id, created_at, report) VALUES(?,?,?)",
                  (report.tenant_id, report.created_at, report.model_dump_json()))


def latest(tid: str) -> dict | None:
    with core.db() as c:
        row = c.execute("SELECT report FROM tidier_reports WHERE tenant_id=? ORDER BY id DESC LIMIT 1",
                        (tid,)).fetchone()
    return json.loads(row["report"]) if row else None
