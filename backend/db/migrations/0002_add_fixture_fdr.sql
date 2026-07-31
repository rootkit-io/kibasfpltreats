-- ============================================================================
-- Migration 0002: FPL fixture difficulty ratings
--
-- ``fixtures`` already exists as a season-scoped dimension table from 0001.
-- Extend it rather than replacing its (season, id) identity or existing FKs.
-- ============================================================================

-- This migration is also run by the backend startup migrator.  PostgreSQL's
-- docker-entrypoint only reads this directory for a new data volume, so
-- upgrades must be safe to apply to an established production database.
ALTER TABLE fixtures
    ADD COLUMN IF NOT EXISTS finished boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS team_h_fdr_fpl integer,
    ADD COLUMN IF NOT EXISTS team_a_fdr_fpl integer,
    ADD COLUMN IF NOT EXISTS team_h_fdr_override integer,
    ADD COLUMN IF NOT EXISTS team_a_fdr_override integer;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'fixtures'::regclass
          AND conname = 'fixtures_team_h_fdr_fpl_range'
    ) THEN
        ALTER TABLE fixtures ADD CONSTRAINT fixtures_team_h_fdr_fpl_range
            CHECK (team_h_fdr_fpl BETWEEN 1 AND 5);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'fixtures'::regclass
          AND conname = 'fixtures_team_a_fdr_fpl_range'
    ) THEN
        ALTER TABLE fixtures ADD CONSTRAINT fixtures_team_a_fdr_fpl_range
            CHECK (team_a_fdr_fpl BETWEEN 1 AND 5);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'fixtures'::regclass
          AND conname = 'fixtures_team_h_fdr_override_range'
    ) THEN
        ALTER TABLE fixtures ADD CONSTRAINT fixtures_team_h_fdr_override_range
            CHECK (team_h_fdr_override BETWEEN 1 AND 5);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'fixtures'::regclass
          AND conname = 'fixtures_team_a_fdr_override_range'
    ) THEN
        ALTER TABLE fixtures ADD CONSTRAINT fixtures_team_a_fdr_override_range
            CHECK (team_a_fdr_override BETWEEN 1 AND 5);
    END IF;
END
$$;

-- 0001 already provides idx_fixtures_gameweek (season, gameweek_id), which
-- serves the season-scoped gameweek query used by the public FDR endpoint.
