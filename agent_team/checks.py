"""Deterministic copy checks for the content agent.

They run in code after the LLM claim checker, on every sentence and visual, and its labels cannot
override them. Each issue is {"where", "claim", "reason"}; content.py adds the part name.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from . import offerings
from .offerings import ProductOffering

APPROVED_PLACEHOLDERS = ("[CTA]", "[PRICE]", "[WHERE TO GET IT]", "[PLATFORMS]", "[WHAT THE USER SEES]",
                         "[DATA HANDLING]")
REQUIRED_RATIOS = {"hook": ("9:16", "4:5"), "short": ("9:16", "4:5"), "explainer": ("16:9",)}
UI_PLACEHOLDER = "[WHAT THE USER SEES]"


# ---------------------------------------------------------------- Content Verifier & Results
class CheckResult(BaseModel):
    """Encapsulates the structured outcome of a deterministic content verification audit."""
    passed: bool = Field(..., description="Indicates whether all validation checks passed successfully.")
    forbidden_terms_found: List[str] = Field(default_factory=list, description="Blacklisted terminology detected in the text.")
    unauthorized_claims: List[str] = Field(default_factory=list, description="Extracted claims failing strict claim-bank validation.")
    pricing_violations: List[str] = Field(default_factory=list, description="Pricing figures found that violate established boundaries.")
    details: str = Field(..., description="Human-readable summary of the verification audit.")


class ContentVerifier:
    """Deterministic assertion engine for validating generated content against the Protected Claim Bank."""
    
    def __init__(self, offering_id: str = "core_agent_suite_v1") -> None:
        self.offering_id = offering_id

    def verify_content(self, text: str, extracted_claims: Optional[List[str]] = None) -> CheckResult:
        """Executes deterministic assertion checks on draft content:
        1. Forbidden terms scan.
        2. Price boundary enforcement.
        3. Strict claim-bank matching for extracted claims.
        """
        offering = offerings.claim_bank.get_offering(self.offering_id)
        if not offering:
            return CheckResult(
                passed=False,
                details=f"Validation failed: Offering ID '{self.offering_id}' not found in registry."
            )

        # 1. Scan forbidden terms
        forbidden_found = offerings.claim_bank.scan_forbidden_terms(self.offering_id, text)

        # 2. Enforce price boundaries
        pricing_violations = self._check_pricing_boundaries(text, offering.pricing_boundaries)

        # 3. Strict claim-bank matching
        unauthorized: List[str] = []
        if extracted_claims:
            for claim in extracted_claims:
                if not offerings.claim_bank.validate_claim(self.offering_id, claim):
                    unauthorized.append(claim)

        passed = len(forbidden_found) == 0 and len(pricing_violations) == 0 and len(unauthorized) == 0

        details = (
            "All verification checks passed successfully." 
            if passed 
            else "Validation errors detected: rule violations found in draft content."
        )

        return CheckResult(
            passed=passed,
            forbidden_terms_found=forbidden_found,
            unauthorized_claims=unauthorized,
            pricing_violations=pricing_violations,
            details=details
        )

    def _check_pricing_boundaries(self, text: str, boundaries: Dict[str, float]) -> List[str]:
        """Scans text for currency patterns and verifies them against permitted boundaries."""
        violations: List[str] = []
        price_pattern = r'\$([0-9]+\.?[0-9]*)'
        matches = re.findall(price_pattern, text)
        
        allowed_prices = set(boundaries.values())
        for match in matches:
            try:
                val = float(match)
                if val not in allowed_prices:
                    violations.append(f"Unauthorized price detected: ${val}. Permitted boundaries: {sorted(list(allowed_prices))}")
            except ValueError:
                continue
        return violations


# Global singleton verifier instance
verifier = ContentVerifier()


# ---------------------------------------------------------------- splitting copy into lines
_SENT = re.compile(r"(?<=[.!?])\s+")


def _sentences(s: str) -> list[str]:
    return [x.strip() for x in _SENT.split(s or "") if x.strip()]


def lines(part: dict) -> list[dict]:
    """Every sentence (kind "text"), on-screen text ("ost") and visual direction ("visual") in a part."""
    out = []

    def add(where, kind, s, split=True):
        for x in (_sentences(s) if split else [str(s or "").strip()]):
            if x:
                out.append({"where": where, "kind": kind, "text": x})

    cs = part.get("campaign_summary")
    if isinstance(cs, dict) and "core_message" in cs:
        add("summary core message", "text", cs.get("core_message"))
        add("summary call to action", "text", cs.get("call_to_action"))
    for name in ("hook", "short", "explainer"):
        v = part.get(name)
        if not isinstance(v, dict) or "scenes" not in v:
            continue
        for i, sc in enumerate(v.get("scenes") or [], 1):
            add(f"{name} scene {i} voiceover", "text", sc.get("line"))
            add(f"{name} scene {i} on-screen text", "ost", sc.get("on_screen_text"), split=False)
            add(f"{name} scene {i} visual", "visual", sc.get("visual"), split=False)
        add(f"{name} caption", "text", v.get("caption"))
        for i, s in enumerate(v.get("shot_list") or [], 1):
            add(f"{name} shot {i}", "visual", s, split=False)
    for q in part.get("posts") or [] if isinstance(part.get("posts"), list) else []:
        add(f"{q.get('platform')} post", "text", q.get("text"))
        add(f"{q.get('platform')} post notes", "text", q.get("notes"))
    a = part.get("article")
    if isinstance(a, dict) and "draft" in a:
        add("article title", "text", a.get("title"))
        for i, o in enumerate(a.get("outline") or [], 1):
            add(f"article outline {i}", "text", o)
        add("article draft", "text", a.get("draft"))
    return out


# ---------------------------------------------------------------- 1. cross-offering leakage
_WORD = re.compile(r"[a-z]+")
# Generic words in the other offerings' descriptions that are not capabilities (roles, the problem, filler).
_NOT_CAPABILITIES = set("""
about action actions adds after applies before being blocks check checks company control controls cost costs
creates data each enabling enterprise enterprises error errors execution generative help human humans just large
leakage level manage moment needing occur organization organizations platform prevent public quality risk risks
risky scale security seeking sensitive teams their them they this usage with without operations centers
administrators deploying models model alerts integrated
""".split())


def stem(w: str) -> str:
    w = w.lower()
    if w.endswith("ies") and len(w) > 5:
        w = w[:-3] + "y"
    for suf in ("ations", "ation", "ions", "ion", "ings", "ing", "ors", "or", "ers", "er", "ence", "ent",
                "ed", "es", "s", "ive", "ly"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[:-len(suf)]
    return w


def _terms(text: str) -> list[str]:
    return [w for w in _WORD.findall((text or "").lower()) if len(w) >= 4 and w not in _NOT_CAPABILITIES]


def cross_offering_terms(product, rules: list, brain: dict) -> dict[str, tuple[str, str]]:
    """{stem: (word, offering)} for capability words of every OTHER offering in the brief, minus any word
    the product's own facts use. Built automatically, so new offerings are covered without a list."""
    own = {stem(w) for w in _WORD.findall(" ".join(product.facts).lower())}
    out = {}
    sources = [(r.name, " ".join(r.facts)) for r in rules if r.name != product.name]
    for o in brain.get("offerings") or []:
        if isinstance(o, dict) and o.get("name") and o["name"].lower() != product.name.lower():
            sources.append((o["name"], f"{o.get('description', '')} {o.get('target_customer', '')}"))
    diffs = (brain.get("positioning") or {}).get("differentiators") or []
    sources += [("the company's other differentiators", d) for d in diffs]
    for name, text in sources:
        for w in _terms(text):
            s = stem(w)
            if s not in own and s not in out:
                out[s] = (w, name)
    return out


