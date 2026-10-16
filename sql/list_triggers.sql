-- Every trigger defined in this database, and the source of the functions
-- behind them.
--
--   make triggers
--   docker compose exec -T warehouse psql -U twin -d twin -f /sql/list_triggers.sql
--
-- NOT t.tgisinternal hides the triggers PostgreSQL creates for itself to
-- enforce foreign keys. Without that filter this is mostly noise.

\echo '=== triggers ==='
SELECT n.nspname                        AS schema,
       c.relname                        AS "table",
       t.tgname                         AS trigger,
       p.proname                        AS function,
       t.tgdeferrable                   AS deferrable,
       t.tginitdeferred                 AS initially_deferred,
       CASE WHEN t.tgconstraint <> 0 THEN 'constraint trigger' ELSE 'plain trigger' END AS kind
FROM pg_trigger t
JOIN pg_class     c ON c.oid = t.tgrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_proc      p ON p.oid = t.tgfoid
WHERE NOT t.tgisinternal
ORDER BY n.nspname, c.relname, t.tgname;

\echo ''
\echo '=== full CREATE TRIGGER statement for each ==='
SELECT pg_get_triggerdef(t.oid) || ';' AS definition
FROM pg_trigger t
JOIN pg_class c ON c.oid = t.tgrelid
WHERE NOT t.tgisinternal
ORDER BY c.relname, t.tgname;

\echo ''
\echo '=== source of each trigger function ==='
SELECT pg_get_functiondef(p.oid) AS source
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE p.prorettype = 'trigger'::regtype
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
ORDER BY p.proname;

\echo ''
\echo '=== deferrable CONSTRAINTS (not triggers) ==='
SELECT conrelid::regclass AS "table",
       conname            AS constraint_name,
       contype            AS type,   -- u = unique, p = primary key, f = foreign key, c = check
       condeferrable      AS deferrable,
       condeferred        AS initially_deferred
FROM pg_constraint
WHERE connamespace = 'public'::regnamespace
  AND condeferrable
ORDER BY conrelid::regclass::text, conname;
