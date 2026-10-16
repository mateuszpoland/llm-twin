-- Print every variable PostgreSQL hands a PL/pgSQL trigger function.
--
-- Self-contained: builds a scratch schema, fires the trigger on INSERT,
-- UPDATE, DELETE and a 3-row INSERT, then drops everything. Touches nothing
-- real.
--
--   make trigger-vars
--   docker compose exec -T warehouse psql -U twin -d twin -f /sql/demo_trigger_variables.sql

\set ON_ERROR_STOP on
CREATE SCHEMA IF NOT EXISTS trigdemo;
SET search_path = trigdemo, public;

DROP TABLE IF EXISTS t CASCADE;
CREATE TABLE t (id int PRIMARY KEY, doc text, ord int);

CREATE OR REPLACE FUNCTION trigdemo.show_everything() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE NOTICE '--- invocation ---';
    RAISE NOTICE 'TG_OP           = %', TG_OP;            -- INSERT / UPDATE / DELETE / TRUNCATE
    RAISE NOTICE 'TG_NAME         = %', TG_NAME;          -- name of this trigger
    RAISE NOTICE 'TG_WHEN         = %', TG_WHEN;          -- BEFORE / AFTER / INSTEAD OF
    RAISE NOTICE 'TG_LEVEL        = %', TG_LEVEL;         -- ROW / STATEMENT
    RAISE NOTICE 'TG_TABLE_NAME   = %', TG_TABLE_NAME;
    RAISE NOTICE 'TG_TABLE_SCHEMA = %', TG_TABLE_SCHEMA;
    RAISE NOTICE 'TG_RELID        = %', TG_RELID;         -- internal numeric id of the table
    RAISE NOTICE 'TG_NARGS        = %', TG_NARGS;         -- how many args CREATE TRIGGER passed
    RAISE NOTICE 'TG_ARGV         = %', TG_ARGV;          -- those args, as a text array
    RAISE NOTICE 'NEW (whole row) = %', NEW;
    RAISE NOTICE 'OLD (whole row) = %', OLD;
    RAISE NOTICE 'NEW.doc = %   OLD.doc = %', NEW.doc, OLD.doc;
    RAISE NOTICE 'COALESCE(NEW.doc, OLD.doc) = %', COALESCE(NEW.doc, OLD.doc);
    RETURN NULL;   -- ignored for an AFTER trigger
END $$;

-- The 'hello' and '42' are arbitrary - they only exist to show that
-- CREATE TRIGGER can pass arguments, which arrive as TG_ARGV.
CREATE TRIGGER t_trg AFTER INSERT OR UPDATE OR DELETE ON t
FOR EACH ROW EXECUTE FUNCTION trigdemo.show_everything('hello', '42');

\echo ''
\echo '################ INSERT one row  (OLD is NULL) ################'
INSERT INTO t VALUES (1, 'docA', 0);

\echo ''
\echo '################ UPDATE  (both NEW and OLD) ################'
UPDATE t SET ord = 5 WHERE id = 1;

\echo ''
\echo '################ DELETE  (NEW is NULL) ################'
DELETE FROM t WHERE id = 1;

\echo ''
\echo '####### INSERT 3 ROWS IN ONE STATEMENT -> 3 separate invocations #######'
INSERT INTO t VALUES (10,'docB',0), (11,'docB',1), (12,'docB',2);

\echo ''
\echo '################ cleaning up ################'
DROP SCHEMA trigdemo CASCADE;
