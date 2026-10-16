-- 001_core_schema.sql
--
-- The four things this schema has to make cheap:
--   1. Skip unchanged documents on a re-run          -> documents.content_hash
--   2. Diff a changed document chunk-by-chunk        -> chunks.chunk_id (content-addressed)
--   3. Never lose the ability to rebuild a past run  -> samples.valid_to (soft delete)
--   4. Keep machine-written text out of targets      -> samples.target is always verbatim human
--
-- Everything is one transaction boundary. That is the whole reason this is
-- Postgres and not Mongo + a separate vector store.
--
-- Alembic wraps this file in a transaction and records the revision, so
-- there is no BEGIN/COMMIT and no version table here.

-- ---------------------------------------------------------------------------
-- documents : raw ingested source material, one row per source file/post/chat
-- ---------------------------------------------------------------------------
CREATE TABLE documents (
    id            uuid PRIMARY KEY,

    -- which twin this feeds. Carried from ingestion so the A-vs-C decision
    -- (two adapters vs one pooled adapter) stays a training flag forever.
    twin          text NOT NULL CHECK (twin IN ('articles', 'chat')),

    -- register within the twin. The article corpus is two distinct voices;
    -- without this the model cannot be told which one to produce.
    register      text NOT NULL CHECK (register IN ('tech', 'essay', 'casual')),

    source        text NOT NULL CHECK (source IN ('local', 'medium', 'gdrive', 'telegram', 'whatsapp')),
    source_ref    text NOT NULL,          -- file path, URL, or telegram chat id

    -- provenance. Set at ingestion, never inferred later. This is the defence
    -- against the twin training on its own drafts round after round.
    origin        text NOT NULL CHECK (origin IN ('human_written', 'model_drafted_edited', 'model_drafted')),

    lang          text,                   -- 'pl' | 'en' | 'mixed'
    title         text,
    content       text NOT NULL,          -- post-redaction. Raw never lands here.

    -- per-source varying shape (telegram chat name, medium url, drive file id...)
    meta          jsonb NOT NULL DEFAULT '{}'::jsonb,

    -- sha256 over NORMALISED content. The cheap gate: if this matches the
    -- stored value, skip the document entirely and never chunk it.
    content_hash  bytea NOT NULL CHECK (octet_length(content_hash) = 32),

    fetched_at    timestamptz NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),

    UNIQUE (source, source_ref)
);

CREATE INDEX documents_twin_register_idx ON documents (twin, register);
CREATE INDEX documents_origin_idx        ON documents (origin);
CREATE INDEX documents_meta_idx          ON documents USING gin (meta);

-- ---------------------------------------------------------------------------
-- chunks : content-addressed passages
--
-- chunk_id = sha256(document_id || normalize(text)), computed app-side.
-- Identity comes from CONTENT, never position, so inserting a paragraph at the
-- top does not invalidate every chunk below it. `ord` is for reassembly and
-- display only - never for identity.
-- ---------------------------------------------------------------------------
CREATE TABLE chunks (
    chunk_id     bytea PRIMARY KEY CHECK (octet_length(chunk_id) = 32),
    document_id  uuid NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    ord          int  NOT NULL,
    text         text NOT NULL,
    token_count  int,
    created_at   timestamptz NOT NULL DEFAULT now(),

    UNIQUE (document_id, ord)
);

CREATE INDEX chunks_document_idx ON chunks (document_id);

-- ---------------------------------------------------------------------------
-- chunk_embeddings : derived, disposable, re-computable
--
-- Separate from `chunks` on purpose: embeddings are the one thing you throw
-- away and rebuild when you change embedding model. Keeping them here means
-- `TRUNCATE chunk_embeddings` is a safe operation.
--
-- 1024 dims covers multilingual-e5-large and bge-m3. A model with a different
-- dimension needs its own migration - pgvector fixes dimension per column.
-- ---------------------------------------------------------------------------
CREATE TABLE chunk_embeddings (
    chunk_id   bytea NOT NULL REFERENCES chunks (chunk_id) ON DELETE CASCADE,
    model      text  NOT NULL,          -- exact model id, so two models can coexist
    embedding  vector(1024) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (chunk_id, model)
);

