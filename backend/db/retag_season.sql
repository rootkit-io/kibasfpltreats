-- ===========================================================================
-- Re-tag a season: :from_season -> :to_season
--
--   psql -v from_season=2627 -v to_season=2526 -f db/retag_season.sql
--
-- WHY THIS IS NOT A SET OF UPDATEs
-- --------------------------------
-- Every fact table carries a composite FK onto its dimension, e.g.
--   FOREIGN KEY (season, player_id) REFERENCES players (season, id)
-- and those constraints are NOT DEFERRABLE. Updating a parent key while a
-- child still references it fails immediately:
--
--   ERROR: update or delete on table "players" violates foreign key
--          constraint ... Key (season, id)=(2627, 1) is still referenced
--
-- So the safe shape is clone -> repoint -> delete, inside one transaction:
--   1. copy the dimension rows under the new season code
--   2. move the fact rows onto them
--   3. drop the now-unreferenced originals
--
-- Idempotent: ON CONFLICT DO NOTHING on the clones means a re-run after a
-- partial failure is safe. Wrapped in a single transaction, so any error
-- leaves the database exactly as it was.
-- ===========================================================================

BEGIN;

-- 1. dimensions ------------------------------------------------------------
INSERT INTO gameweeks (season, id, deadline_time, finished)
SELECT :'to_season', id, deadline_time, finished
FROM gameweeks WHERE season = :'from_season'
ON CONFLICT (season, id) DO NOTHING;

INSERT INTO teams (season, id, name, short_name)
SELECT :'to_season', id, name, short_name
FROM teams WHERE season = :'from_season'
ON CONFLICT (season, id) DO NOTHING;

INSERT INTO players (season, id, first_name, second_name, web_name)
SELECT :'to_season', id, first_name, second_name, web_name
FROM players WHERE season = :'from_season'
ON CONFLICT (season, id) DO NOTHING;

-- 2. fixtures are both a child (teams, gameweeks) and a parent
--    (fixture_forecasts), so they clone before their children repoint.
INSERT INTO fixtures (season, id, gameweek_id, home_team_id, away_team_id,
                      kickoff_time, finished, team_h_fdr_fpl, team_a_fdr_fpl,
                      team_h_fdr_override, team_a_fdr_override)
SELECT :'to_season', id, gameweek_id, home_team_id, away_team_id,
       kickoff_time, finished, team_h_fdr_fpl, team_a_fdr_fpl,
       team_h_fdr_override, team_a_fdr_override
FROM fixtures WHERE season = :'from_season'
ON CONFLICT (season, id) DO NOTHING;

-- 3. facts repoint onto the cloned dimensions ------------------------------
UPDATE projection_runs             SET season = :'to_season' WHERE season = :'from_season';
UPDATE player_gameweek_projections SET season = :'to_season' WHERE season = :'from_season';
UPDATE player_gameweek_simulations SET season = :'to_season' WHERE season = :'from_season';
UPDATE player_fixture_projections  SET season = :'to_season' WHERE season = :'from_season';
UPDATE fixture_forecasts           SET season = :'to_season' WHERE season = :'from_season';

-- 4. drop the originals, now unreferenced ----------------------------------
DELETE FROM fixtures  WHERE season = :'from_season';
DELETE FROM players   WHERE season = :'from_season';
DELETE FROM teams     WHERE season = :'from_season';
DELETE FROM gameweeks WHERE season = :'from_season';

COMMIT;
