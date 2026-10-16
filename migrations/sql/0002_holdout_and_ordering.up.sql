-- 0002: close the two leaks the held-out set still has, and fix chunk reordering.
--
-- 1. documents.held_out  - the database, not a JSON file, decides what is
--                          off-limits. Closes the RAG and few-shot leaks.
-- 2. DEFERRABLE unique   - lets a document's chunks be renumbered in one
--                          transaction. Without this, inserting a paragraph
--                          near the top fails whenever survivors have to shift.
-- 3. chunks_evaluable    - the relation evaluation reads. Using the wrong one
--                          is a visible mistake; forgetting a WHERE clause is not.

-- --- 1 -----------------------------------------------------------------------
ALTER TABLE documents
    ADD COLUMN held_out boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN documents.held_out IS
    'Stamped at ingestion from the G1 manifest (.doc/held_out.json). True means '
    'this source is reserved for final evaluation: never a training sample, never '
    'retrieved during evaluation, never used as a few-shot example.';

-- Held-out rows are the minority, so the index pays off on the common filter.
CREATE INDEX documents_held_out_idx ON documents (held_out);

-- --- 2 -----------------------------------------------------------------------
-- A rotation is temporarily invalid at every intermediate step: moving chunk A
-- into slot 1 collides with chunk B, which has not vacated slot 1 yet. Checking
-- per row therefore cannot succeed. Checking once at COMMIT can.
ALTER TABLE chunks
    DROP CONSTRAINT chunks_document_id_ord_key;

ALTER TABLE chunks
    ADD CONSTRAINT chunks_document_ord_uniq
    UNIQUE (document_id, ord) DEFERRABLE INITIALLY IMMEDIATE;

COMMENT ON CONSTRAINT chunks_document_ord_uniq ON chunks IS
    'DEFERRABLE so a reconcile can renumber chunks inside one transaction. '
    'Use SET CONSTRAINTS chunks_document_ord_uniq DEFERRED before a MERGE that '
    'shifts ord values; INITIALLY IMMEDIATE keeps ordinary writes strict.';

-- --- 3 -----------------------------------------------------------------------
CREATE VIEW chunks_evaluable AS
SELECT c.chunk_id,
       c.document_id,
       c.ord,
       c.text,
       c.token_count,
       c.created_at,
       d.twin,
       d.register,
       d.origin
  FROM chunks c
  JOIN documents d ON d.id = c.document_id
 WHERE d.held_out = false;

COMMENT ON VIEW chunks_evaluable IS
    'Chunks that evaluation is allowed to see. Retrieval and few-shot selection '
    'during an experiment must read this, never the chunks table, or the twin can '
    'be handed the very passage it is being asked to produce.';
