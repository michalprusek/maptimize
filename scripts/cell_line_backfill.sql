-- One-off, additive: assign the U2OS cell line to the experiments that predate
-- the `cell_lines` table.
--
-- NOT run at startup, deliberately. `seed_default_data()` inserts U2OS only when
-- `cell_lines` is empty -- the same "never resurrect a row the lab deleted"
-- rule the PTM vocabulary follows -- and a data backfill on every boot would
-- silently re-stamp rows someone cleared on purpose. Fresh databases therefore
-- get the row from DEFAULT_CELL_LINES and nothing else; an existing database
-- gets its experiments filled in once, here.
--
-- `experiments.cell_line_id` and the `cell_lines` table itself are created at
-- startup by ensure_schema_updates() and create_all, so RUN THIS AFTER the
-- backend has been restarted on the new image, not before.
--
--   docker exec -i maptimize-db     psql -U maptimize -d maptimize < scripts/cell_line_backfill.sql
--   docker exec -i maptimize-dev-db psql -U maptimize -d maptimize < scripts/cell_line_backfill.sql
--
-- ⚠️ Without this, psql walks past a failed statement and still exits 0. The
-- summary below would print nothing and the deploy log would read as success
-- over a database that was never migrated. Same failure this repo already fixed
-- for backups in "Make the backup tell the truth when it fails".
\set ON_ERROR_STOP on

BEGIN;

-- The row itself is NOT created here. `seed_default_data()` inserts it from
-- DEFAULT_CELL_LINES on the restart that creates the table, and that is the only
-- writer: a second copy of the name, description and colour in this file would
-- be two sources for one fact, free to drift.
--
-- ⚠️ This check has to come BEFORE anything that could create the row, or it is
-- decoration. An earlier version of this script inserted U2OS defensively and
-- then checked for it, which made the RAISE unreachable -- a guard whose
-- condition can never hold, passing a test that only grepped for the word.
-- Caught by running the script against a real Postgres, not by reading it.
--
-- What it actually catches: running this before the backend restart. Then
-- `(SELECT id ...)` below is NULL, the UPDATE writes NULL over NULL, and the
-- migration reports success having changed nothing.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM cell_lines WHERE name = 'U2OS') THEN
        RAISE EXCEPTION
            'No cell line named U2OS exists, so there is nothing to assign. '
            'Restart the backend first -- seed_default_data() creates the row '
            '-- then re-run this script.';
    END IF;
END $$;

-- ⚠️ The second predicate is what makes this safe to re-run, not `IS NULL`.
--
-- `IS NULL` alone would re-stamp every experiment a colleague had deliberately
-- cleared, every time anyone ran the file again. Gating on the whole column
-- still being empty means the first run fills in the backlog and every run
-- after it is a no-op. The subquery is uncorrelated, so Postgres evaluates it
-- once before the scan -- rows updated by this very statement cannot flip it
-- half way through.
--
-- Scope: exactly one column of one table. Nothing else is touched, which
-- matters because most of these rows belong to the lab's annotator, not to
-- whoever runs this.
UPDATE experiments
   SET cell_line_id = (SELECT id FROM cell_lines WHERE name = 'U2OS')
 WHERE cell_line_id IS NULL
   AND NOT EXISTS (SELECT 1 FROM experiments WHERE cell_line_id IS NOT NULL);

COMMIT;

-- Expected after the first run: every experiment on U2OS, none unassigned.
-- After any later run: unchanged, because the guard above no longer holds.
SELECT COALESCE(c.name, '(unassigned)') AS cell_line, count(*) AS experiments
FROM experiments e
LEFT JOIN cell_lines c ON c.id = e.cell_line_id
GROUP BY 1
ORDER BY 2 DESC;
