DELETE FROM chunk_embeddings WHERE dim <> 1024;

ALTER TABLE chunk_embeddings DROP CONSTRAINT IF EXISTS chunk_embeddings_dim_matches;
ALTER TABLE chunk_embeddings DROP CONSTRAINT IF EXISTS chunk_embeddings_pkey CASCADE;
ALTER TABLE chunk_embeddings DROP COLUMN IF EXISTS dim;

ALTER TABLE chunk_embeddings
    ALTER COLUMN embedding TYPE vector(1024) USING embedding::vector(1024);

ALTER TABLE chunk_embeddings ADD CONSTRAINT chunk_embeddings_pkey PRIMARY KEY (chunk_id, model);
CREATE INDEX chunk_embeddings_hnsw_idx ON chunk_embeddings USING hnsw (embedding vector_cosine_ops);
