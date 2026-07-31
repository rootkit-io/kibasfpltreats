-- ============================================================================
-- Migration 0002: FPL fixture difficulty ratings
--
-- ``fixtures`` already exists as a season-scoped dimension table from 0001.
-- Extend it rather than replacing its (season, id) identity or existing FKs.
-- ============================================================================

ALTER TABLE fixtures
    ADD COLUMN finished boolean NOT NULL DEFAULT false,
    ADD COLUMN team_h_fdr_fpl integer,
    ADD COLUMN team_a_fdr_fpl integer,
    ADD COLUMN team_h_fdr_override integer,
    ADD COLUMN team_a_fdr_override integer,
    ADD CONSTRAINT fixtures_team_h_fdr_fpl_range
        CHECK (team_h_fdr_fpl BETWEEN 1 AND 5),
    ADD CONSTRAINT fixtures_team_a_fdr_fpl_range
        CHECK (team_a_fdr_fpl BETWEEN 1 AND 5),
    ADD CONSTRAINT fixtures_team_h_fdr_override_range
        CHECK (team_h_fdr_override BETWEEN 1 AND 5),
    ADD CONSTRAINT fixtures_team_a_fdr_override_range
        CHECK (team_a_fdr_override BETWEEN 1 AND 5);

-- 0001 already provides idx_fixtures_gameweek (season, gameweek_id), which
-- serves the season-scoped gameweek query used by the public FDR endpoint.
