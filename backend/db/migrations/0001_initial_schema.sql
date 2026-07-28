-- ============================================================================
-- Migration 0001: initial projection persistence schema (PostgreSQL 15+)
--
-- Phase 7 finalization of the Phase 6 design, incorporating:
--   * SEASON SCOPING: FPL recycles ids every season, so every dimension is
--     keyed by (season, fpl_id) -- season is a short code like '2627'.
--     Fact tables carry season and reference dimensions with composite FKs.
--   * published_fixture_projections view: the fixture grain is public data
--     (Double Gameweek breakdowns).
--   * No retention machinery: runs are kept indefinitely by decision.
--
-- Transactions are the migration runner's responsibility (no BEGIN/COMMIT
-- here); the test harness executes this file inside its own transaction.
--
-- Invariants (enforced in ProjectionRepository implementations):
--   * a run's fact rows all carry the run's season;
--   * facts are immutable after save; corrections are a new run;
--   * the dashboard reads only the published_* views.
-- ============================================================================

-- ---------------------------------------------------------------- dimensions

CREATE TABLE gameweeks (
    season        text NOT NULL,
    id            smallint NOT NULL,               -- FPL event id (1..38)
    deadline_time timestamptz,
    finished      boolean NOT NULL DEFAULT false,
    PRIMARY KEY (season, id)
);

CREATE TABLE teams (
    season     text NOT NULL,
    id         smallint NOT NULL,                  -- FPL team id (season-scoped)
    name       text NOT NULL,
    short_name text,
    PRIMARY KEY (season, id)
);

CREATE TABLE players (
    season      text NOT NULL,
    id          integer NOT NULL,                  -- FPL element id
    first_name  text,
    second_name text,
    web_name    text NOT NULL,
    PRIMARY KEY (season, id)
);

CREATE TABLE fixtures (
    season       text NOT NULL,
    id           integer NOT NULL,                 -- FPL fixture id
    gameweek_id  smallint,
    home_team_id smallint NOT NULL,
    away_team_id smallint NOT NULL,
    kickoff_time timestamptz,
    PRIMARY KEY (season, id),
    FOREIGN KEY (season, gameweek_id)  REFERENCES gameweeks (season, id),
    FOREIGN KEY (season, home_team_id) REFERENCES teams (season, id),
    FOREIGN KEY (season, away_team_id) REFERENCES teams (season, id)
);

CREATE INDEX idx_fixtures_gameweek ON fixtures (season, gameweek_id);

-- ---------------------------------------------------------------- run header

CREATE TABLE projection_runs (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    season                text NOT NULL,
    created_at            timestamptz NOT NULL DEFAULT now(),
    status                text NOT NULL DEFAULT 'draft'
                          CHECK (status IN ('draft', 'published', 'archived')),
    published_at          timestamptz,
    source                text NOT NULL
                          CHECK (source IN ('admin_api', 'cli', 'notebook')),
    gw_start              smallint,
    gw_end                smallint,
    n_sim                 integer,
    include_mc            boolean NOT NULL DEFAULT false,
    minutes_model_loaded  boolean NOT NULL DEFAULT false,
    manual_minutes_layers smallint NOT NULL DEFAULT 0,
    override_count        smallint NOT NULL DEFAULT 0,
    inputs                jsonb,
    notes                 text,
    CONSTRAINT published_iff_timestamp
        CHECK ((status = 'published') = (published_at IS NOT NULL))
);

CREATE INDEX idx_runs_published
    ON projection_runs (published_at DESC)
    WHERE status = 'published';

-- ---------------------------------------------------------------- fact tables

CREATE TABLE fixture_forecasts (
    run_id            uuid NOT NULL REFERENCES projection_runs (id) ON DELETE CASCADE,
    season            text NOT NULL,
    fixture_id        integer NOT NULL,
    gameweek_id       smallint,
    home_goals_lambda double precision,
    away_goals_lambda double precision,
    home_cs_prob      double precision CHECK (home_cs_prob BETWEEN 0 AND 1),
    away_cs_prob      double precision CHECK (away_cs_prob BETWEEN 0 AND 1),
    projection_source text,
    PRIMARY KEY (run_id, fixture_id),
    FOREIGN KEY (season, fixture_id)  REFERENCES fixtures (season, id),
    FOREIGN KEY (season, gameweek_id) REFERENCES gameweeks (season, id)
);

