-- Migration: Add microscopes reference table + experiments.microscope_id FK.
-- Microscopes are shared reference data (like map_proteins): no user_id.
-- Also applied at runtime by database.ensure_schema_updates() + create_all.

CREATE TABLE IF NOT EXISTS microscopes (
    id            SERIAL PRIMARY KEY,
    name          VARCHAR(100) NOT NULL UNIQUE,
    manufacturer  VARCHAR(100),
    model         VARCHAR(100),
    objective     VARCHAR(100),
    magnification VARCHAR(50),
    description   TEXT,
    color         VARCHAR(7),
    created_at    TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
-- No separate index on name: the UNIQUE constraint above already creates one
-- (matches create_all, which builds a single unique index for the model's
-- unique=True, index=True column).

ALTER TABLE experiments
    ADD COLUMN IF NOT EXISTS microscope_id INTEGER REFERENCES microscopes(id);
