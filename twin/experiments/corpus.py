"""Load the raw corpus off disk, grouped by register.

Deliberately reads source files rather than the warehouse: the style profile is
a property of what was written, and must stay runnable before any ingestion
exists. Once documents land in Postgres this gains a warehouse-backed sibling.
"""

from __future__ import annotations

import json
from pathlib import Path

DATA = Path("/app/data")
ME = "Mateusz Kowalewski"


def _flatten(text: str | list) -> str:
    """Telegram stores `text` as a string, or a list of fragments when the
    message contains links, mentions or formatting."""
    if isinstance(text, str):
        return text
    return "".join(p if isinstance(p, str) else p.get("text", "") for p in text)


def articles() -> tuple[list[str], list[str]]:
    """Return (tech, essay). Register is taken from the filename."""
    tech: list[str] = []
    essay: list[str] = []
    for path in sorted((DATA / "article_generator").glob("*.txt")):
        body = path.read_text(encoding="utf-8").strip()
        if not body:
            continue
        (tech if "tech" in path.name else essay).append(body)
    return tech, essay


def telegram_messages(author: str = ME) -> list[str]:
    """Every non-empty message written by `author`."""
    export = DATA / "chat_interface" / "result.json"
    if not export.is_file():
        return []
    payload = json.loads(export.read_text(encoding="utf-8"))
    out: list[str] = []
    for chat in payload["chats"]["list"]:
        for msg in chat.get("messages", []):
            if msg.get("type") != "message" or msg.get("from") != author:
                continue
            body = _flatten(msg.get("text", "")).strip()
            if body:
                out.append(body)
    return out