CREATE INDEX chunk_embeddings_hnsw_idx
    ON chunk_embeddings USING hnsw (embedding vector_cosine_ops);

-- ---------------------------------------------------------------------------
-- samples : training pairs
--
-- `target` is ALWAYS verbatim human text. Nothing a model wrote is ever
-- allowed in this column - that is the correction to the handbook's method.
-- `prompt` may be machine-written (backtranslated instruction) because loss
-- is masked to the completion.
-- ---------------------------------------------------------------------------
CREATE TABLE samples (
    id            bigserial PRIMARY KEY,

    twin          text NOT NULL CHECK (twin IN ('articles', 'chat')),
    register      text NOT NULL CHECK (register IN ('tech', 'essay', 'casual')),
    kind          text NOT NULL CHECK (kind IN ('article_backtranslation', 'chat_reply')),

    system_prompt text,                  -- the register marker shown at train + serve time
    prompt        text NOT NULL,         -- instruction, or preceding conversation turns
    target        text NOT NULL,         -- verbatim. Never generated.

    -- provenance
    chunk_id      bytea REFERENCES chunks (chunk_id) ON DELETE SET NULL,  -- articles
    document_id   uuid  REFERENCES documents (id)    ON DELETE CASCADE,
    origin        text NOT NULL CHECK (origin IN ('human_written', 'model_drafted_edited', 'model_drafted')),
    generator     text,                  -- model that wrote `prompt`; NULL for chat pairs

    -- Assigned once, at insert, and never recomputed. A split that moves
    -- between runs makes every eval number meaningless.
    split         text NOT NULL DEFAULT 'train' CHECK (split IN ('train', 'val', 'test')),

    -- temporal. valid_to IS NULL means "current". Rows are retired, never
    -- deleted, so a past training run stays reproducible.
    valid_from    timestamptz NOT NULL DEFAULT now(),
    valid_to      timestamptz,

    CONSTRAINT samples_target_nonempty CHECK (length(btrim(target)) > 0),
    -- an article sample must trace back to a chunk; a chat sample must not
    CONSTRAINT samples_chunk_matches_kind CHECK (
        (kind = 'article_backtranslation' AND chunk_id IS NOT NULL) OR
        (kind = 'chat_reply'              AND chunk_id IS NULL)
    )
);

-- the hot path: "give me the current training set for this twin"
CREATE INDEX samples_current_idx ON samples (twin, split)
    WHERE valid_to IS NULL;
CREATE INDEX samples_chunk_idx    ON samples (chunk_id);
CREATE INDEX samples_document_idx ON samples (document_id);

-- ---------------------------------------------------------------------------
-- dataset_builds : what was actually shipped to a GPU, and where it landed
-- ---------------------------------------------------------------------------
CREATE TABLE dataset_builds (
    id            uuid PRIMARY KEY,
    twin          text NOT NULL,
    uri           text NOT NULL,          -- s3://... the GPU reads this, not the DB
    sample_count  int  NOT NULL,
    filters       jsonb NOT NULL DEFAULT '{}'::jsonb,   -- origin policy, register mix, caps
    built_at      timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- pseudonyms : the redaction map. NEVER leaves this machine.
--
-- Not published to Supabase, not in backups that leave the laptop, not in any
-- dataset. Stable across runs so the same person keeps the same pseudonym and
-- conversations stay coherent.
-- ---------------------------------------------------------------------------
CREATE TABLE pseudonyms (
    value_hash  bytea PRIMARY KEY CHECK (octet_length(value_hash) = 32),
    kind        text  NOT NULL CHECK (kind IN ('person', 'phone', 'email', 'address', 'url', 'other')),
    pseudonym   text  NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),

    UNIQUE (kind, pseudonym)
);
