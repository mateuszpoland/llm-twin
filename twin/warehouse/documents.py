"""Write parsed documents into Postgres.

SQLAlchemy Core, not the ORM. The work here is set-shaped - upsert many rows,
compare hashes in bulk - which is what Core expresses directly and what the
ORM's per-object change tracking fights against.

Everything for one run happens in a single transaction: the pseudonym map and
the documents that depend on it commit together, or neither does. A half-written
run that left pseudonyms without their documents would hand out the same fake
name to a different person on the next run.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import MetaData, Table, bindparam, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from twin.ingest.models import ParsedDocument
from twin.ingest.redact import Redactor

_meta = MetaData()


def _table(name: str, conn) -> Table:
    """Reflect the real schema instead of restating it in Python.

    The migrations are the single definition of the tables. Declaring them again
    here would create a second truth that drifts the moment a migration lands.
    """
    if name not in _meta.tables:
        Table(name, _meta, autoload_with=conn)
    return _meta.tables[name]


@dataclass
class LoadReport:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    held_out: int = 0
    pseudonyms_minted: int = 0
    redactions: dict[str, int] = field(default_factory=dict)

    def __str__(self) -> str:
        return (
            f"{self.inserted} inserted, {self.updated} updated, "
            f"{self.unchanged} unchanged (hash matched), {self.held_out} flagged held-out"
        )


def load_pseudonyms(session: Session) -> dict[bytes, str]:
    """The existing map, so the same person keeps the same fake name."""
    t = _table("pseudonyms", session.connection())
    rows = session.execute(select(t.c.value_hash, t.c.pseudonym)).all()
    return {bytes(h): p for h, p in rows}


def save_pseudonyms(session: Session, minted: dict[bytes, tuple[str, str]]) -> int:
    """Persist newly invented pseudonyms. Never overwrite an existing one."""
    if not minted:
        return 0
    t = _table("pseudonyms", session.connection())
    rows = [{"value_hash": k, "kind": kind, "pseudonym": fake} for k, (kind, fake) in minted.items()]
    stmt = pg_insert(t).values(rows).on_conflict_do_nothing(index_elements=["value_hash"])
    session.execute(stmt)
    return len(rows)


def redact_document(doc: ParsedDocument, redactor: Redactor) -> ParsedDocument:
    """Redact a document in place of its raw self.

    Messages are redacted individually - author label and body - and the stored
    `content` is rebuilt from the redacted messages. Running a regex over the
    joined blob instead would leave author labels untouched, because the names
    there are structure rather than prose.
    """
    is_chat = doc.twin.value == "chat"
    if doc.messages:
        for m in doc.messages:
            m.author = redactor.redact_author(m.author, m.is_mine)
            m.text = redactor.redact(m.text, strip_urls=is_chat)
        content = "\n".join(f"{m.author}: {m.text}" for m in doc.messages)
    else:
        content = redactor.redact(doc.content, strip_urls=is_chat)

    return doc.model_copy(
        update={
            "content": content,
            "title": redactor.redact(doc.title, strip_urls=is_chat) if doc.title else None,
        }
    )


def upsert(
    session: Session,
    documents: list[ParsedDocument],
    *,
    held_out_check,
) -> LoadReport:
    """Insert new documents, update changed ones, skip unchanged ones.

    `held_out_check(doc) -> bool` comes from the G1 manifest. The answer is
    stored on the row so every later query asks the database rather than the
    manifest file - which is what closes the retrieval and few-shot leaks.
    """
    report = LoadReport()
    conn = session.connection()
    t = _table("documents", conn)

    # One round trip for every (source, source_ref) we are about to touch.
    keys = [(d.source.value, d.source_ref) for d in documents]
    existing = {
        (s, r): bytes(h)
        for s, r, h in session.execute(
            select(t.c.source, t.c.source_ref, t.c.content_hash).where(t.c.source.in_({k[0] for k in keys}))
        ).all()
    }

    now = datetime.now(UTC)
    to_write = []

    for doc in documents:
        key = (doc.source.value, doc.source_ref)
        incoming_hash = doc.hash

        if existing.get(key) == incoming_hash:
            report.unchanged += 1
            continue  # the skip gate: nothing changed, do no work at all

        held = held_out_check(doc)
        if held:
            report.held_out += 1
        if key in existing:
            report.updated += 1
        else:
            report.inserted += 1

        to_write.append(
            {
                "id": uuid.uuid4(),
                "twin": doc.twin.value,
                "register": doc.register.value,
                "source": doc.source.value,
                "source_ref": doc.source_ref,
                "origin": doc.origin.value,
                "lang": doc.lang,
                "title": doc.title,
                "content": doc.content,
                "meta": doc.meta,
                "content_hash": incoming_hash,
                "held_out": held,
                "fetched_at": now,
                "updated_at": now,
            }
        )

    if to_write:
        stmt = pg_insert(t).values(to_write)
        # Re-ingesting an existing source updates it in place, keeping its id so
        # chunks and samples that reference it stay attached.
        stmt = stmt.on_conflict_do_update(
            index_elements=["source", "source_ref"],
            set_={
                c: stmt.excluded[c]
                for c in (
                    "twin",
                    "register",
                    "origin",
                    "lang",
                    "title",
                    "content",
                    "meta",
                    "content_hash",
                    "held_out",
                    "fetched_at",
                    "updated_at",
                )
            },
        )
        session.execute(stmt)

    logger.info(f"documents: {report}")
    return report


def ingest(
    session: Session,
    documents: list[ParsedDocument],
    *,
    roster: set[str],
    held_out_check,
) -> LoadReport:
    """Parse -> redact -> store, as one transaction."""
    redactor = Redactor(known_names=roster, pseudonyms=load_pseudonyms(session))
    redacted = [redact_document(d, redactor) for d in documents]

    report = upsert(session, redacted, held_out_check=held_out_check)
    report.pseudonyms_minted = save_pseudonyms(session, redactor.minted)
    report.redactions = dict(redactor.counts)
    logger.info(f"redaction: {redactor.report()}")
    return report


__all__ = ["LoadReport", "bindparam", "ingest", "redact_document", "upsert"]
