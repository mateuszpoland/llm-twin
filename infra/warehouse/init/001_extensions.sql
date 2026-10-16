-- Runs once, on first initialisation of an empty data directory.
-- Extensions only. The schema itself is applied by migrations, not here,
-- so it stays reviewable and re-runnable.

CREATE EXTENSION IF NOT EXISTS vector;     -- pgvector: embedding column type + HNSW
CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- digest() for content-addressed chunk ids
CREATE EXTENSION IF NOT EXISTS unaccent;   -- PL/EN text normalisation for lexical search
