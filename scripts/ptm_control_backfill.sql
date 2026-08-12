-- One-off, additive: bring an existing PTM vocabulary to the current shape —
-- `kind` values, and a control row PAIRED with the modification it controls.
--
-- NOT run at startup, deliberately. `seed_default_data()` seeds PTMs only when
-- the table is empty, on the principle that re-adding a row the lab has
-- deliberately deleted is worse than leaving the vocabulary short. Inserting a
-- control row on every boot would break that, so an existing database gets it
-- once, here, and fresh databases get it from DEFAULT_PTMS.
--
-- The `kind` column, its CHECK constraint and `controls_ptm_id` are added
-- automatically by ensure_schema_updates() at startup, and every existing row
-- lands on 'modification' — correct for all of them except `Unmodified`.
--
-- ⚠️ Run this against EVERY already-seeded database, not just production. A dev
-- stack whose `ptms` table is non-empty takes the ensure_schema_updates() path
-- and never gets a control row, and leaves `Unmodified` classified as a
-- modification — so every unmodified sample draws with the black PTM centre
-- dot, the plot asserting the opposite of the truth with nothing failing.
--
--   docker exec -i maptimize-db     psql -U maptimize -d maptimize < scripts/ptm_control_backfill.sql
--   docker exec -i maptimize-dev-db psql -U maptimize -d maptimize < scripts/ptm_control_backfill.sql
--
-- Idempotent from ANY earlier state: run it once, twice, or after the older
-- version of this same file that created a pooled `Control` row — it converges.
-- That is why the pairing step lives here and not in a second script: two
-- scripts that must run in order are one forgotten step away from a database
-- with both a pooled `Control` and a paired one.
--
-- Additive: no row is deleted and no experiment's ptm_id is touched. The 28
-- experiments filed under the old `Control` row keep pointing at it; that row is
-- renamed in place, so they end up on a correctly-labelled control rather than
-- being migrated anywhere.

-- ⚠️ Without this, psql walks past a failed statement and still exits 0. Run
-- the script before the backend restart and every statement errors, COMMIT
-- silently becomes ROLLBACK, the summary SELECT below prints nothing, and the
-- deploy log reads as success. Same failure this repo already fixed for backups
-- in "Make the backup tell the truth when it fails".
\set ON_ERROR_STOP on

BEGIN;

UPDATE ptms SET kind = 'none' WHERE name = 'Unmodified' AND kind <> 'none';

-- A `Control` row predating the `kind` column was necessarily filed as a
-- modification -- before `kind` existed there was no other option, and
-- `Unmodified`'s own seeded description called itself "the control condition".
-- Leaving it draws every control with the PTM centre dot: the control rendered
-- as the sample it exists to be compared against.
UPDATE ptms SET kind = 'control' WHERE name = 'Control' AND kind <> 'control';

-- The pooled `Control` row becomes the detyrosination control, in place.
--
-- Every experiment filed under it is a detyrosination control -- their names say
-- so ("CLIP170 DÉTYR-CTRL", "MAP7 ctrl détyr manip 2", 28 of them, checked
-- 2026-08-12) -- so this rename asserts nothing the data does not already say.
-- Future PTM conditions get their own control row through the API, which
-- refuses to create one without a partner.
UPDATE ptms SET name = 'Detyrosination control', abbreviation = 'deTyr ctrl',
                description = 'Paired control for detyrosination: the same '
                              'transfection carried out with a catalytically '
                              'inactive enzyme, so the lattice is unmodified. '
                              'Run alongside the modified sample it is compared to.'
WHERE name = 'Control';

-- Neutral grey: for a value that is not a modification, grey is the right answer
-- wherever the PTM's own colour is shown.
INSERT INTO ptms (name, abbreviation, description, color, kind)
SELECT 'Detyrosination control', 'deTyr ctrl',
       'Paired control for detyrosination: the same transfection carried out '
       'with a catalytically inactive enzyme, so the lattice is unmodified. '
       'Run alongside the modified sample it is compared to.',
       '#94a3b8', 'control'
WHERE NOT EXISTS (SELECT 1 FROM ptms WHERE name = 'Detyrosination control');

UPDATE ptms c
   SET controls_ptm_id = m.id
  FROM ptms m
 WHERE c.name = 'Detyrosination control'
   AND m.name = 'Detyrosination'
   AND m.kind = 'modification'
   AND c.controls_ptm_id IS DISTINCT FROM m.id;

-- Assert the shape rather than printing it for a human to interpret. The
-- statements above match by NAME -- the one handle these rows have, and the
-- thing the design says never to classify by -- so a renamed vocabulary matches
-- zero rows and `UPDATE 0` is not an error. This is what turns that into one.
--
-- The control COUNT is deliberately not asserted: there is one control per PTM
-- condition now, so the lab adding a second is the feature working. What must
-- hold is that no control is left unpaired -- an unpaired control IS the pooled
-- row this migration exists to eliminate, and it would be reached in silence.
DO $$
DECLARE n_control int; n_none int; strays text; unpaired text;
BEGIN
    SELECT count(*) FILTER (WHERE kind = 'control'),
           count(*) FILTER (WHERE kind = 'none')
      INTO n_control, n_none FROM ptms;
    SELECT string_agg(name, ', ' ORDER BY name) INTO strays
      FROM ptms WHERE kind NOT IN ('modification', 'control', 'none');
    SELECT string_agg(c.name, ', ' ORDER BY c.name) INTO unpaired
      FROM ptms c LEFT JOIN ptms m ON m.id = c.controls_ptm_id
     WHERE c.kind = 'control'
       AND (m.id IS NULL OR m.kind <> 'modification');

    IF n_control < 1 OR n_none <> 1 OR strays IS NOT NULL OR unpaired IS NOT NULL THEN
        RAISE EXCEPTION
            'PTM backfill did not reach the expected shape: control=% (want >=1), '
            'none=% (want 1), unreadable kinds=%, controls with no modification '
            'partner=%. The vocabulary was probably renamed -- classify those '
            'rows by hand rather than re-running this.',
            n_control, n_none, COALESCE(strays, '(none)'), COALESCE(unpaired, '(none)');
    END IF;
END $$;

COMMIT;

-- Expected: at least one 'control' row, each naming its modification; one 'none'
-- row; every remaining row 'modification'. The count of modifications is
-- deliberately not asserted -- the vocabulary is editable and the lab may add or
-- delete marks freely.
SELECT c.kind, c.name, m.name AS controls
FROM ptms c LEFT JOIN ptms m ON m.id = c.controls_ptm_id
ORDER BY c.kind, c.name;
