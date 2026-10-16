-- 0003: make "ord runs 0..n-1 with no gaps" a rule the database enforces.
--
-- UNIQUE (document_id, ord) only stops two chunks sharing a slot. It happily
-- accepts 0, 1, 3, 7 - and it has no opinion about which chunk gets which
-- number, so a document can be stored in the wrong reading order and look
-- perfectly valid.
--
-- CHECK cannot express this: a CHECK sees one row and knows nothing about its
-- siblings. A CONSTRAINT TRIGGER can look at the whole set, and deferring it to
-- COMMIT is not an optimisation but a necessity - contiguity is false halfway
-- through inserting a document's chunks, so an immediate check would reject
-- every legitimate write.

CREATE FUNCTION chunks_contiguous() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    doc uuid := COALESCE(NEW.document_id, OLD.document_id); -- returns NULL if both are NULL or the first not NULL doc uuid
    n   int;
    lo  int;
    hi  int;
BEGIN
    SELECT count(*), min(ord), max(ord) INTO n, lo, hi
      FROM chunks WHERE document_id = doc;

    -- The document has no chunks: either not chunked yet, or deleted entirely.
    IF n = 0 THEN
        RETURN NULL;
    END IF;

    IF lo <> 0 OR hi <> n - 1 THEN
        RAISE EXCEPTION
            'chunks for document % must be numbered 0..% with no gaps; found % rows spanning %..%',
            doc, n - 1, n, lo, hi
            USING HINT = 'Reconcile writes a document''s complete chunk list. ' -- trigger throws DB error that code catches and invokes reconcile function for chunks order
                         'A partial write leaves holes.';
    END IF;

    RETURN NULL;
END $$;

COMMENT ON FUNCTION chunks_contiguous() IS
    'Deferred guard: every document''s chunks must form 0..n-1 with no gaps.';

-- FOR EACH ROW is required for a CONSTRAINT TRIGGER. At this corpus size the
-- repeated per-document count is immaterial; correctness is worth more than
-- the microseconds a statement-level check would save.
CREATE CONSTRAINT TRIGGER chunks_contiguous_trg
    AFTER INSERT OR UPDATE OR DELETE ON chunks
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION chunks_contiguous();
