"""Strip third-party personal data before anything reaches the database.

This sits between parsing and the warehouse. Nothing raw is stored, so there is
no later stage that could accidentally forward it to an LLM, a dataset push, or
a model checkpoint - the only copy of the real text is the export file on disk.

Names are not guessed. The exports list their own participants, so we
pseudonymise from a known roster and pattern-match only the things that have
reliable shapes: phones, emails, addresses, URLs, bank-ish numbers.

The pseudonym map lives in Postgres, keyed by a hash of the real value, so the
same person keeps the same fake name across runs and conversations stay
coherent. That table never leaves this machine.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

# Shapes with low false-positive rates. Order matters: URLs before phones, or a
# phone-like run of digits inside a URL gets mangled first.
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    # Polish postcode, but only when followed by a capitalised word - i.e. an
    # actual "80-416 Gdansk". Bare \d{2}-\d{3} eats legitimate number ranges:
    # it turned a survey's "51-200" option into [postcode] on the first run.
    (
        "postcode",
        re.compile(r"\b\d{2}-\d{3}\b(?=\s+[A-Z\u0104\u0106\u0118\u0141\u0143\u00d3\u015a\u017b\u0179])"),
    ),
    # Street line: a street word, some words, then a number (optionally 12/34).
    (
        "address",
        re.compile(
            r"\b(?:ul\.|ul\b|al\.|Aleja|Alei|ulica|plac|pl\.|os\.)\s+[^\n,]{2,40}?\s+\d+[a-zA-Z]?(?:/\d+[a-zA-Z]?)?",
            re.I,
        ),
    ),
    # IBAN-ish and long digit runs (account/card numbers).
    ("account", re.compile(r"\b[A-Z]{2}\d{2}[\s\d]{12,34}\b")),
    ("longnumber", re.compile(r"\b\d[\d\s-]{12,}\d\b")),
    # Phones: +48 prefixed, or 9 digits in 3-3-3 / 3x3 groupings.
    ("phone", re.compile(r"(?:\+\d{1,3}[\s-]?)?(?:\d{3}[\s-]?){2}\d{3}\b")),
]

# Applied to conversations only. A link in my own published article is content;
# a link in a private chat can carry a session token or an invite - the export
# contains at least one self-verify URL with a live session id in it.
CHAT_ONLY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("url", re.compile(r"https?://\S+|www\.\S+", re.I)),
]


def value_hash(value: str) -> bytes:
    """Key for the pseudonym map. Casefolded so 'Piotr' and 'piotr' agree."""
    return hashlib.sha256(value.strip().casefold().encode("utf-8")).digest()


@dataclass
class Redactor:
    """Applies redaction and records which pseudonyms it had to invent.

    `known_names` is the participant roster from the export. `pseudonyms` is the
    map loaded from the database; anything new gets added to `minted` so the
    caller can persist it in the same transaction as the documents.
    """

    known_names: set[str] = field(default_factory=set)
    pseudonyms: dict[bytes, str] = field(default_factory=dict)
    minted: dict[bytes, tuple[str, str]] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    _name_re: re.Pattern[str] | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self._build_name_pattern()

    def _build_name_pattern(self) -> None:
        # Longest first, so "Anna Kowalska" wins over "Anna".
        # 2 chars is the floor: WhatsApp renders unknown contacts as "~T".
        names = sorted((n for n in self.known_names if len(n.strip()) >= 2), key=len, reverse=True)
        if not names:
            self._name_re = None
            return
        self._name_re = re.compile(r"(?<!\w)(" + "|".join(re.escape(n) for n in names) + r")(?!\w)", re.I)

    def _bump(self, kind: str) -> None:
        self.counts[kind] = self.counts.get(kind, 0) + 1

    def pseudonym_for(self, real: str, kind: str = "person") -> str:
        """Stable fake name for a real one. Same input, same output, forever."""
        key = value_hash(real)
        if key in self.pseudonyms:
            return self.pseudonyms[key]
        if key in self.minted:
            return self.minted[key][1]
        # Numbered per kind, in first-seen order. Short and readable in a diff.
        n = sum(1 for k, _ in self.minted.values() if k == kind) + sum(
            1 for v in self.pseudonyms.values() if v.startswith(f"[{kind}-")
        )
        fake = f"[{kind}-{n + 1:02d}]"
        self.minted[key] = (kind, fake)
        return fake

    def redact(self, text: str, *, strip_urls: bool = False) -> str:
        """Everything that is not mine, replaced. My own words untouched.

        Structural patterns run FIRST. Otherwise a name inside an email address
        gets pseudonymised before the email pattern sees it, and
        "anna.k@example.com" comes out as "[person-01].[email]" - a mangled
        half-redaction that still leaks the shape of the original.
        """
        patterns = PATTERNS + (CHAT_ONLY_PATTERNS if strip_urls else [])
        for kind, pattern in patterns:

            def _mask(m: re.Match[str], kind: str = kind) -> str:
                self._bump(kind)
                return f"[{kind}]"

            text = pattern.sub(_mask, text)

        if self._name_re is not None:

            def _swap(m: re.Match[str]) -> str:
                self._bump("person")
                return self.pseudonym_for(m.group(1))

            text = self._name_re.sub(_swap, text)

        return text

    def redact_author(self, author: str, is_mine: bool) -> str:
        """Author labels get the same treatment, mine excepted."""
        if is_mine:
            return author
        self._bump("person")
        return self.pseudonym_for(author)

    def report(self) -> str:
        if not self.counts:
            return "nothing redacted"
        parts = ", ".join(f"{k}={v}" for k, v in sorted(self.counts.items()))
        return f"{parts} | {len(self.minted)} new pseudonyms"
