-- ============================================================================
-- Migration 0004: per-user fixture difficulty overrides
--
-- FDR ratings are personal. One manager rates Arsenal away a 5, another a 4.
-- Storing this per-user server-side means ratings follow the user across
-- devices, survive browser clears, and can be seeded from existing
-- localStorage on first sync.
--
-- Precedence (highest to lowest):
--   1. user_fixture_overrides.fdr          (this table)
--   2. fixtures.team_h/a_fdr_override      (admin global override)
--   3. fixtures.team_h/a_fdr_fpl           (official FPL rating)
--
-- The `fdr` column maps to FDR values 1-5. A deleted row means "no personal
-- override" — the row is removed rather than NULL so queries are a simple
-- EXISTS check.
-- ============================================================================

CREATE TABLE IF NOT EXISTS user_fixture_overrides (
    clerk_user_id  text    NOT NULL,
    season         text    NOT NULL,
    fixture_id     integer NOT NULL,
    team_id        integer NOT NULL,
    fdr            integer NOT NULL,

    PRIMARY KEY (clerk_user_id, season, fixture_id, team_id),

    CONSTRAINT ufo_fdr_range CHECK (fdr BETWEEN 1 AND 5),

    CONSTRAINT ufo_fixture_fk
        FOREIGN KEY (season, fixture_id)
        REFERENCES fixtures (season, id)
        ON DELETE CASCADE,

    CONSTRAINT ufo_team_fk
        FOREIGN KEY (season, team_id)
        REFERENCES teams (season, id)
        ON DELETE CASCADE
);

-- Queried by user + season on every page load (bulk fetch).
CREATE INDEX IF NOT EXISTS ufo_user_season_idx
    ON user_fixture_overrides (clerk_user_id, season);
