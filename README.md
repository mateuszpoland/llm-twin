# LLM Twin

Two personal models, built alongside *LLM Engineer's Handbook*:

| Twin | Trained on | Serves as |
|---|---|---|
| `twin-articles` | published + local long-form writing | drafts articles in my style |
| `twin-chat` | WhatsApp / Telegram conversations | replies in my style |

Both are LoRA adapters over **one shared base model**. Training runs on a rented
GPU; serving will run on AWS. The only thing passing between them is a ~100 MB
adapter file in object storage — so the GPU provider stays swappable and never
touches the database.

## Current state

Infrastructure and schema. No ingestion or training yet.

**Plan, decisions and experiment results live in [`.doc/PLAN.md`](.doc/PLAN.md)** —
the living document tracking roadmap, locked decisions, measurements, tooling and
deployments. [`.doc/explainers/`](.doc/explainers/) holds plain-English
walkthroughs written for no assumed knowledge, and
[`.doc/dev_process.pdf`](.doc/dev_process.pdf) is the original design note.

---

## Quick start

```bash
cp .env.example .env     # first time only
make up                  # start everything
make migrate             # apply database migrations
make ping                # confirm it worked
```

`make` with no target lists everything available.

| Service | URL | Credentials |
|---|---|---|
| ZenML dashboard | <http://localhost:8237> | `default` / `zenml` |
| Adminer (DB viewer) | <http://localhost:8080> | see below |
| Postgres | `localhost:5432` | `twin` / `twin` / db `twin` |

---

## Connecting to Postgres

### Adminer — the DB viewer, in the browser

Open <http://localhost:8080> and fill in:

| Field | Value |
|---|---|
| System | **PostgreSQL** |
| Server | `warehouse` |
| Username | `twin` |
| Password | `twin` |
| Database | `twin` |

**Server is `warehouse`, not `localhost`** — Adminer runs inside the Docker
network, so it reaches Postgres by its service name. This is the single most
common mistake here.

From there you can browse tables, run SQL, and export results.

### psql — the terminal

```bash
make psql                       # interactive session
```

Or one-off queries:

```bash
docker compose exec -T warehouse psql -U twin -d twin -c "\dt"
docker compose exec -T warehouse psql -U twin -d twin -c "SELECT count(*) FROM documents;"
```

Useful psql commands once inside: `\dt` list tables · `\d documents` describe a
table · `\di` list indexes · `\dx` list extensions · `\q` quit.

### From a host GUI (DBeaver, DataGrip, TablePlus)

Postgres is published on the host, so connect to **`localhost:5432`**, user
`twin`, password `twin`, database `twin`. Note the difference from Adminer:
host tools use `localhost`, containers use `warehouse`.

### From Python

`twin/db.py` builds the URL from environment variables and hands out sessions:

```python
from sqlalchemy import text
from twin.db import session

with session() as s:                      # commits on exit, rolls back on error
    n = s.execute(text("SELECT count(*) FROM chunks")).scalar_one()
```

---

## Migrations (Alembic)

Schema changes are versioned SQL files. Alembic owns ordering, the transaction,
and the revision record; the DDL stays readable as plain `.sql`.

```
migrations/
├── versions/0001_core_schema.py     # revision: calls run_sql()
├── sql/0001_core_schema.up.sql      # the actual DDL
├── sql/0001_core_schema.down.sql    # the reverse
├── sql_revision.py                  # run_sql() helper
└── env.py                           # reads credentials from the environment
```

### Everyday commands

```bash
make migrate           # apply everything pending
make revision          # what is applied now, and full history
make migrate-down      # roll back one step
```

### Adding a migration

```bash
make migration m="add register index"
```

That scaffolds `migrations/versions/<hash>_add_register_index.py`. Then:

1. Write `migrations/sql/<hash>_add_register_index.up.sql` and `.down.sql`
2. Point the revision at them:

```python
from migrations.sql_revision import run_sql

def upgrade() -> None:
    run_sql("<hash>_add_register_index.up.sql")

def downgrade() -> None:
    run_sql("<hash>_add_register_index.down.sql")
```

3. `make migrate`

**Always write the `.down.sql`.** Alembic cannot infer it from raw SQL, and a
migration you cannot reverse is one you cannot safely experiment with.

### Seeing that it worked

```bash
make ping
```

```
connected: warehouse:5432/twin
PostgreSQL 17.11 (Debian 17.11-1.pgdg12+2)
  extension pgcrypto 1.3
  extension unaccent 1.1
  extension vector 0.8.6
  migration revision: 0001_core_schema
```

Or open Adminer and look at the tables directly.

### Starting over

```bash
docker compose down -v      # DELETES all data
make up && make migrate
```

---

## Linting

Ruff handles both linting and formatting, inside the container:

```bash
make lint     # check only
make fmt      # apply formatting
make check    # lint + formatting check, writes nothing (use in CI)
```

Configured in `pyproject.toml`: line length 110, rules `E`, `F`, `I`, `UP`, `B`.

To get live diagnostics in Zed, ruff also has to exist on the **host**:

```bash
uv tool install ruff
```

---

## Editor (Zed)

`~/.config/zed/settings.json` auto-installs `postgres-language-server`,
`postgres-context-server` and `sql` on next launch — schema-aware SQL
completion, highlighting, and schema context for the agent.

Zed has **no database viewer**; that is an open feature request, not a shipped
feature. Adminer covers it instead.

---

## Schema

| Table | Holds |
|---|---|
| `documents` | Raw ingested sources. `content_hash` is the skip-gate for re-runs. |
| `chunks` | Content-addressed passages. `chunk_id = sha256(doc_id ‖ normalized text)` — identity from content, never position, so an inserted paragraph doesn't invalidate everything below it. |
| `chunk_embeddings` | Derived and disposable, one row per (chunk, model). Safe to truncate and rebuild. |
| `samples` | Training pairs. `target` is **always verbatim human text**. Retired via `valid_to`, never deleted, so past runs stay reproducible. |
| `dataset_builds` | What was shipped to a GPU, and the URI it landed at. |
| `pseudonyms` | Redaction map. **Never leaves this machine.** |

Three tags travel on every row: `twin` (articles/chat), `register`
(tech/essay/casual), and `origin` (human_written / model_drafted_edited /
model_drafted).

---

## Data & privacy

`data/` and `_chat_*.txt` are gitignored. The chat exports contain third-party
personal data — names, addresses, phone numbers. Redaction happens immediately
after parsing, before anything reaches the warehouse, an external API, or a
model checkpoint. The `pseudonyms` table stays local, permanently.
