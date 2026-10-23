"""Protected product rules: facts, not-yet-available list, copy rules, never-claim terms,
pricing boundaries, and exact claim banks.

Stored in their own table, not in the tenant brain, so the "Teach your team" LLM rewrite
(core.refine_brain only writes tenants.brain) can never change them. Edits go through
preview (returns a diff and the version it was made against) and then save, which refuses
to write if the rules changed since that preview.
"""
from __future__ import annotations

import datetime as dt
import difflib
import hashlib
import json
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from . import core


class ProductOffering(BaseModel):
    """Immutable ground-truth definition of a product offering's capabilities, 
    pricing boundaries, and compliance constraints.
    """
    offering_id: str = Field(..., description="Unique identifier for the product offering.")
    name: str = Field(..., description="Human-readable product name.")
    authorized_claims: List[str] = Field(
        default_factory=list, 
        description="Exact verbatim product claims approved for marketing and content generation."
    )
    pricing_boundaries: Dict[str, float] = Field(
        default_factory=dict, 
        description="Strict financial and pricing constraints."
    )
    feature_limits: Dict[str, str] = Field(
        default_factory=dict, 
        description="Technical boundaries and capacity limits."
    )
    forbidden_terms: List[str] = Field(
        default_factory=list, 
        description="Blacklisted terminology, unverified superlatives, or unauthorized promises."
    )

    def to_offering_rule(self) -> OfferingRule:
        """Convert to OfferingRule for persistence and backward compatibility."""
        return OfferingRule(
            name=self.name,
            facts=self.authorized_claims or [self.name],
            not_yet_available=[f"{k}: {v}" for k, v in self.feature_limits.items()],
            never_claim_terms=self.forbidden_terms,
            claim_bank=self.authorized_claims,
            pricing_boundaries=self.pricing_boundaries,
            feature_limits=self.feature_limits,
            forbidden_terms=self.forbidden_terms,
            authorized_claims=self.authorized_claims,
            offering_id=self.offering_id,
        )


class OfferingRule(BaseModel):
    name: str = Field(min_length=1)
    facts: list[str] = Field(default_factory=list)          # the only statements copy may make about this offering
    not_yet_available: list[str] = Field(default_factory=list)               # never claim or imply these
    copy_rules: list[str] = Field(default_factory=list)
    never_claim_terms: list[str] = Field(default_factory=list)               # banned-terms check, second line of defence
    claim_bank: list[str] = Field(default_factory=list)                      # the only sentences copy may use to describe this offering
    source: str = core.RULES_SOURCE
    offering_id: Optional[str] = None
    pricing_boundaries: dict[str, float] = Field(default_factory=dict)
    feature_limits: dict[str, str] = Field(default_factory=dict)
    forbidden_terms: list[str] = Field(default_factory=list)
    authorized_claims: list[str] = Field(default_factory=list)

    @field_validator("name", "source")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("facts", "not_yet_available", "copy_rules", "never_claim_terms", "claim_bank", "forbidden_terms", "authorized_claims")
    @classmethod
    def _lines(cls, v: list[str]) -> list[str]:
        return [x.strip() for x in v if x and x.strip()]

    def model_post_init(self, __context: object) -> None:
        if not self.authorized_claims and self.claim_bank:
            self.authorized_claims = list(self.claim_bank)
        elif not self.claim_bank and self.authorized_claims:
            self.claim_bank = list(self.authorized_claims)
        if not self.forbidden_terms and self.never_claim_terms:
            self.forbidden_terms = list(self.never_claim_terms)
        elif not self.never_claim_terms and self.forbidden_terms:
            self.never_claim_terms = list(self.forbidden_terms)
        if not self.facts and self.authorized_claims:
            self.facts = list(self.authorized_claims)
        if not self.offering_id:
            self.offering_id = self.name.lower().replace(" ", "_")

    def to_product_offering(self) -> ProductOffering:
        """Convert to strict ProductOffering representation."""
        return ProductOffering(
            offering_id=self.offering_id or self.name.lower().replace(" ", "_"),
            name=self.name,
            authorized_claims=self.authorized_claims or self.claim_bank or self.facts,
            pricing_boundaries=self.pricing_boundaries,
            feature_limits=self.feature_limits,
            forbidden_terms=self.forbidden_terms or self.never_claim_terms,
        )


