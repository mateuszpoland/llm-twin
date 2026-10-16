"""Numeric description of a body of writing.

The yardstick for experiment G. Every feature here is cheap, model-free and
interpretable: if a generated sample drifts on these, you can say exactly how.
Classic stylometry - function-word rates and sentence rhythm carry authorship
signal far more reliably than vocabulary does.
"""

from __future__ import annotations

import re
import statistics as stats
from dataclasses import dataclass, field

SENT_SPLIT = re.compile(r"[.!?]+(?:\s|$)")
WORD = re.compile(r"\b[\w'’-]+\b", re.UNICODE)
PL_DIACRITICS = re.compile(r"[ąćęłńóśżźĄĆĘŁŃÓŚŻŹ]")
CYRILLIC = re.compile(r"[а-яёА-ЯЁ]")

# Function words carry style, not topic. Split by language so code-switching
# shows up rather than averaging away.
EN_FUNCTION = {
    "the",
    "a",
    "an",
    "and",
    "but",
    "or",
    "if",
    "of",
    "to",
    "in",
    "on",
    "for",
    "that",
    "this",
    "it",
    "is",
    "are",
    "was",
    "be",
    "you",
    "your",
    "i",
    "my",
    "we",
    "not",
    "no",
    "so",
    "just",
    "what",
    "how",
    "why",
    "because",
    "as",
}
PL_FUNCTION = {
    "i",
    "a",
    "ale",
    "lub",
    "czy",
    "z",
    "do",
    "na",
    "w",
    "o",
    "że",
    "to",
    "jest",
    "są",
    "był",
    "być",
    "ty",
    "twój",
    "ja",
    "mój",
    "my",
    "nie",
    "tak",
    "już",
    "co",
    "jak",
    "dlaczego",
    "bo",
    "się",
    "tylko",
}
COARSE = re.compile(
    r"\b(fuck\w*|shit\w*|crap|damn|ass|bullshit|kurw\w+|chuj\w*|jeban\w+|pierdol\w+|gówn\w+)\b",
    re.IGNORECASE,
)
SECOND_PERSON = re.compile(r"\b(you|your|yours|ty|ciebie|twój|twoja|twoje|wasz\w*)\b", re.IGNORECASE)
SENT_INITIAL_CONJ = re.compile(r"(?:^|[.!?]\s+)(But|And|So|Bo|Ale|I|A)\b")


@dataclass
class StyleProfile:
    label: str
    n_texts: int
    n_words: int
    sent_len_mean: float
    sent_len_median: float
    sent_len_stdev: float
    word_len_mean: float
    mattr: float  # lexical diversity, length-normalised
    per_1k: dict[str, float] = field(default_factory=dict)
    function_word_rate: dict[str, float] = field(default_factory=dict)

    def rows(self) -> list[tuple[str, float]]:
        out = [
            ("texts", self.n_texts),
            ("words", self.n_words),
            ("sentence length mean", self.sent_len_mean),
            ("sentence length median", self.sent_len_median),
            ("sentence length stdev", self.sent_len_stdev),
            ("word length mean", self.word_len_mean),
            ("lexical diversity (MATTR)", self.mattr),
        ]
        out += [(f"per 1k: {k}", v) for k, v in self.per_1k.items()]
        return out


def _mattr(words: list[str], window: int = 100) -> float:
    """Moving-average type-token ratio - comparable across different lengths,
    unlike raw TTR which falls as a text gets longer."""
    if len(words) < window:
        return len(set(words)) / len(words) if words else 0.0
    ratios = [
        len(set(words[i : i + window])) / window
        for i in range(0, len(words) - window + 1, max(1, window // 4))
    ]
    return sum(ratios) / len(ratios)


def profile(texts: list[str], label: str) -> StyleProfile:
    joined = "\n".join(texts)
    words_raw = WORD.findall(joined)
    words = [w.lower() for w in words_raw]
    n = len(words) or 1

    sent_lens: list[int] = []
    for t in texts:
        for s in SENT_SPLIT.split(t):
            wc = len(WORD.findall(s))
            if wc:
                sent_lens.append(wc)
    sent_lens = sent_lens or [0]

    per_1k = {
        "commas": joined.count(",") / n * 1000,
        "dashes": len(re.findall(r"[-–—]", joined)) / n * 1000,
        "ellipses": len(re.findall(r"\.{3}|…", joined)) / n * 1000,
        "questions": joined.count("?") / n * 1000,
        "exclamations": joined.count("!") / n * 1000,
        "ALLCAPS words": sum(1 for w in words_raw if len(w) > 2 and w.isupper()) / n * 1000,
        "coarse words": len(COARSE.findall(joined)) / n * 1000,
        "second person": len(SECOND_PERSON.findall(joined)) / n * 1000,
        "sentence-initial conj.": len(SENT_INITIAL_CONJ.findall(joined)) / n * 1000,
        "polish diacritics": len(PL_DIACRITICS.findall(joined)) / n * 1000,
        "cyrillic": len(CYRILLIC.findall(joined)) / n * 1000,
    }

    wordset = set(words)
    fw = {
        "english": sum(words.count(w) for w in EN_FUNCTION & wordset) / n * 1000,
        "polish": sum(words.count(w) for w in PL_FUNCTION & wordset) / n * 1000,
    }

    return StyleProfile(
        label=label,
        n_texts=len(texts),
        n_words=len(words),
        sent_len_mean=stats.fmean(sent_lens),
        sent_len_median=stats.median(sent_lens),
        sent_len_stdev=stats.stdev(sent_lens) if len(sent_lens) > 1 else 0.0,
        word_len_mean=stats.fmean([len(w) for w in words]) if words else 0.0,
        mattr=_mattr(words),
        per_1k=per_1k,
        function_word_rate=fw,
    )