CREATE INDEX idx_fixture_forecasts_gw ON fixture_forecasts (run_id, gameweek_id);

CREATE TABLE player_fixture_projections (
    run_id            uuid NOT NULL REFERENCES projection_runs (id) ON DELETE CASCADE,
    season            text NOT NULL,
    player_id         integer NOT NULL,
    fixture_id        integer NOT NULL,
    gameweek_id       smallint NOT NULL,
    team_id           smallint,
    opponent_id       smallint,
    was_home          boolean,
    expected_minutes  double precision,
    likely_minutes    double precision,
    start_probability double precision CHECK (start_probability BETWEEN 0 AND 1),
    play_probability  double precision CHECK (play_probability BETWEEN 0 AND 1),
    minutes_source    text,
    xg                double precision,
    xa                double precision,
    xga_expected      double precision,
    cs_prob           double precision CHECK (cs_prob BETWEEN 0 AND 1),
    p1_ga             double precision,
    xpts              double precision,
    appearance_pts    double precision,
    goal_pts          double precision,
    assist_pts        double precision,
    cs_pts            double precision,
    save_pts          double precision,
    defcon_pts        double precision,
    card_pts          double precision,
    pen_miss_pts      double precision,
    concede_pts       double precision,
    prior_based       boolean,
    prior_source      text,
    PRIMARY KEY (run_id, player_id, fixture_id),
    FOREIGN KEY (season, player_id)   REFERENCES players (season, id),
    FOREIGN KEY (season, fixture_id)  REFERENCES fixtures (season, id),
    FOREIGN KEY (season, gameweek_id) REFERENCES gameweeks (season, id),
    FOREIGN KEY (season, team_id)     REFERENCES teams (season, id),
    FOREIGN KEY (season, opponent_id) REFERENCES teams (season, id)
);

CREATE INDEX idx_pfp_read ON player_fixture_projections (run_id, gameweek_id, xpts DESC);

CREATE TABLE player_gameweek_projections (
    run_id            uuid NOT NULL REFERENCES projection_runs (id) ON DELETE CASCADE,
    season            text NOT NULL,
    gameweek_id       smallint NOT NULL,
    player_id         integer NOT NULL,
    team_id           smallint,
    position          text CHECK (position IN ('GK', 'DEF', 'MID', 'FWD')),
    now_cost          smallint,
    selected_by_pct   double precision,
    fpl_status        text,
    chance_of_playing smallint,
    news              text,
    prior_based       boolean,
    prior_source      text,
    fixtures_in_week  smallint,
    expected_minutes  double precision,
    start_probability double precision CHECK (start_probability BETWEEN 0 AND 1),
    play_probability  double precision CHECK (play_probability BETWEEN 0 AND 1),
    minutes_source    text,
    xg                double precision,
    xa                double precision,
    xga_expected      double precision,
    xpts              double precision,
    ml_xpts           double precision,
    p1_ga             double precision,
    p_return          double precision CHECK (p_return BETWEEN 0 AND 1),
    p_haul            double precision CHECK (p_haul BETWEEN 0 AND 1),
    appearance_pts    double precision,
    goal_pts          double precision,
    assist_pts        double precision,
    cs_pts            double precision,
    save_pts          double precision,
    defcon_pts        double precision,
    card_pts          double precision,
    pen_miss_pts      double precision,
    concede_pts       double precision,
    PRIMARY KEY (run_id, gameweek_id, player_id),
    FOREIGN KEY (season, player_id)   REFERENCES players (season, id),
    FOREIGN KEY (season, gameweek_id) REFERENCES gameweeks (season, id),
    FOREIGN KEY (season, team_id)     REFERENCES teams (season, id)
);

CREATE INDEX idx_pgp_read   ON player_gameweek_projections (run_id, gameweek_id, xpts DESC);
CREATE INDEX idx_pgp_player ON player_gameweek_projections (season, player_id, gameweek_id);

