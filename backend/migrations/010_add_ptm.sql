-- Migration: Add ptms reference table + experiments.ptm_id FK.
-- PTMs (post-translational modifications of the microtubule lattice) are shared
-- reference data (like map_proteins and microscopes): no user_id.
-- Also applied at runtime by database.ensure_schema_updates() + create_all, and
-- the default vocabulary is seeded by database.seed_default_data() from
-- models.ptm.DEFAULT_PTMS -- this file is manual/prod parity, nothing runs it.

CREATE TABLE IF NOT EXISTS ptms (
    id               SERIAL PRIMARY KEY,
    name             VARCHAR(100) NOT NULL UNIQUE,
    abbreviation     VARCHAR(50),
    modified_residue VARCHAR(100),
    enzyme           VARCHAR(255),
    description      TEXT,
    color            VARCHAR(7),
    created_at       TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
-- No separate index on name: the UNIQUE constraint above already creates one
-- (matches create_all, which builds a single unique index for the model's
-- unique=True, index=True column).

ALTER TABLE experiments
    ADD COLUMN IF NOT EXISTS ptm_id INTEGER REFERENCES ptms(id);
