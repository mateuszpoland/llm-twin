"""Reconcile a document's chunks against what is stored.

One MERGE per document, expressing all three outcomes at once:

    kept    - same content hash, maybe a different position. Nothing recomputed.
    added   - new content. Needs embedding, and later an instruction.
    removed - gone from the document. Its embedding cascades away with it.

We never compute the transition ("chunk B moved from 1 to 3"). We hand over the
complete, freshly computed chunk list and let the database work out the diff.
That is what makes `ord` contiguous by construction: it is enumerate() over a
fresh list, so it cannot have holes.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from twin.chunking.splitter import Chunk


@dataclass
class ReconcileResult:
    document_id: str
    kept: int = 0
    added: int = 0
    removed: int = 0

    @property
    def touched(self) -> bool:
        return bool(self.added or self.removed)


# A temporary table per document keeps the MERGE readable and lets Postgres see
# real row counts when planning. ON COMMIT DROP means no cleanup to forget.
_STAGE = text("""
CREATE TEMP TABLE incoming (
    chunk_id    bytea PRIMARY KEY,
    ord         int  NOT NULL,
    text        text NOT NULL,
    token_count int
) ON COMMIT DROP
""")

_FILL = text("""
INSERT INTO incoming (chunk_id, ord, text, token_count)
VALUES (:chunk_id, :ord, :text, :token_count)
""")

# WHEN NOT MATCHED BY SOURCE requires PostgreSQL 17. On 15/16 the DELETE would
# have to be a separate statement before the merge.
_MERGE = text("""
MERGE INTO chunks AS t
USING incoming AS s
   ON t.chunk_id = s.chunk_id AND t.document_id = :doc
 WHEN MATCHED AND t.ord IS DISTINCT FROM s.ord THEN
      UPDATE SET ord = s.ord
 WHEN NOT MATCHED BY TARGET THEN
      INSERT (chunk_id, document_id, ord, text, token_count)
      VALUES (s.chunk_id, :doc, s.ord, s.text, s.token_count)
 WHEN NOT MATCHED BY SOURCE AND t.document_id = :doc THEN
      DELETE
""")


def reconcile(session: Session, document_id: str, chunks: list[Chunk]) -> ReconcileResult:
    """Make the stored chunk set equal the given one, cheaply.

    Must run inside a transaction. Both guards are deferred for its duration:
    a reordering is invalid at every intermediate step and only the final state
    can be checked.
    """
    result = ReconcileResult(document_id=document_id)

    stored: set[bytes] = {
        bytes(r[0])
        for r in session.execute(
            text("SELECT chunk_id FROM chunks WHERE document_id = :doc"), {"doc": document_id}
        ).all()
    }
    incoming = {c.chunk_id(document_id): c for c in chunks}

    result.kept = len(stored & incoming.keys())
    result.added = len(incoming.keys() - stored)
    result.removed = len(stored - incoming.keys())

    session.execute(text("SET CONSTRAINTS chunks_document_ord_uniq, chunks_contiguous_trg DEFERRED"))
    session.execute(_STAGE)
    if incoming:
        session.execute(
            _FILL,
            [
                {
                    "chunk_id": cid,
                    "ord": c.ord,
                    "text": c.text,
                    "token_count": c.words,  # word count stands in until a tokeniser is chosen
                }
                for cid, c in incoming.items()
            ],
        )
    session.execute(_MERGE, {"doc": document_id})
    session.execute(text("DROP TABLE IF EXISTS incoming"))

    return result


def chunks_needing_embedding(session: Session, model: str) -> int:
    """How many chunks have no embedding for this model yet."""
    return session.execute(
        text("""
        SELECT count(*) FROM chunks c
        WHERE NOT EXISTS (
            SELECT 1 FROM chunk_embeddings e
             WHERE e.chunk_id = c.chunk_id AND e.model = :model
        )
        """),
        {"model": model},
    ).scalar_one()


def verify_contiguous(session: Session) -> list[tuple[str, int, int, int]]:
    """Belt and braces: the trigger should make this impossible to fail."""
    return [
        (str(r[0]), r[1], r[2], r[3])
        for r in session.execute(
            text("""
            SELECT document_id, count(*), min(ord), max(ord)
              FROM chunks GROUP BY document_id
            HAVING min(ord) <> 0 OR max(ord) <> count(*) - 1
            """)
        ).all()
    ]


__all__ = [
    "ReconcileResult",
    "bindparam",
    "chunks_needing_embedding",
    "reconcile",
    "verify_contiguous",
]