CREATE TABLE player_gameweek_simulations (
    run_id          uuid NOT NULL REFERENCES projection_runs (id) ON DELETE CASCADE,
    season          text NOT NULL,
    gameweek_id     smallint NOT NULL,
    player_id       integer NOT NULL,
    n_sim           integer,
    mean_pts        double precision,
    std_pts         double precision,
    min_pts         double precision,
    max_pts         double precision,
    floor_p10       double precision,
    p25             double precision,
    p75             double precision,
    upside_p90      double precision,
    p1_return       double precision CHECK (p1_return BETWEEN 0 AND 1),
    p2_return       double precision CHECK (p2_return BETWEEN 0 AND 1),
    p_return        double precision CHECK (p_return BETWEEN 0 AND 1),
    p_haul          double precision CHECK (p_haul BETWEEN 0 AND 1),
    bracket_le_2    double precision CHECK (bracket_le_2 BETWEEN 0 AND 1),
    bracket_3_6     double precision CHECK (bracket_3_6 BETWEEN 0 AND 1),
    bracket_7_9     double precision CHECK (bracket_7_9 BETWEEN 0 AND 1),
    bracket_10_14   double precision CHECK (bracket_10_14 BETWEEN 0 AND 1),
    bracket_15_plus double precision CHECK (bracket_15_plus BETWEEN 0 AND 1),
    PRIMARY KEY (run_id, gameweek_id, player_id),
    FOREIGN KEY (season, player_id)   REFERENCES players (season, id),
    FOREIGN KEY (season, gameweek_id) REFERENCES gameweeks (season, id)
);

CREATE INDEX idx_pgs_read ON player_gameweek_simulations (run_id, gameweek_id, mean_pts DESC);

-- ------------------------------------------------------------- read surface

CREATE VIEW current_published_run AS
SELECT *
FROM projection_runs
WHERE status = 'published'
ORDER BY published_at DESC
LIMIT 1;

CREATE VIEW published_player_week AS
SELECT p.web_name,
       p.first_name,
       p.second_name,
       t.name       AS team_name,
       t.short_name AS team_short,
       g.deadline_time,
       (pgp.now_cost / 10.0)::double precision AS price,
       pgp.*
FROM player_gameweek_projections pgp
JOIN current_published_run r ON r.id = pgp.run_id
JOIN players   p ON (p.season, p.id) = (pgp.season, pgp.player_id)
LEFT JOIN teams t ON (t.season, t.id) = (pgp.season, pgp.team_id)
JOIN gameweeks g ON (g.season, g.id) = (pgp.season, pgp.gameweek_id);

CREATE VIEW published_player_week_simulations AS
SELECT p.web_name,
       t.short_name AS team_short,
       pgs.*
FROM player_gameweek_simulations pgs
JOIN current_published_run r ON r.id = pgs.run_id
JOIN players p ON (p.season, p.id) = (pgs.season, pgs.player_id)
LEFT JOIN player_gameweek_projections pgp
       ON (pgp.run_id, pgp.gameweek_id, pgp.player_id)
        = (pgs.run_id, pgs.gameweek_id, pgs.player_id)
LEFT JOIN teams t ON (t.season, t.id) = (pgs.season, pgp.team_id);

-- Fixture grain is public: Double Gameweek breakdowns for the dashboard.
CREATE VIEW published_fixture_projections AS
SELECT p.web_name,
       t.short_name   AS team_short,
       opp.short_name AS opponent_short,
       f.kickoff_time,
       pfp.*
FROM player_fixture_projections pfp
JOIN current_published_run r ON r.id = pfp.run_id
JOIN players  p   ON (p.season, p.id)     = (pfp.season, pfp.player_id)
JOIN fixtures f   ON (f.season, f.id)     = (pfp.season, pfp.fixture_id)
LEFT JOIN teams t   ON (t.season, t.id)   = (pfp.season, pfp.team_id)
LEFT JOIN teams opp ON (opp.season, opp.id) = (pfp.season, pfp.opponent_id);

CREATE VIEW published_fixture_forecasts AS
SELECT f.kickoff_time,
       th.name AS home_team,
       ta.name AS away_team,
       ff.*
FROM fixture_forecasts ff
JOIN current_published_run r ON r.id = ff.run_id
JOIN fixtures f ON (f.season, f.id) = (ff.season, ff.fixture_id)
JOIN teams   th ON (th.season, th.id) = (f.season, f.home_team_id)
JOIN teams   ta ON (ta.season, ta.id) = (f.season, f.away_team_id);
