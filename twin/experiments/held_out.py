"""G1 - choose what never gets trained on, and freeze that choice.

Identity, not content. A held-out item is named by something that survives
editing: an article's filename, a conversation's numeric id. Re-chunk, rewrite
or re-segment as much as you like - everything produced from a held-out source
stays held out, because we check where it came from, not what it looks like.

Picking by chunk position ("chunk 2") or by content hash both break the moment
you edit a file: the position points at different text, and the hash points at
nothing. Either way the drawer silently changes and the evaluation stops
meaning anything, with no error to warn you.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from twin.experiments import corpus

# --- the rule. Changing any of these invalidates an existing freeze. ---------

HOLD_OUT_PERCENT = 20
"""Roughly what share of sources to hold back."""

SALT = "twin-g1-v1"
"""Mixed into the hash. Fixed forever: changing it reshuffles every choice."""

DOMINANT_SHARE = 0.20
"""A conversation holding at least this share of my messages is too big to
hold out whole - losing it would cost most of the training data. Those get a
time slice held out instead."""

TIME_TAIL_PERCENT = 15
"""For a dominant conversation: hold out its most recent messages. Training
then only ever sees older material, which is also how it will be used."""

EXCLUDED_CHAT_TYPES = {"saved_messages"}
"""Notes to self. No reply partner, so no reply pairs - not train, not test."""


def bucket(identity: str) -> int:
    """Turn an identity into a stable number 0-99.

    Same name always lands in the same bucket, on any machine, forever. No
    randomness, so a freeze can be recomputed and checked rather than trusted.
    """
    digest = hashlib.sha256(f"{SALT}:{identity}".encode()).hexdigest()
    return int(digest[:8], 16) % 100


def pick(identities: list[str], percent: int = HOLD_OUT_PERCENT) -> list[str]:
    """Choose a fixed share of a group, by rank rather than per-item chance.

    Testing each item independently ("is its bucket < 20?") has wild variance
    on small groups - 8 items can easily yield 5 hits instead of 2. Ranking by
    bucket and taking the lowest slice keeps the count exact while staying
    fully deterministic: the same group always yields the same choice.
    """
    if not identities:
        return []
    n = max(1, round(len(identities) * percent / 100))
    return sorted(sorted(identities), key=bucket)[:n]


@dataclass
class Manifest:
    frozen_at: str
    rule: dict = field(default_factory=dict)
    held_out: dict = field(default_factory=dict)
    excluded: dict = field(default_factory=dict)
    counts: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(
            {
                "experiment": "G1",
                "frozen_at": self.frozen_at,
                "rule": self.rule,
                "excluded": self.excluded,
                "held_out": self.held_out,
                "counts": self.counts,
            },
            indent=2,
            ensure_ascii=False,
        )


def _article_sources() -> list[tuple[str, str]]:
    """(filename, register) for every non-empty article."""
    out = []
    for path in sorted((corpus.DATA / "article_generator").glob("*.txt")):
        if not path.read_text(encoding="utf-8").strip():
            continue
        out.append((path.name, "tech" if "tech" in path.name else "essay"))
    return out


def _chat_sources() -> list[dict]:
    """One entry per conversation that contains messages I wrote."""
    export = corpus.DATA / "chat_interface" / "result.json"
    payload = json.loads(export.read_text(encoding="utf-8"))
    out = []
    for chat in payload["chats"]["list"]:
        mine = [
            m
            for m in chat.get("messages", [])
            if m.get("type") == "message"
            and m.get("from") == corpus.ME
            and corpus._flatten(m.get("text", "")).strip()
        ]
        if not mine:
            continue
        out.append(
            {
                "id": str(chat.get("id")),
                "name": chat.get("name") or "(no name)",
                "type": chat.get("type"),
                "my_messages": len(mine),
                "dates": sorted(m["date"] for m in mine if m.get("date")),
            }
        )
    return out


def build() -> Manifest:
    articles = _article_sources()
    chats = _chat_sources()

    # --- articles: whole files, balanced across the two registers -----------
    held_articles: dict[str, list[str]] = {}
    per_register: dict[str, int] = {}
    for register in ("tech", "essay"):
        names = [n for n, r in articles if r == register]
        per_register[register] = len(names)
        chosen = pick([f"article:{n}" for n in names])
        held_articles[register] = sorted(c.removeprefix("article:") for c in chosen)

    # --- chat: whole conversations, except ones too big to lose -------------
    usable = [c for c in chats if c["type"] not in EXCLUDED_CHAT_TYPES]
    total_mine = sum(c["my_messages"] for c in usable)

    small = [c for c in usable if (c["my_messages"] / total_mine if total_mine else 0) < DOMINANT_SHARE]
    chosen_small = set(pick([f"chat:{c['id']}" for c in small]))

    held_convs, time_tails = [], []
    for chat in usable:
        share = chat["my_messages"] / total_mine if total_mine else 0
        if share >= DOMINANT_SHARE:
            # Too big to hold out whole. Hold back its most recent slice.
            dates = chat["dates"]
            cut = dates[int(len(dates) * (1 - TIME_TAIL_PERCENT / 100))]
            time_tails.append(
                {
                    "id": chat["id"],
                    "name": chat["name"],
                    "held_out_from": cut,
                    "my_messages_held": sum(1 for d in dates if d >= cut),
                    "my_messages_total": chat["my_messages"],
                    "reason": f"holds {share:.0%} of my messages - too large to hold out whole",
                }
            )
        elif f"chat:{chat['id']}" in chosen_small:
            held_convs.append({"id": chat["id"], "name": chat["name"], "my_messages": chat["my_messages"]})

    excluded = [
        {"id": c["id"], "name": c["name"], "type": c["type"], "reason": "no reply partner"}
        for c in chats
        if c["type"] in EXCLUDED_CHAT_TYPES
    ]

    held_msgs = sum(c["my_messages"] for c in held_convs) + sum(t["my_messages_held"] for t in time_tails)

    return Manifest(
        frozen_at=datetime.now(UTC).isoformat(timespec="seconds"),
        rule={
            "identity": "articles by filename; conversations by numeric chat id",
            "articles": (
                f"per register, rank files by sha256('{SALT}:article:<name>') and hold out "
                f"the lowest {HOLD_OUT_PERCENT}% (minimum one)"
            ),
            "chat": (
                f"rank non-dominant conversations by sha256('{SALT}:chat:<id>') and hold out "
                f"the lowest {HOLD_OUT_PERCENT}% (minimum one)"
            ),
            "chat_dominant": (
                f"a conversation holding >= {DOMINANT_SHARE:.0%} of my messages instead has "
                f"its most recent {TIME_TAIL_PERCENT}% held out by date"
            ),
            "excluded": f"chat types {sorted(EXCLUDED_CHAT_TYPES)} are neither trained on nor evaluated",
            "salt": SALT,
        },
        excluded={"chats": excluded},
        held_out={
            "articles": held_articles,
            "chat_conversations": held_convs,
            "chat_time_tails": time_tails,
        },
        counts={
            "articles_total": per_register,
            "articles_held": {k: len(v) for k, v in held_articles.items()},
            "chat_conversations_total": len(usable),
            "chat_conversations_held": len(held_convs),
            "my_messages_total": total_mine,
            "my_messages_held": held_msgs,
            "my_messages_held_percent": round(100 * held_msgs / total_mine, 1) if total_mine else 0,
        },
    )


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def is_held_out(manifest: dict, *, source: str, source_ref: str, date: str | None = None) -> bool:
    """The check ingestion will call for every document and message.

    `source_ref` is the identity: a filename for articles, a chat id for chat.
    `date` only matters for a conversation that was split by time.
    """
    held = manifest["held_out"]
    if source in {"local", "medium", "gdrive"}:
        return source_ref in held["articles"]["tech"] or source_ref in held["articles"]["essay"]
    if source in {"telegram", "whatsapp"}:
        if any(c["id"] == source_ref for c in held["chat_conversations"]):
            return True
        for tail in held["chat_time_tails"]:
            if tail["id"] == source_ref and date is not None:
                return date >= tail["held_out_from"]
    return False
