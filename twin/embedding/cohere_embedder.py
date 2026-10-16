"""Turn chunk text into vectors with Cohere's embed API.

Two details that are easy to get wrong and expensive to discover later:

`input_type` must differ between indexing and searching. Cohere trains these
asymmetrically: a stored passage goes in as `search_document`, a question goes
in as `search_query`. Embed everything as the same type and retrieval quality
drops for no visible reason - nothing errors, the numbers just get worse.

`model` is recorded on every row. Switching models does not corrupt anything:
the new model simply has no rows yet, so every chunk reappears in the
"needs embedding" query and the old vectors sit alongside until deleted.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cohere
from loguru import logger

from twin.settings import settings

# Cohere caps texts per call. 96 is the documented maximum for the embed
# endpoint; batching at that size keeps the number of round trips minimal.
BATCH_SIZE = 96


@dataclass
class EmbedReport:
    requested: int = 0
    embedded: int = 0
    batches: int = 0
    failures: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        s = f"{self.embedded}/{self.requested} embedded in {self.batches} batches"
        return f"{s}, {len(self.failures)} failed" if self.failures else s


class CohereEmbedder:
    """Thin wrapper. Holds the model choice so callers cannot disagree about it."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        dim: int | None = None,
    ) -> None:
        key = api_key or settings.COHERE_API_KEY
        if not key:
            raise RuntimeError("COHERE_API_KEY is not set. Add it to .env (see .env.example).")
        self.model = model or settings.EMBEDDING_MODEL
        self.dim = dim or settings.EMBEDDING_DIM
        self._client = cohere.ClientV2(api_key=key)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed passages for storage. Always `search_document`."""
        return self._embed(texts, input_type="search_document")

    def embed_query(self, text: str) -> list[float]:
        """Embed a question for retrieval. Always `search_query`."""
        return self._embed([text], input_type="search_query")[0]

    def _embed(self, texts: list[str], *, input_type: str) -> list[list[float]]:
        if not texts:
            return []
        response = self._client.embed(
            model=self.model,
            input_type=input_type,
            texts=texts,
            output_dimension=self.dim,
            embedding_types=["float"],
            # Cohere rejects anything over the context window unless told what
            # to do. Chunks are ~300 words so this should never fire; END keeps
            # a stray oversized one from failing the whole batch.
            truncate="END",
        )
        vectors = response.embeddings.float_
        if vectors is None:
            raise RuntimeError("Cohere returned no float embeddings")
        if len(vectors) != len(texts):
            raise RuntimeError(f"asked for {len(texts)} embeddings, got {len(vectors)}")
        for v in vectors:
            if len(v) != self.dim:
                raise RuntimeError(f"expected dim {self.dim}, got {len(v)}")
        return vectors


def batched(items: list, size: int = BATCH_SIZE):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def embed_missing(session, embedder: CohereEmbedder, *, limit: int | None = None) -> EmbedReport:
    """Embed every chunk that has no vector for this model yet.

    Idempotent by construction: the work list is "chunks with no row in
    chunk_embeddings for this model", so a re-run after a partial failure picks
    up exactly what is left. There is no progress state to corrupt.
    """
    from sqlalchemy import text as sql

    report = EmbedReport()

    rows = session.execute(
        sql(
            """
        SELECT c.chunk_id, c.text
          FROM chunks c
         WHERE NOT EXISTS (
               SELECT 1 FROM chunk_embeddings e
                WHERE e.chunk_id = c.chunk_id AND e.model = :model)
         ORDER BY c.document_id, c.ord
         """
            + ("LIMIT :limit" if limit else "")
        ),
        {"model": embedder.model, **({"limit": limit} if limit else {})},
    ).all()

    report.requested = len(rows)
    if not rows:
        logger.info(f"nothing to embed for model {embedder.model}")
        return report

    logger.info(f"embedding {len(rows)} chunks with {embedder.model} (dim {embedder.dim})")

    for batch in batched(list(rows)):
        report.batches += 1
        try:
            vectors = embedder.embed_documents([r[1] for r in batch])
        except Exception as exc:  # one bad batch must not lose the others
            report.failures.append(f"batch {report.batches}: {exc}")
            logger.error(f"batch {report.batches} failed: {exc}")
            continue

        session.execute(
            sql("""
            INSERT INTO chunk_embeddings (chunk_id, model, dim, embedding)
            VALUES (:chunk_id, :model, :dim, :embedding)
            ON CONFLICT (chunk_id, model) DO UPDATE SET embedding = EXCLUDED.embedding,
                                                        dim = EXCLUDED.dim
            """),
            [
                {
                    "chunk_id": row[0],
                    "model": embedder.model,
                    "dim": embedder.dim,
                    "embedding": str(vector),  # pgvector accepts the '[1,2,3]' text form
                }
                for row, vector in zip(batch, vectors, strict=True)
            ],
        )
        report.embedded += len(batch)
        logger.info(f"  batch {report.batches}: {report.embedded}/{report.requested}")

    return report