def leakage(ls: list[dict], terms: dict[str, tuple[str, str]], product_name: str) -> list[dict]:
    issues = []
    for x in ls:
        for w in _WORD.findall(x["text"].lower()):
            hit = terms.get(stem(w))
            if hit:
                issues.append({**_ref(x), "reason": f"rule: '{w}' is a capability of {hit[1]}, not {product_name}"})
                break
    return issues


# ---------------------------------------------------------------- 2. claim bank
def canon(s: str) -> str:
    """Normalise only what is invisible when reading: Unicode forms, hyphen and quote variants, spacing."""
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"[\u2010-\u2015\u2212]", "-", s)
    s = s.replace("\u2018", "'").replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
    return re.sub(r"\s+", " ", s).strip()


def claim_bank(ls: list[dict], labels: dict[int, str], products: list) -> list[dict]:
    """Every PRODUCT line must be a claim bank sentence, word for word; a product visual must be exactly
    [WHAT THE USER SEES]. A line is PRODUCT if the checker says so OR it names a product (the checker cannot
    clear that). A line the checker did not label is flagged too."""
    bank = {canon(c) for p in products for c in p.claim_bank}
    names = [p.name.lower() for p in products]
    issues = []
    for i, x in enumerate(ls, 1):
        t = canon(x["text"])
        if t in bank or t == UI_PLACEHOLDER:
            continue
        label = labels.get(i)
        names_product = any(n in t.lower() for n in names)
        if label is None:
            reason = "the checker did not label this line"
        elif label != "PRODUCT" and not names_product:
            continue
        elif x["kind"] == "visual":
            reason = f"shows the product; a product visual must be exactly {UI_PLACEHOLDER}"
        elif not bank:
            reason = "describes the product, and the product has no claim bank sentences yet"
        else:
            reason = "describes the product but is not word for word from the claim bank"
        issues.append({**_ref(x), "reason": f"rule: {reason}"})
    return issues


