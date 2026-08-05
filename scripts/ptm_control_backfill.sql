-- One-off, additive: give an existing PTM vocabulary its `kind` values.
--
-- NOT run at startup, deliberately. `seed_default_data()` seeds PTMs only when
-- the table is empty, on the principle that re-adding a row the lab has
-- deliberately deleted is worse than leaving the vocabulary short. Inserting
-- `Control` on every boot would break that for this one row, so an existing
-- database gets it once, here, and fresh databases get it from DEFAULT_PTMS.
--
-- The `kind` column and its CHECK constraint are added automatically by
-- ensure_schema_updates() at startup, and every existing row lands on
-- 'modification' — correct for all of them except `Unmodified`.
--
-- ⚠️ Run this against EVERY already-seeded database, not just production. A dev
-- stack whose `ptms` table is non-empty takes the ensure_schema_updates() path
-- and never gets a `Control` row, and leaves `Unmodified` classified as a
-- modification — so every unmodified sample draws with the black PTM centre
-- dot, the plot asserting the opposite of the truth with nothing failing.
--
--   docker exec -i maptimize-db     psql -U maptimize -d maptimize < scripts/ptm_control_backfill.sql
--   docker exec -i maptimize-dev-db psql -U maptimize -d maptimize < scripts/ptm_control_backfill.sql
--
-- Idempotent: re-running it changes nothing. Additive: no row is deleted and no
-- experiment's ptm_id is touched, so rows already recorded as `Unmodified` keep
-- that assignment and simply start reading as non-PTM.

-- ⚠️ Without this, psql walks past a failed statement and still exits 0. Run
-- the script before the backend restart and every statement errors, COMMIT
-- silently becomes ROLLBACK, the summary SELECT below prints nothing, and the
-- deploy log reads as success. Same failure this repo already fixed for backups
-- in "Make the backup tell the truth when it fails".
\set ON_ERROR_STOP on

BEGIN;

UPDATE ptms SET kind = 'none' WHERE name = 'Unmodified' AND kind <> 'none';

-- A `Control` row predating this migration was necessarily filed as a
-- modification -- before `kind` existed there was no other option, and
-- `Unmodified`'s own seeded description called itself "the control condition".
-- Leaving it draws every control with the PTM centre dot: the control rendered
-- as the sample it exists to be compared against.
UPDATE ptms SET kind = 'control' WHERE name = 'Control' AND kind <> 'control';

INSERT INTO ptms (name, abbreviation, description, color, kind)
SELECT 'Control', 'ctrl',
       'Paired control for a PTM condition: the same transfection carried out '
       'with a catalytically inactive enzyme, so the lattice is unmodified. '
       'Run alongside the modified sample it is compared to.',
       '#94a3b8', 'control'
WHERE NOT EXISTS (SELECT 1 FROM ptms WHERE name = 'Control');

-- Assert the shape rather than printing it for a human to interpret. Both
-- UPDATEs match by NAME -- the one handle these rows have, and the thing the
-- design says never to classify by -- so a renamed vocabulary matches zero rows
-- and `UPDATE 0` is not an error. This is what turns that into one.
DO $$
DECLARE n_control int; n_none int; strays text;
BEGIN
    SELECT count(*) FILTER (WHERE kind = 'control'),
           count(*) FILTER (WHERE kind = 'none')
      INTO n_control, n_none FROM ptms;
    SELECT string_agg(name, ', ' ORDER BY name) INTO strays
      FROM ptms WHERE kind NOT IN ('modification', 'control', 'none');

    IF n_control <> 1 OR n_none <> 1 OR strays IS NOT NULL THEN
        RAISE EXCEPTION
            'PTM backfill did not reach the expected shape: control=% (want 1), '
            'none=% (want 1), unreadable kinds=%. The vocabulary was probably '
            'renamed -- classify those rows by hand rather than re-running this.',
            n_control, n_none, COALESCE(strays, '(none)');
    END IF;
END $$;

COMMIT;

-- Expected: one 'control' row, one 'none' row, every remaining row
-- 'modification'. The count of modifications is deliberately not asserted --
-- the vocabulary is editable and the lab may add or delete marks freely.
SELECT kind, count(*) AS rows, string_agg(name, ', ' ORDER BY name) AS names
FROM ptms GROUP BY kind ORDER BY kind;
