-- One-off, additive: give the existing PTM vocabulary its `kind` values.
--
-- NOT run at startup, deliberately. `seed_default_data()` seeds PTMs only when
-- the table is empty, on the principle that re-adding a row the lab has
-- deliberately deleted is worse than leaving the vocabulary short. Inserting
-- `Control` on every boot would break that for this one row, so production gets
-- it once, here, and fresh databases get it from DEFAULT_PTMS.
--
-- The `kind` column itself is added automatically by ensure_schema_updates() at
-- startup, and every existing row lands on 'modification' — which is correct for
-- all of them except `Unmodified`.
--
--   docker exec -i maptimize-db psql -U maptimize -d maptimize \
--     < scripts/ptm_control_backfill.sql
--
-- Idempotent: re-running it changes nothing. Additive: no row is deleted and no
-- experiment's ptm_id is touched, so the 49 experiments already recorded as
-- `Unmodified` keep that assignment and simply start reading as non-PTM.

BEGIN;

UPDATE ptms SET kind = 'none' WHERE name = 'Unmodified' AND kind <> 'none';

INSERT INTO ptms (name, abbreviation, description, color, kind)
SELECT 'Control', 'ctrl',
       'Paired control for a PTM condition: the same transfection carried out '
       'with a catalytically inactive enzyme, so the lattice is unmodified. '
       'Run alongside the modified sample it is compared to.',
       '#94a3b8', 'control'
WHERE NOT EXISTS (SELECT 1 FROM ptms WHERE name = 'Control');

COMMIT;

-- Expected afterwards: one 'control' row (Control), one 'none' row (Unmodified),
-- and the nine tubulin marks on 'modification'.
SELECT kind, count(*) AS rows, string_agg(name, ', ' ORDER BY name) AS names
FROM ptms GROUP BY kind ORDER BY kind;
