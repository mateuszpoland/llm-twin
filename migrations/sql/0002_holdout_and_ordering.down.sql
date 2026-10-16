DROP VIEW IF EXISTS chunks_evaluable;

ALTER TABLE chunks DROP CONSTRAINT IF EXISTS chunks_document_ord_uniq;
ALTER TABLE chunks ADD CONSTRAINT chunks_document_id_ord_key UNIQUE (document_id, ord);

DROP INDEX IF EXISTS documents_held_out_idx;
ALTER TABLE documents DROP COLUMN IF EXISTS held_out;
