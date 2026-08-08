-- ============================================================================
-- Migration 0003: current player price and ownership
--
-- `price` and `selected_by_pct` were only ever populated from
-- player_gameweek_projections, i.e. per RUN. Runs ingested from the CSV pair
-- carry neither, so the dashboard grid showed empty Price and Own% columns.
--
-- Price and ownership are attributes of a player RIGHT NOW, not outputs of a
-- projection run -- they move every gameweek regardless of whether a run
-- happened. They therefore belong on the `players` dimension, refreshed from
-- the FPL bootstrap, with the per-run value preferred when present so
-- historical model-computed runs keep reporting the price they were built on.
-- ============================================================================

ALTER TABLE players
    ADD COLUMN IF NOT EXISTS now_cost integer,
    ADD COLUMN IF NOT EXISTS selected_by_percent double precision;

-- FPL stores price in tenths of a million (55 => GBP 5.5m).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'players'::regclass
          AND conname = 'players_now_cost_positive'
    ) THEN
        ALTER TABLE players ADD CONSTRAINT players_now_cost_positive
            CHECK (now_cost IS NULL OR now_cost > 0);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'players'::regclass
          AND conname = 'players_selected_by_percent_range'
    ) THEN
        ALTER TABLE players ADD CONSTRAINT players_selected_by_percent_range
            CHECK (selected_by_percent IS NULL
                   OR selected_by_percent BETWEEN 0 AND 100);
    END IF;
END
$$;

-- The view is rebuilt rather than replaced: `pgp.*` cannot coexist with a
-- COALESCE that reuses the same output names, so the projection columns are
-- enumerated and the two overridden ones re-emitted. Column NAMES are
-- unchanged, so `SELECT *` consumers are unaffected.
DROP VIEW IF EXISTS published_player_week;

CREATE VIEW published_player_week AS
SELECT p.web_name,
       p.first_name,
       p.second_name,
       t.name       AS team_name,
       t.short_name AS team_short,
       g.deadline_time,
       -- Per-run value wins when a run recorded one; otherwise the current
       -- dimension value, so CSV-ingested runs still show a live price.
       (COALESCE(pgp.now_cost, p.now_cost) / 10.0)::double precision AS price,
       COALESCE(pgp.now_cost, p.now_cost) AS now_cost,
       COALESCE(pgp.selected_by_pct, p.selected_by_percent) AS selected_by_pct,
       pgp.run_id,
       pgp.season,
       pgp.gameweek_id,
       pgp.player_id,
       pgp.team_id,
       pgp.position,
       pgp.fpl_status,
       pgp.chance_of_playing,
       pgp.news,
       pgp.prior_based,
       pgp.prior_source,
       pgp.fixtures_in_week,
       pgp.expected_minutes,
       pgp.start_probability,
       pgp.play_probability,
       pgp.minutes_source,
       pgp.xg,
       pgp.xa,
       pgp.xga_expected,
       pgp.xpts,
       pgp.ml_xpts,
       pgp.p1_ga,
       pgp.p_return,
       pgp.p_haul,
       pgp.appearance_pts,
       pgp.goal_pts,
       pgp.assist_pts,
       pgp.cs_pts,
       pgp.save_pts,
       pgp.defcon_pts,
       pgp.card_pts,
       pgp.pen_miss_pts,
       pgp.concede_pts
FROM player_gameweek_projections pgp
JOIN current_published_run r ON r.id = pgp.run_id
JOIN players   p ON (p.season, p.id) = (pgp.season, pgp.player_id)
LEFT JOIN teams t ON (t.season, t.id) = (pgp.season, pgp.team_id)
JOIN gameweeks g ON (g.season, g.id) = (pgp.season, pgp.gameweek_id);