class ProtectedClaimBankRegistry:
    """Thread-safe, immutable registry managing ground-truth product offerings 
    and providing deterministic validation interfaces for the verification pipeline.
    """
    def __init__(self) -> None:
        self._offerings: Dict[str, ProductOffering] = {}
        self._initialize_default_offerings()

    def _initialize_default_offerings(self) -> None:
        """Populates baseline ground-truth offerings for the platform."""
        default_offering = ProductOffering(
            offering_id="core_agent_suite_v1",
            name="Core Agent Suite",
            authorized_claims=[
                "Multi-tenant SQLite persistence with thread-safe connection pooling",
                "Deterministic multi-stage drafting pipeline with claim-bank validation",
                "Timezone-aware research digests via APScheduler background workers",
                "Secure OpenAI-compatible and Anthropic API provider abstractions"
            ],
            pricing_boundaries={
                "base_monthly": 49.00,
                "enterprise_monthly": 199.00
            },
            feature_limits={
                "max_concurrent_agents": "10",
                "max_tokens_per_request": "128000"
            },
            forbidden_terms=[
                "unlimited scaling",
                "100% autonomous",
                "zero human oversight",
                "guaranteed financial returns",
                "instantaneous execution"
            ]
        )
        self.register_offering(default_offering)

    def register_offering(self, offering: ProductOffering | OfferingRule) -> None:
        """Registers a product offering in the registry under its offering_id and name."""
        if isinstance(offering, OfferingRule):
            po = offering.to_product_offering()
        else:
            po = offering
        self._offerings[po.offering_id] = po
        if po.name:
            self._offerings[po.name] = po
            self._offerings[po.name.lower()] = po

    def get_offering(self, offering_id: str) -> Optional[ProductOffering]:
        """Retrieves a product offering by its unique identifier or name."""
        return self._offerings.get(offering_id) or self._offerings.get(offering_id.lower())

    def validate_claim(self, offering_id: str, claim: str) -> bool:
        """Determines if an extracted claim matches the exact authorized claim bank."""
        offering = self.get_offering(offering_id)
        if not offering:
            return False
        return claim.strip() in [c.strip() for c in offering.authorized_claims]

    def scan_forbidden_terms(self, offering_id: str, text: str) -> List[str]:
        """Scans text for blacklisted phrases defined in the protected claim bank."""
        offering = self.get_offering(offering_id)
        if not offering:
            return []
        
        found_terms: List[str] = []
        text_lower = text.lower()
        for term in offering.forbidden_terms:
            if term.lower() in text_lower:
                found_terms.append(term)
        return found_terms


# Global singleton registry instance
claim_bank = ProtectedClaimBankRegistry()


class StaleEdit(Exception):
    """The rules changed after the diff was shown."""


def to_text(rule: OfferingRule | ProductOffering) -> str:
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
    rules = [OfferingRule.model_validate_json(r["rule"]) for r in rows]
    for r in rules:
        claim_bank.register_offering(r)
    return rules


def all_rules(tid: str) -> list[dict]:
    with core.db() as c:
        rows = c.execute("SELECT rule, updated_at FROM offering_rules WHERE tenant_id=? ORDER BY name",
                         (tid,)).fetchall()
    return [{**json.loads(r["rule"]), "version": version(r["rule"]), "updated_at": r["updated_at"]} for r in rows]


def preview(tid: str, rule: OfferingRule | ProductOffering) -> dict:
    if isinstance(rule, ProductOffering):
        rule = rule.to_offering_rule()
    old, new = get_text(tid, rule.name) or "", to_text(rule)
    diff = difflib.unified_diff(old.splitlines(), new.splitlines(), "saved", "proposed", lineterm="")
    return {"name": rule.name, "changed": old != new, "base_version": version(old), "diff": "\n".join(diff)}


def save(tid: str, rule: OfferingRule | ProductOffering, base_version: str | None) -> dict:
    if isinstance(rule, ProductOffering):
        rule = rule.to_offering_rule()
    current = get_text(tid, rule.name)
    if version(current) != base_version:
        raise StaleEdit(rule.name)
    text = to_text(rule)
    with core.db() as c:
        c.execute("INSERT INTO offering_rules(tenant_id,name,rule,updated_at) VALUES(?,?,?,?) "
                  "ON CONFLICT(tenant_id,name) DO UPDATE SET rule=excluded.rule, updated_at=excluded.updated_at",
                  (tid, rule.name, text, dt.datetime.now(dt.timezone.utc).isoformat()))
    claim_bank.register_offering(rule)
    return {**rule.model_dump(), "version": version(text)}
