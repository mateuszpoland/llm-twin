import json
from datetime import UTC, datetime
from pathlib import Path

import typer
from loguru import logger
from sqlalchemy import text

from twin.chunking import splitter
from twin.db import session
from twin.embedding.cohere_embedder import CohereEmbedder, embed_missing
from twin.experiments import corpus
from twin.experiments import held_out as g1
from twin.experiments.style_profile import profile as build_profile
from twin.ingest import sources
from twin.settings import settings
from twin.warehouse import chunks as wchunks
from twin.warehouse import documents as warehouse

RESULTS = Path("/app/.doc/experiments")

app = typer.Typer(help="LLM Twin operational CLI.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """Keep subcommand dispatch even while only one command exists."""


@app.command()
def ping() -> None:
    """Check the warehouse is reachable and report what is installed."""
    with session() as s:
        version = s.execute(text("SELECT version()")).scalar_one()
        exts = s.execute(text("SELECT extname, extversion FROM pg_extension ORDER BY extname")).all()
        applied = (
            s.execute(
                text(
                    "SELECT version_num FROM alembic_version WHERE to_regclass('alembic_version') IS NOT NULL"
                )
            )
            .scalars()
            .all()
        )

    logger.info(f"connected: {settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}")
    logger.info(version.split(" on ")[0])
    for name, ver in exts:
        logger.info(f"  extension {name} {ver}")
    logger.info(f"  migration revision: {applied[0] if applied else '(none applied)'}")


if __name__ == "__main__":
    app()


@app.command()
def profile(
    out: str = typer.Option(str(RESULTS), help="Directory for result files."),
) -> None:
    """G2: profile my real writing, per register, and persist the result.

    Model-free stylometry - reruns cheaply and deterministically, so the
    numbers can be regenerated and diffed rather than taken on trust.
    """
    tech, essay = corpus.articles()
    chat = corpus.telegram_messages()

    if not (tech or essay or chat):
        raise typer.BadParameter("No corpus found under /app/data - is the volume mounted?")

    profiles = [
        build_profile(tech, "articles/tech"),
        build_profile(essay, "articles/essay"),
        build_profile(chat, "chat/casual"),
    ]

    keys = [k for k, _ in profiles[0].rows()]
    width = max(len(k) for k in keys) + 2
    header = f"{'':<{width}}" + "".join(f"{p.label:>18}" for p in profiles)
    lines = [header, "-" * len(header)]
    for i, key in enumerate(keys):
        cells = []
        for p in profiles:
            v = p.rows()[i][1]
            cells.append(f"{v:>18,.0f}" if key in ("texts", "words") else f"{v:>18.2f}")
        lines.append(f"{key:<{width}}" + "".join(cells))
    for lang in ("english", "polish"):
        cells = "".join(f"{p.function_word_rate[lang]:>18.1f}" for p in profiles)
        lines.append(f"{'fn-words/1k ' + lang:<{width}}" + cells)
    table = "\n".join(lines)
    print(table)

    generated = datetime.now(UTC).isoformat(timespec="seconds")
    payload = {
        "experiment": "G2",
        "generated_at": generated,
        "profiles": [
            {
                "label": p.label,
                "n_texts": p.n_texts,
                "n_words": p.n_words,
                "sent_len_mean": p.sent_len_mean,
                "sent_len_median": p.sent_len_median,
                "sent_len_stdev": p.sent_len_stdev,
                "word_len_mean": p.word_len_mean,
                "mattr": p.mattr,
                "per_1k": p.per_1k,
                "function_word_rate": p.function_word_rate,
            }
            for p in profiles
        ],
    }

    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "g2_style_profile.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (out_dir / "g2_style_profile.txt").write_text(
        f"G2 style profile\ngenerated: {generated}\nregenerate: make profile\n\n{table}\n",
        encoding="utf-8",
    )
    logger.info(f"wrote {out_dir}/g2_style_profile.json and .txt")


HOLDOUT_PATH = Path("/app/.doc/held_out.json")


@app.command("freeze-holdout")
def freeze_holdout(
    force: bool = typer.Option(False, "--force", help="Overwrite an existing freeze. Ruins comparability."),
) -> None:
    """G1: choose what is never trained on, and write it down permanently.

    Refuses to overwrite. That refusal is the whole point - a split that can be
    silently regenerated is not frozen, and every score measured against it
    stops being comparable to the ones before.
    """
    if HOLDOUT_PATH.exists() and not force:
        existing = g1.load(HOLDOUT_PATH)
        logger.error(f"already frozen at {existing['frozen_at']} - refusing to overwrite")
        logger.error(f"  {HOLDOUT_PATH}")
        logger.error("  re-freezing invalidates every score measured so far. Pass --force only")
        logger.error("  if you accept starting the evaluation history over.")
        raise typer.Exit(code=1)

    manifest = g1.build()
    HOLDOUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    HOLDOUT_PATH.write_text(manifest.to_json(), encoding="utf-8")
    logger.info(f"froze the held-out set at {manifest.frozen_at} -> {HOLDOUT_PATH}")
    _show(g1.load(HOLDOUT_PATH))


@app.command()
def holdout() -> None:
    """Show what is currently held out."""
    if not HOLDOUT_PATH.exists():
        logger.error(f"nothing frozen yet - run 'twin freeze-holdout' ({HOLDOUT_PATH})")
        raise typer.Exit(code=1)
    _show(g1.load(HOLDOUT_PATH))


def _show(m: dict) -> None:
    c = m["counts"]
    print(f"\nfrozen at {m['frozen_at']}\n")
    print("ARTICLES held out (whole files)")
    for register, names in m["held_out"]["articles"].items():
        print(f"  {register:<6} {len(names)} of {c['articles_total'][register]}")
        for n in names:
            print(f"           - {n}")
    print("\nCHAT held out (whole conversations)")
    for conv in m["held_out"]["chat_conversations"]:
        print(f"  - {conv['name']:<22} id={conv['id']:<12} {conv['my_messages']:>5} of my messages")
    if m["held_out"]["chat_time_tails"]:
        print("\nCHAT held out (most recent slice - too big to hold out whole)")
        for t in m["held_out"]["chat_time_tails"]:
            print(f"  - {t['name']:<22} id={t['id']:<12} from {t['held_out_from']}")
            print(f"    {t['my_messages_held']} of {t['my_messages_total']} messages | {t['reason']}")
    if m["excluded"]["chats"]:
        print("\nEXCLUDED entirely (neither trained on nor evaluated)")
        for e in m["excluded"]["chats"]:
            print(f"  - {e['name']:<22} id={e['id']:<12} {e['type']} | {e['reason']}")
    print(
        f"\nmy messages held out: {c['my_messages_held']} of "
        f"{c['my_messages_total']} ({c['my_messages_held_percent']}%)"
    )


@app.command()
def ingest() -> None:
    """Parse every source, redact PII, and load it into the warehouse.

    Safe to re-run: documents whose content hash is unchanged are skipped
    entirely, and changed ones are updated in place keeping their id.
    """
    if not HOLDOUT_PATH.exists():
        logger.error("no held-out set frozen - run 'twin freeze-holdout' first.")
        logger.error("ingesting before the split is frozen risks training on evaluation data.")
        raise typer.Exit(code=1)

    manifest = g1.load(HOLDOUT_PATH)
    logger.info(f"held-out set frozen at {manifest['frozen_at']}")

    docs = sources.all_sources()
    roster = sources.participants(docs)
    logger.info(f"participant roster: {len(roster)} names to pseudonymise")

    def held_out_check(doc) -> bool:
        return g1.is_held_out(manifest, source=doc.source.value, source_ref=doc.source_ref)

    with session() as s:
        report = warehouse.ingest(s, docs, roster=roster, held_out_check=held_out_check)

    print()
    print(f"  inserted   {report.inserted}")
    print(f"  updated    {report.updated}")
    print(f"  unchanged  {report.unchanged}   (content hash matched - no work done)")
    print(f"  held out   {report.held_out}")
    print(f"  pseudonyms {report.pseudonyms_minted} new")
    if report.redactions:
        print("  redacted   " + ", ".join(f"{k}={v}" for k, v in sorted(report.redactions.items())))


@app.command()
def chunk(
    keep_code: bool = typer.Option(False, "--keep-code", help="Keep code blocks in chunks."),
) -> None:
    """Split article documents into chunks and reconcile them into the warehouse.

    Chat is not chunked yet - its unit is a reply pair, which depends on
    segmentation rules that are still open.

    Re-running is cheap: unchanged passages keep their chunk id, so they are
    neither re-stored nor re-embedded.
    """
    totals = {"kept": 0, "added": 0, "removed": 0, "docs": 0, "touched": 0}

    with session() as s:
        rows = s.execute(
            text("SELECT id, source_ref, content FROM documents WHERE twin = 'articles' ORDER BY source_ref")
        ).all()
        logger.info(f"chunking {len(rows)} article documents (keep_code={keep_code})")

        for doc_id, ref, content in rows:
            pieces = splitter.split(content, keep_code=keep_code)
            r = wchunks.reconcile(s, str(doc_id), pieces)
            totals["docs"] += 1
            totals["kept"] += r.kept
            totals["added"] += r.added
            totals["removed"] += r.removed
            if r.touched:
                totals["touched"] += 1
                logger.info(f"  {ref:<24} +{r.added} -{r.removed} ={r.kept}")

        bad = wchunks.verify_contiguous(s)

    print()
    print(f"  documents      {totals['docs']}  ({totals['touched']} changed)")
    print(f"  chunks added   {totals['added']}")
    print(f"  chunks removed {totals['removed']}")
    print(f"  chunks kept    {totals['kept']}   (no re-embedding needed)")
    if bad:
        print(f"  !! NON-CONTIGUOUS: {bad}")
    else:
        print("  ord contiguous  verified")


@app.command()
def embed(
    limit: int = typer.Option(0, help="Embed at most this many chunks. 0 = all."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report what would be embedded, call nothing."),
) -> None:
    """Embed chunks that have no vector yet for the configured model.

    Safe to re-run and safe to interrupt: the work list is derived from which
    chunks lack a row, so a second run does exactly what is left.
    """
    model = settings.EMBEDDING_MODEL

    with session() as s:
        pending = s.execute(
            text("""
            SELECT count(*) FROM chunks c
             WHERE NOT EXISTS (SELECT 1 FROM chunk_embeddings e
                                WHERE e.chunk_id = c.chunk_id AND e.model = :model)
            """),
            {"model": model},
        ).scalar_one()
        total = s.execute(text("SELECT count(*) FROM chunks")).scalar_one()

    print(f"  model      {model}  (dim {settings.EMBEDDING_DIM})")
    print(f"  chunks     {total}")
    print(f"  to embed   {pending}")

    if dry_run:
        print("  dry run - nothing called")
        return
    if not pending:
        print("  nothing to do")
        return
    if not settings.COHERE_API_KEY:
        logger.error("COHERE_API_KEY is not set. Copy the entry from .env.example into .env.")
        raise typer.Exit(code=1)

    embedder = CohereEmbedder()
    with session() as s:
        report = embed_missing(s, embedder, limit=limit or None)

    print()
    print(f"  embedded   {report.embedded}")
    print(f"  batches    {report.batches}")
    for f in report.failures:
        print(f"  FAILED     {f}")
