"""Runtime security gate: an INDEPENDENT second look at scrubbed text.

The scrubber (scrubber.py) removes identifiers.  This module does not trust
it: it re-scans the finished output with its own, broader "does this still
look like PII?" patterns.  It is used as a gate, so text that fails is NEVER
stored in PostgreSQL and NEVER sent to Groq.

Design notes
  * Patterns describe the SHAPE of an identifier, not specific values, so the
    gate works on statements from banks/countries nobody has seen yet.
  * The report contains only CATEGORY COUNTS, never the matched text, so a
    failed check cannot itself spill the leaked value into a response or log.
  * The same patterns are reused by tests/test_scrubber.py (single source of truth).
"""
import re
from dataclasses import dataclass, field

LEAK_PATTERNS = {
    "email": r"[\w.+-]+@[\w-]+\.[\w.]+",
    "payment handle": r"[\w.+-]+@[A-Za-z][\w-]*\b",
    "long digits": r"(?<![\d.,])\d{8,}(?![\d]|[.,]\d)",
    "iban-like": r"\b[A-Za-z]{2}\d{2}[A-Za-z0-9]{10,}\b",
    "long mixed id": r"\b(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{12,}\b",
    "intl phone": r"(?<![\w.,])\+\d[\d\s().-]{8,}\d(?!\.\d{2}\b)",
    "ssn-like": r"\b\d{3}-\d{2}-\d{4}\b",
    "cnic-like": r"\b\d{5}-\d{7}-\d\b",
    "ipv4": r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])",
}


def find_leaks(text: str) -> dict[str, list[str]]:
    """Return {category: [matched strings]}.  For tests and debugging ONLY:
    the matched strings are the leaked values, so never send this to a client."""
    return {name: hits for name, pat in LEAK_PATTERNS.items()
            if (hits := re.findall(pat, text))}


@dataclass
class VerificationReport:
    ok: bool
    findings: dict[str, int] = field(default_factory=dict)  # category -> count (no values)


def verify_clean(text: str) -> VerificationReport:
    """True gate: ok=False if ANY pattern still matches the scrubbed text."""
    findings = {name: len(hits) for name, hits in find_leaks(text).items()}
    return VerificationReport(ok=not findings, findings=findings)
