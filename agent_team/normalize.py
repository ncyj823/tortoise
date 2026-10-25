"""Text normalisation for anything that reaches storage or a check.

LLM providers and fetched pages can hand back damaged characters. The patterns seen in
stored drafts are a replacement character followed by question marks, and dashes that do
not match the writing rules. This module repairs them in one place, and it only repairs:
it never adds a word, a number or a claim.

Every function takes and returns plain Python values, so it can run on a model dump, on a
stored package, or on a single string.
"""
import difflib
import re

from .checks import APPROVED_PLACEHOLDERS

# "\ufffd" followed by any number of question marks: what a broken multi-byte character
# looks like after one or two lossy decodes.
_MOJIBAKE = re.compile("\ufffd\\?*")

# Em dash, en dash, alone or with spaces around it.
_DASH = re.compile(r"[ \t]*[\u2014\u2013][ \t]*")

# A spaced hyphen or a spaced double hyphen, used as a dash.
_SPACED_HYPHEN = re.compile(r"[ \t]+-{1,2}[ \t]+")

# Bracketed text, skipping markdown links: [text](url).
_BRACKET = re.compile(r"\[[^\]]*\](?!\()")

_PLACEHOLDER_KEYS = {p.replace(" ", "").casefold(): p for p in APPROVED_PLACEHOLDERS}


def _repair_placeholder(match: re.Match) -> str:
    """Return the approved placeholder this bracket is a near miss of, or the bracket as is."""
    raw = match.group(0)
    key = raw.replace(" ", "").casefold()
    if key in _PLACEHOLDER_KEYS:
        return _PLACEHOLDER_KEYS[key]
    close = difflib.get_close_matches(key, list(_PLACEHOLDER_KEYS), n=1, cutoff=0.9)
    return _PLACEHOLDER_KEYS[close[0]] if close else raw


def fix_text(s: str) -> str:
    """Repair mojibake, dashes, near-miss placeholders and double spaces in one string."""
    if not isinstance(s, str):
        return s
    s = _MOJIBAKE.sub(", ", s)
    s = _DASH.sub(", ", s)
    s = _SPACED_HYPHEN.sub(", ", s)
    s = _BRACKET.sub(_repair_placeholder, s)
    s = re.sub(r",(\s*,)+", ",", s)            # "a, , b" -> "a, b"
    s = re.sub(r"(?m)^, ", "", s)              # a line that started with a dash
    s = re.sub(r",\s*([,.;:!?)])", r"\1", s)   # ", ." -> "."
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s


def normalize_deep(obj):
    """Apply fix_text to every string inside nested dicts and lists. Returns a new value;
    the input is not modified. Keys are left as they are."""
    if isinstance(obj, str):
        return fix_text(obj)
    if isinstance(obj, list):
        return [normalize_deep(x) for x in obj]
    if isinstance(obj, tuple):
        return tuple(normalize_deep(x) for x in obj)
    if isinstance(obj, dict):
        return {k: normalize_deep(v) for k, v in obj.items()}
    return obj