# ---------------------------------------------------------------- 3. forbidden terms & pricing
def forbidden_terms(ls: list[dict], products: list) -> list[dict]:
    """Every sentence, on-screen text or caption must not contain blacklisted terms."""
    issues = []
    for x in ls:
        text_lower = x["text"].lower()
        for p in products:
            terms = getattr(p, "forbidden_terms", None) or getattr(p, "never_claim_terms", [])
            for term in terms:
                if term.lower() in text_lower:
                    issues.append({**_ref(x), "reason": f"rule: contains forbidden term '{term}' for {p.name}"})
    return issues


def pricing(ls: list[dict], products: list) -> list[dict]:
    """Pricing numbers mentioned must match allowed pricing boundaries for the product."""
    issues = []
    price_pattern = re.compile(r'\$([0-9]+\.?[0-9]*)')
    for x in ls:
        matches = price_pattern.findall(x["text"])
        if not matches:
            continue
        for p in products:
            bounds = getattr(p, "pricing_boundaries", {})
            if not bounds:
                continue
            allowed = set(bounds.values())
            for match in matches:
                try:
                    val = float(match)
                    if val not in allowed:
                        issues.append({**_ref(x), "reason": f"rule: unauthorized price ${val} for {p.name}; permitted: {sorted(allowed)}"})
                except ValueError:
                    continue
    return issues


# ---------------------------------------------------------------- 4. invented assets
_ASSET = re.compile(r"\b(one[- ]?pagers?|check ?lists?|white ?papers?|e-?books?|webinars?|data ?sheets?|"
                    r"case stud(?:y|ies)|(?:free|download|get|grab|read|our|the|start an?|request an?)\s+"
                    r"(?:[\w-]+\s+){0,2}(?:guides?|trials?))\b", re.I)


def assets(ls: list[dict], facts: str) -> list[dict]:
    issues = []
    for x in ls:
        if x["kind"] == "visual":          # a checklist icon in a shot is not an offer
            continue
        for m in _ASSET.finditer(x["text"]):
            if m.group(0).lower() not in facts.lower():
                issues.append({**_ref(x), "reason": f"rule: offers '{m.group(0)}', which no fact mentions"})
                break
    return issues


# ---------------------------------------------------------------- placeholders and formats
_BRACKET = re.compile(r"\[[^\]]*\](?!\()")      # skip markdown links: [text](url)


def placeholders(ls: list[dict]) -> list[dict]:
    issues = []
    for x in ls:
        bad = [b for b in _BRACKET.findall(x["text"]) if b not in APPROVED_PLACEHOLDERS]
        if bad:
            issues.append({**_ref(x), "reason": f"rule: {', '.join(bad)} is not an approved placeholder "
                                                f"({', '.join(APPROVED_PLACEHOLDERS)}), nothing goes inside the brackets"})
    return issues


def formats(part: dict) -> list[dict]:
    issues = []
    for name, need in REQUIRED_RATIOS.items():
        v = part.get(name)
        if isinstance(v, dict) and "aspect_ratios" in v:
            missing = [r for r in need if r not in v["aspect_ratios"]]
            if missing:
                issues.append({"where": f"{name} aspect ratios", "claim": ", ".join(v["aspect_ratios"]),
                               "reason": f"rule: the {name} video must include {' and '.join(need)}; "
                                         f"missing {', '.join(missing)}"})
    return issues


def _ref(x: dict) -> dict:
    return {"where": x["where"], "claim": x["text"]}


def language(ls: list[dict], lang: str) -> list[dict]:
    """Every sentence, on-screen text and visual must be in the workspace language."""
    from .core import language_issue
    return [{**_ref(x), "reason": f"rule: not in {lang}: {issue}"}
            for x in ls if (issue := language_issue(x["text"], lang))]


def run_all(part: dict, products: list, leak_terms: dict, facts: str, lang: str = "English") -> list[dict]:
    """Every deterministic check on one part."""
    ls = lines(part)
    issues = placeholders(ls) + assets(ls, facts) + formats(part) + language(ls, lang)
    for p in products:
        issues += leakage(ls, leak_terms.get(p.name, {}), p.name)
    return issues


# ---------------------------------------------------------------- open questions
_QSTOP = set("what which who how the a an for to of or and should be used is are exact specific e g eg "
             "do does we our your there any this that with in on use replace placeholder".split())


def dedupe_questions(qs: list[str], threshold: float = 0.6) -> list[str]:
    """Drop questions that ask the same thing in other words (word overlap at or above the threshold)."""
    out, seen = [], []
    for q in qs:
        w = {x for x in _WORD.findall(q.lower()) if x not in _QSTOP}
        if any(w and s and len(w & s) / len(w | s) >= threshold for s in seen):
            continue
        seen.append(w)
        out.append(q)
    return out
