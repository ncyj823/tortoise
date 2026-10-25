"""Protected product rules: facts, not-yet-available list, copy rules and never-claim terms.

Stored in their own table, not in the tenant brain, so the "Teach your team" LLM rewrite
(core.refine_brain only writes tenants.brain) can never change them. Edits go through
preview (returns a diff and the version it was made against) and then save, which refuses
to write if the rules changed since that preview.
"""
import difflib, hashlib, json, datetime as dt

from pydantic import BaseModel, Field, field_validator

from . import core


class OfferingRule(BaseModel):
    name: str = Field(min_length=1)
    facts: list[str] = Field(min_length=1)          # the only statements copy may make about this offering
    not_yet_available: list[str] = []               # never claim or imply these
    copy_rules: list[str] = []
    never_claim_terms: list[str] = []               # banned-terms check, second line of defence
    claim_bank: list[str] = []                      # the only sentences copy may use to describe this offering
    source: str = core.RULES_SOURCE

    @field_validator("name", "source")
    @classmethod
    def _strip(cls, v):
        return v.strip()

    @field_validator("facts", "not_yet_available", "copy_rules", "never_claim_terms", "claim_bank")
    @classmethod
    def _lines(cls, v):
        return [x.strip() for x in v if x and x.strip()]


class StaleEdit(Exception):
    """The rules changed after the diff was shown."""


def to_text(rule: OfferingRule) -> str:
    return json.dumps(rule.model_dump(), ensure_ascii=False, indent=2)


def version(text: str | None) -> str | None:
    return hashlib.sha256(text.encode()).hexdigest()[:12] if text else None


def get_text(tid: str, name: str) -> str | None:
    with core.db() as c:
        row = c.execute("SELECT rule FROM offering_rules WHERE tenant_id=? AND name=?", (tid, name)).fetchone()
    return row["rule"] if row else None


def load(tid: str) -> list[OfferingRule]:
    with core.db() as c:
        rows = c.execute("SELECT rule FROM offering_rules WHERE tenant_id=? ORDER BY name", (tid,)).fetchall()
    return [OfferingRule.model_validate_json(r["rule"]) for r in rows]


def all_rules(tid: str) -> list[dict]:
    with core.db() as c:
        rows = c.execute("SELECT rule, updated_at FROM offering_rules WHERE tenant_id=? ORDER BY name",
                         (tid,)).fetchall()
    return [{**json.loads(r["rule"]), "version": version(r["rule"]), "updated_at": r["updated_at"]} for r in rows]


def preview(tid: str, rule: OfferingRule) -> dict:
    old, new = get_text(tid, rule.name) or "", to_text(rule)
    diff = difflib.unified_diff(old.splitlines(), new.splitlines(), "saved", "proposed", lineterm="")
    return {"name": rule.name, "changed": old != new, "base_version": version(old), "diff": "\n".join(diff)}


def save(tid: str, rule: OfferingRule, base_version: str | None) -> dict:
    current = get_text(tid, rule.name)
    if version(current) != base_version:
        raise StaleEdit(rule.name)
    text = to_text(rule)
    with core.db() as c:
        c.execute("INSERT INTO offering_rules(tenant_id,name,rule,updated_at) VALUES(?,?,?,?) "
                  "ON CONFLICT(tenant_id,name) DO UPDATE SET rule=excluded.rule, updated_at=excluded.updated_at",
                  (tid, rule.name, text, dt.datetime.utcnow().isoformat()))
    return {**rule.model_dump(), "version": version(text)}
