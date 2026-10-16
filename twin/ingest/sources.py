"""Turn each raw source into ParsedDocument objects.

One document per article file, one document per conversation. A conversation is
the unit because its numeric id is the identity the G1 manifest holds and the
thing that survives re-segmenting messages later.

Chunking is not done here - that is a separate stage, and for chat it depends on
segmentation rules not yet settled.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from loguru import logger

from twin.ingest.models import Message, Origin, ParsedDocument, Register, Source, Twin

DATA = Path("/app/data")
ME = "Mateusz Kowalewski"

# WhatsApp: [26/07/2025, 16:46:41] Author: text
WA_LINE = re.compile(r"^\[(\d{2}/\d{2}/\d{4}),\s*(\d{2}:\d{2}:\d{2})\]\s*([^:]{1,60}):\s*(.*)$")
# Lines the export adds that are not anybody's writing.
WA_NOISE = re.compile(
    r"(Messages and calls are end-to-end encrypted|image omitted|video omitted"
    r"|audio omitted|sticker omitted|document omitted|GIF omitted|This message was deleted"
    r"|created this group|You were added|changed the subject|joined using this group)",
    re.I,
)


def _flatten(text: str | list | None) -> str:
    """Telegram stores `text` as a list of fragments when it contains a link,
    a mention or formatting. Flattening is not optional: 682 messages in the
    current export are list-shaped and would otherwise read as empty."""
    if text is None:
        return ""
    if isinstance(text, str):
        return text
    return "".join(p if isinstance(p, str) else p.get("text", "") for p in text)


def articles() -> list[ParsedDocument]:
    """One document per non-empty article file. Register from the filename."""
    out: list[ParsedDocument] = []
    for path in sorted((DATA / "article_generator").glob("*.txt")):
        body = path.read_text(encoding="utf-8").strip()
        if not body:
            logger.debug(f"skipping empty {path.name}")
            continue
        register = Register.TECH if "tech" in path.name else Register.ESSAY
        first = next((ln.strip() for ln in body.splitlines() if ln.strip() and ln.strip() != "---"), None)
        out.append(
            ParsedDocument(
                twin=Twin.ARTICLES,
                register=register,
                source=Source.LOCAL,
                source_ref=path.name,
                origin=Origin.HUMAN,
                title=first[:200] if first else None,
                content=body,
                lang="en",
                meta={"bytes": len(body.encode()), "words": len(body.split())},
            )
        )
    return out


def telegram() -> list[ParsedDocument]:
    """One document per conversation, keyed on the numeric chat id."""
    export = DATA / "chat_interface" / "result.json"
    if not export.is_file():
        logger.warning(f"no telegram export at {export}")
        return []

    payload = json.loads(export.read_text(encoding="utf-8"))
    out: list[ParsedDocument] = []

    for chat in payload["chats"]["list"]:
        chat_id = str(chat.get("id"))
        chat_type = chat.get("type") or "unknown"
        messages: list[Message] = []

        for m in chat.get("messages", []):
            if m.get("type") != "message":
                continue  # service entries: joins, pins, calls
            body = _flatten(m.get("text")).strip()
            if not body:
                continue  # media-only
            author = m.get("from") or "(unknown)"
            raw_date = m.get("date")
            messages.append(
                Message(
                    author=author,
                    text=body,
                    sent_at=datetime.fromisoformat(raw_date) if raw_date else None,
                    is_mine=author == ME,
                )
            )

        if not any(m.is_mine for m in messages):
            continue  # nothing of mine to learn from

        out.append(
            ParsedDocument(
                twin=Twin.CHAT,
                register=Register.CASUAL,
                source=Source.TELEGRAM,
                source_ref=chat_id,
                origin=Origin.HUMAN,
                title=chat.get("name") or None,
                content="\n".join(f"{m.author}: {m.text}" for m in messages),
                meta={
                    "chat_type": chat_type,
                    "messages": len(messages),
                    "mine": sum(1 for m in messages if m.is_mine),
                },
                messages=messages,
            )
        )
    return out


def whatsapp() -> list[ParsedDocument]:
    """One document per exported chat file.

    WhatsApp exports carry no chat id, so the filename is the identity anchor -
    the same choice as for articles, for the same reason.
    """
    out: list[ParsedDocument] = []
    for path in sorted((DATA / "chat_interface").glob("_chat_*.txt")):
        messages: list[Message] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.replace("‎", "").strip()
            if not line or WA_NOISE.search(line):
                continue
            m = WA_LINE.match(line)
            if not m:
                continue  # continuation of a multi-line message; skipped for now
            date_s, time_s, author, body = m.groups()
            if not body.strip():
                continue
            try:
                sent = datetime.strptime(f"{date_s} {time_s}", "%d/%m/%Y %H:%M:%S")
            except ValueError:
                sent = None
            messages.append(
                Message(
                    author=author.strip(),
                    text=body.strip(),
                    sent_at=sent,
                    is_mine=author.strip() == "Mateusz",
                )
            )

        if not any(m.is_mine for m in messages):
            continue

        out.append(
            ParsedDocument(
                twin=Twin.CHAT,
                register=Register.CASUAL,
                source=Source.WHATSAPP,
                source_ref=path.name,
                origin=Origin.HUMAN,
                content="\n".join(f"{m.author}: {m.text}" for m in messages),
                meta={
                    "messages": len(messages),
                    "mine": sum(1 for m in messages if m.is_mine),
                },
                messages=messages,
            )
        )
    return out


def participants(documents: list[ParsedDocument]) -> set[str]:
    """Every name that appears as an author, minus mine.

    This is the roster the redactor pseudonymises from - known names, not
    guesswork. Mine is excluded: my own name in my own writing is not
    third-party data, and masking it would corrupt the style signal.
    """
    names: set[str] = set()
    for doc in documents:
        for m in doc.messages:
            if not m.is_mine:
                names.add(m.author)
        if doc.title and doc.twin == Twin.CHAT:
            names.add(doc.title)
    names.discard(ME)
    names.discard("Mateusz")
    return {n for n in names if n and not n.startswith("(")}


def all_sources() -> list[ParsedDocument]:
    docs = articles() + telegram() + whatsapp()
    logger.info(
        f"parsed {len(docs)} documents: "
        f"{sum(1 for d in docs if d.twin == Twin.ARTICLES)} articles, "
        f"{sum(1 for d in docs if d.source == Source.TELEGRAM)} telegram, "
        f"{sum(1 for d in docs if d.source == Source.WHATSAPP)} whatsapp"
    )
    return docs
