-- 0004: stop hard-coding the embedding dimension.
--
-- vector(1024) was a guess made before an embedding model was chosen. Cohere
-- embed-v4.0 defaults to 1536 and supports 256/512/1024/1536, and the model
-- may change again - so the column should not encode one answer.
--
-- The catch: pgvector's HNSW index requires a fixed dimension. Dropping the
-- index is what buys the flexibility. At this corpus size that costs nothing:
-- a sequential scan over ~12k vectors is milliseconds. Add an index back when
-- the corpus is large enough to need one, and that migration will also fix the
-- dimension - by then it will be known rather than guessed.

ALTER TABLE chunk_embeddings DROP CONSTRAINT IF EXISTS chunk_embeddings_pkey CASCADE;
DROP INDEX IF EXISTS chunk_embeddings_hnsw_idx;

ALTER TABLE chunk_embeddings
    ALTER COLUMN embedding TYPE vector USING embedding::vector;

ALTER TABLE chunk_embeddings
    ADD COLUMN dim int NOT NULL DEFAULT 1536;

ALTER TABLE chunk_embeddings
    ALTER COLUMN dim DROP DEFAULT;

-- The stored vector must actually be the length the row claims. Catches a
-- truncated Matryoshka embedding written under the wrong dim.
ALTER TABLE chunk_embeddings
    ADD CONSTRAINT chunk_embeddings_dim_matches
    CHECK (vector_dims(embedding) = dim);

ALTER TABLE chunk_embeddings
    ADD CONSTRAINT chunk_embeddings_pkey PRIMARY KEY (chunk_id, model);

COMMENT ON COLUMN chunk_embeddings.dim IS
    'Dimension of this row''s vector. Redundant with vector_dims(), and that is '
    'the point: the CHECK between them catches a vector written at the wrong size.';

COMMENT ON TABLE chunk_embeddings IS
    'Derived and disposable: one row per (chunk, model). Safe to TRUNCATE and '
    'rebuild. No vector index - sequential scan is fine at this scale, and an '
    'unindexed column can hold any dimension.';
