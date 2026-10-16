"""The data pipeline, as ZenML sees it: ingest -> chunk -> embed.

Deliberately thin. Every step calls a function that already exists and already
works from the CLI; nothing moved in here. ZenML's job is orchestration and
record-keeping, not logic.

What this buys, honestly:
  - a durable run history, which terminal scrollback is not
  - one command for the whole chain, in the right order
  - the dashboard showing what ran, with what settings, and what came out

What it does NOT buy: caching. ZenML caches per step, keyed on step inputs -
and it cannot hash "the contents of a database". Our skip logic is finer
anyway: an unchanged document is skipped by content hash, an unchanged passage
keeps its chunk id, an already-embedded chunk is simply absent from the work
query. Step caching is therefore switched off; leaving it on would only
mislead.
"""

from __future__ import annotations

from typing import Annotated

from loguru import logger
from sqlalchemy import text
from zenml import pipeline, step

from twin.chunking import splitter
from twin.db import session
from twin.embedding.cohere_embedder import CohereEmbedder, embed_missing
from twin.experiments import held_out as g1
from twin.ingest import sources
from twin.settings import settings
from twin.warehouse import chunks as wchunks
from twin.warehouse import documents as warehouse

HOLDOUT_PATH = "/app/.doc/held_out.json"


@step(enable_cache=False)
def ingest_documents() -> Annotated[dict, "ingest_report"]:
    """Parse every source, redact PII, upsert into the warehouse."""
    from pathlib import Path

    path = Path(HOLDOUT_PATH)
    if not path.exists():
        raise RuntimeError(
            "No held-out set frozen. Run 'twin freeze-holdout' first - ingesting "
            "before the split exists risks training on evaluation data."
        )
    manifest = g1.load(path)

    docs = sources.all_sources()
    roster = sources.participants(docs)

    def held_out_check(doc) -> bool:
        return g1.is_held_out(manifest, source=doc.source.value, source_ref=doc.source_ref)

    with session() as s:
        report = warehouse.ingest(s, docs, roster=roster, held_out_check=held_out_check)

    return {
        "frozen_at": manifest["frozen_at"],
        "documents_seen": len(docs),
        "inserted": report.inserted,
        "updated": report.updated,
        "unchanged": report.unchanged,
        "held_out": report.held_out,
        "pseudonyms_minted": report.pseudonyms_minted,
        "redactions": report.redactions,
    }


@step(enable_cache=False)
def chunk_articles(
    ingest_report: dict,
    keep_code: bool = False,
) -> Annotated[dict, "chunk_report"]:
    """Split article documents into chunks and reconcile them.

    Takes `ingest_report` purely to make the dependency explicit to ZenML -
    chunking must not run before ingestion. The value itself is unused.
    """
    del ingest_report

    totals = {"documents": 0, "changed": 0, "added": 0, "removed": 0, "kept": 0}

    with session() as s:
        rows = s.execute(
            text("SELECT id, source_ref, content FROM documents WHERE twin = 'articles' ORDER BY source_ref")
        ).all()

        for doc_id, ref, content in rows:
            result = wchunks.reconcile(s, str(doc_id), splitter.split(content, keep_code=keep_code))
            totals["documents"] += 1
            totals["added"] += result.added
            totals["removed"] += result.removed
            totals["kept"] += result.kept
            if result.touched:
                totals["changed"] += 1
                logger.info(f"  {ref}: +{result.added} -{result.removed} ={result.kept}")

        non_contiguous = wchunks.verify_contiguous(s)

    totals["keep_code"] = keep_code
    totals["non_contiguous"] = len(non_contiguous)
    if non_contiguous:
        raise RuntimeError(f"chunks are not contiguous: {non_contiguous}")
    return totals


@step(enable_cache=False)
def embed_chunks(
    chunk_report: dict,
    skip_if_no_key: bool = True,
) -> Annotated[dict, "embed_report"]:
    """Embed chunks that have no vector yet for the configured model."""
    del chunk_report

    if not settings.COHERE_API_KEY:
        if skip_if_no_key:
            logger.warning("COHERE_API_KEY not set - skipping the embedding step")
            return {"skipped": True, "reason": "no COHERE_API_KEY"}
        raise RuntimeError("COHERE_API_KEY is not set")

    embedder = CohereEmbedder()
    with session() as s:
        report = embed_missing(s, embedder)

    return {
        "skipped": False,
        "model": embedder.model,
        "dim": embedder.dim,
        "requested": report.requested,
        "embedded": report.embedded,
        "batches": report.batches,
        "failures": report.failures,
    }


@pipeline(enable_cache=False)
def data_pipeline(keep_code: bool = False) -> None:
    """ingest -> chunk -> embed, in that order, recorded as one run."""
    ingested = ingest_documents()
    chunked = chunk_articles(ingest_report=ingested, keep_code=keep_code)
    embed_chunks(chunk_report=chunked)
