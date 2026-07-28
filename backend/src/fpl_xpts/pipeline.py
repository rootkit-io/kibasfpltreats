from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .config import AppConfig
from .rulebook import CURRENT_RULEBOOK, Rulebook
from .data_sources import FplClient, bootstrap_tables, current_event
from .features import attach_recent_player_form, build_team_assist_factors, build_team_strength
from .forecast import forecast_fixture_lambdas
from .market_odds import apply_market_odds_projections
from .minutes_contract import MinutesRunInputs, resolve_minutes_run_inputs
from .minutes_engine import resolve_minutes
from .minutes_model import load_minutes_bundle
from .ml_models import attach_live_ml_predictions
from .minute_overrides import write_player_minutes_inputs
from .monte_carlo import simulate_player_week
from .projections import apply_elevenify_team_projections, apply_external_team_projections
from .shot_profiles import attach_understat_profiles_to_players, build_understat_shot_profiles
from .xpts import aggregate_gameweek, attach_mc_tail_probabilities, build_player_fixture_forecast, recompute_player_fixture_components


def _frame_records(frame: pd.DataFrame) -> list[dict]:
    return json.loads(frame.to_json(orient="records", date_format="iso"))


def _write_projection_snapshot(results: dict[str, pd.DataFrame], event: int | None, config: AppConfig) -> Path:
    snapshot_dir = config.data_dir / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    event_label = int(event) if event is not None else 0
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot_path = snapshot_dir / f"gw{event_label}_snapshot_{timestamp}.json"
    payload = {
        "generated_at": timestamp,
        "event": event_label,
        "tables": {name: _frame_records(frame) for name, frame in results.items()},
    }
    snapshot_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return snapshot_path


@dataclass(frozen=True)
class ProjectionInputs:
    """Everything the projection core consumes for one run (Candidate #3 seam).

    This is the Data Adapter contract: the live pipeline builds it from the
    FPL API + enrichments; a historical adapter will build the same shape
    from vaastav/odds archives. The core (:func:`run_projection_stages`)
    knows nothing about where the frames came from.

    Frame schemas (required columns; extra columns pass through):

    ``players`` -- one row per FPL player (``players_live`` in the blueprint):
        ``id`` (FPL element id), ``web_name``, ``first_name``, ``second_name``,
        ``team`` (team id), ``element_type`` (1-4 -> GK/DEF/MID/FWD), plus the
        rate inputs consumed by ``features.build_player_rates`` (season
        expected-stat and per-90 columns; missing ones degrade to priors) and
        availability fields (``status``, ``chance_of_playing_this_round`` /
        ``_next_round``) consumed by the minutes model features.

    ``teams`` -- one row per team: ``id``, ``name`` (used for canonical
        player/team matching in the Minutes Engine), and ``position`` /
        ``points`` / ``played`` (league-table context for minutes features).

    ``fixtures_forecast`` -- one row per fixture with team expectations:
        ``id`` (FPL fixture id), ``event`` (gameweek), ``team_h``, ``team_a``,
        ``kickoff_time``, ``home_xg``, ``away_xg``, ``home_xa``, ``away_xa``,
        ``home_cs_prob``, ``away_cs_prob``, ``projection_source``.

    ``history_by_player`` -- element id -> element-summary history frame
        (``round``/``event``, ``minutes``, ``starts``, ``kickoff_time``, ...);
        empty dict is valid (rates fall back to season aggregates and the
        minutes model uses cold-start features).

    ``rulebook`` -- the scoring rules for this run (ADR-0003): the live
        adapter passes ``CURRENT_RULEBOOK``; a historical adapter passes
        ``rulebook_for_season(season)``.

    ``minutes_inputs`` -- resolved Manual Minutes Inputs and overrides
        (ADR-0001 boundary output). Empty (the default) means model +
        heuristic only -- which is exactly the historical-replay semantics.

    ``minutes_model_bundle`` -- pre-loaded minutes model, or ``None`` to skip
        layer 2. The core never touches the file system to obtain it.

    ``minutes_features`` -- optional pre-built minutes-model features (one
        row per ``event``/``player_id`` with the bundle's feature columns).
        Adapters holding higher-fidelity features than the live builder can
        derive (e.g. the replay's point-in-time historical features) supply
        them here; ``None`` means the engine builds live features from the
        frames above.
    """

    players: pd.DataFrame
    teams: pd.DataFrame
    fixtures_forecast: pd.DataFrame
    history_by_player: Mapping[int, pd.DataFrame] = field(default_factory=dict)
    rulebook: Rulebook = CURRENT_RULEBOOK
    minutes_inputs: MinutesRunInputs = field(default_factory=MinutesRunInputs)
    minutes_model_bundle: dict | None = None
    minutes_features: pd.DataFrame | None = None


def run_projection_stages(
    inputs: ProjectionInputs,
    config: AppConfig = AppConfig(),
    include_mc: bool = True,
) -> dict[str, pd.DataFrame]:
    """The projection core: one implementation of the stage sequence.

    forecast (layer 1) -> Minutes Engine (layers 2-5) -> component recompute
    (layer 6) -> gameweek aggregation -> Monte Carlo -> MC tail attach.

    Pure with respect to acquisition: consumes only ``inputs`` (plus model
    knobs from ``config``: form/set-piece weights, ``n_sim``,
    ``random_seed``) and performs no network or file I/O. Both the live
    adapter (:func:`run_live_projection`) and, in later phases, the
    historical replay drive this single function.
    """
    player_fixture = build_player_fixture_forecast(
        inputs.players,
        inputs.fixtures_forecast,
        dict(inputs.history_by_player),
        form_blend_weight=config.form_blend_weight,
        set_piece_xa_weight=config.set_piece_xa_weight,
        rulebook=inputs.rulebook,
    )
    player_fixture = resolve_minutes(
        player_fixture,
        inputs.players,
        inputs.teams,
        history_by_player=dict(inputs.history_by_player),
        model_bundle=inputs.minutes_model_bundle,
        minutes_features=inputs.minutes_features,
        manual_inputs=inputs.minutes_inputs.manual_inputs,
        overrides=inputs.minutes_inputs.overrides,
    )
    player_fixture = recompute_player_fixture_components(
        player_fixture,
        set_piece_xa_weight=config.set_piece_xa_weight,
        rulebook=inputs.rulebook,
    )
    weekly = aggregate_gameweek(player_fixture)
    mc = (
        simulate_player_week(
            player_fixture, config.n_sim, config.random_seed, rulebook=inputs.rulebook
        )
        if include_mc
        else pd.DataFrame()
    )
    weekly = attach_mc_tail_probabilities(weekly, mc)
    return {"player_fixture": player_fixture, "weekly": weekly, "monte_carlo": mc}


def run_live_projection(
    config: AppConfig = AppConfig(),
    include_mc: bool = True,
    manual_minutes_paths: Sequence[Path | str] | None = None,
    minute_override_paths: Sequence[Path | str] | None = None,
    manual_minutes_states: Sequence | None = None,
    minute_override_states: Sequence | None = None,
    minutes_model_bundle: dict | None = None,
    write_snapshot: bool = True,
) -> dict[str, pd.DataFrame]:
    """Run the live projection.

    File route (CLI / cron / notebooks):
        ``manual_minutes_paths`` and ``minute_override_paths`` feed CSVs
        through the minutes-contract seam. Explicit paths win outright (and
        must exist), ``[]`` disables that input for the run, and ``None``
        preserves the legacy defaults (config path + gw37-38 file for manual
        inputs; ``minute_overrides.csv`` auto-discovery for overrides).

    In-memory route (Admin Panel / API):
        ``manual_minutes_states`` and ``minute_override_states`` pass
        already-validated contract states (or raw JSON dicts -- the contract
        coerces them) directly to the engine, bypassing the CSV adapters and
        all path discovery. Mutually exclusive with the path parameters.
        ``minutes_model_bundle`` supplies a pre-loaded minutes model (skip
        the per-run disk load in a warm process) and ``write_snapshot=False``
        suppresses the snapshot dump so the run performs no file writes.
    """
    client = FplClient(config=config)
    bootstrap = client.bootstrap()
    tables = bootstrap_tables(bootstrap)
    fixtures = pd.DataFrame(client.fixtures())
    shot_profiles = pd.DataFrame()

    if config.use_understat_profiles:
        try:
            shot_profiles = build_understat_shot_profiles(
                league=config.understat_league,
                season=config.understat_season,
                cache_dir=config.understat_cache_dir,
                include_big_chances=config.include_big_chance_profiles,
            )
            tables["players"] = attach_understat_profiles_to_players(tables["players"], tables["teams"], shot_profiles)
        except Exception as exc:
            tables["players"] = tables["players"].copy()
            tables["players"]["understat_profile_matched"] = False
            tables["players"]["understat_profile_error"] = str(exc)

    event = current_event(tables["events"])
    if event is not None:
        upcoming = fixtures.loc[(fixtures["event"].isna()) | (fixtures["event"] >= event)].copy()
    else:
        upcoming = fixtures.copy()
    event_numbers = pd.to_numeric(upcoming["event"], errors="coerce")
    if config.projection_start_gw is not None:
        upcoming = upcoming.loc[event_numbers >= int(config.projection_start_gw)].copy()
        event_numbers = pd.to_numeric(upcoming["event"], errors="coerce")
    if config.projection_end_gw is not None:
        upcoming = upcoming.loc[event_numbers <= int(config.projection_end_gw)].copy()

    history_by_player = {}
    if config.use_fpl_player_history:
        history_candidates = tables["players"].copy()
        history_candidates["_minutes"] = pd.to_numeric(history_candidates.get("minutes", 0), errors="coerce").fillna(0)
        history_candidates["_selected"] = pd.to_numeric(history_candidates.get("selected_by_percent", 0), errors="coerce").fillna(0)
        history_candidates = history_candidates.loc[
            (history_candidates["_minutes"] > 0) | (history_candidates["_selected"] >= 0.1)
        ].head(int(config.max_history_players))
        for element_id in history_candidates["id"].dropna().astype(int):
            try:
                summary = client.element_summary(int(element_id))
                history = pd.DataFrame(summary.get("history", []))
                if not history.empty:
                    history_by_player[int(element_id)] = history
            except Exception:
                continue

    tables["players"] = attach_recent_player_form(tables["players"], history_by_player)

    team_strength = build_team_strength(
        tables["players"],
        tables["teams"],
        form_blend_weight=config.form_blend_weight,
    )
    team_assist_factors = build_team_assist_factors(
        tables["players"],
        tables["teams"],
        league_prior=config.team_assist_factor,
        form_blend_weight=config.form_blend_weight,
    )
    fixtures_forecast = forecast_fixture_lambdas(upcoming, team_strength)
    fixtures_forecast = apply_market_odds_projections(fixtures_forecast, tables["teams"], config)
    if config.use_elevenify_projection_file:
        fixtures_forecast = apply_elevenify_team_projections(
            fixtures_forecast,
            tables["teams"],
            path=config.elevenify_projection_path,
            start_gw=config.projection_start_gw,
            end_gw=config.projection_end_gw,
            assist_factor=config.team_assist_factor,
            team_assist_factors=team_assist_factors,
        )
    if config.use_external_team_projection_files:
        fixtures_forecast = apply_external_team_projections(
            fixtures_forecast,
            tables["teams"],
            attack_path=config.external_attack_projection_path,
            defense_path=config.external_defense_projection_path,
            start_gw=config.projection_start_gw,
        )
    else:
        fixtures_forecast = fixtures_forecast.copy()
        fixtures_forecast["projection_source"] = fixtures_forecast.get("market_odds_source", "live_fpl_strength")

    gameweeks = sorted(pd.to_numeric(upcoming["event"], errors="coerce").dropna().astype(int).unique().tolist())
    if config.write_player_minutes_input_template:
        write_player_minutes_inputs(
            config.player_minutes_input_path,
            tables["players"],
            tables["teams"],
            history_by_player=history_by_player,
            gameweeks=gameweeks,
            overwrite=config.overwrite_player_minutes_input_template,
        )

    # Acquisition ends here; everything below the ProjectionInputs boundary
    # is the shared core. Minutes input resolution (file/in-memory dispatch)
    # and the optional bundle load are live-adapter concerns (ADR-0001: I/O
    # at the boundary), so they happen before the contract is sealed.
    minutes_run_inputs = resolve_minutes_run_inputs(
        config,
        manual_paths=manual_minutes_paths,
        override_paths=minute_override_paths,
        manual_states=manual_minutes_states,
        override_states=minute_override_states,
    )
    minutes_bundle = minutes_model_bundle
    if minutes_bundle is None and config.minutes_model_path.exists():
        minutes_bundle = load_minutes_bundle(config.minutes_model_path)

    stages = run_projection_stages(
        ProjectionInputs(
            players=tables["players"],
            teams=tables["teams"],
            fixtures_forecast=fixtures_forecast,
            history_by_player=history_by_player,
            rulebook=CURRENT_RULEBOOK,
            minutes_inputs=minutes_run_inputs,
            minutes_model_bundle=minutes_bundle,
        ),
        config=config,
        include_mc=include_mc,
    )
    player_fixture = stages["player_fixture"]
    weekly = stages["weekly"]
    mc = stages["monte_carlo"]
    if config.use_ml_predictions:
        weekly = attach_live_ml_predictions(weekly, tables["players"], player_fixture, tables["teams"], config)

    results = {
        "events": tables["events"],
        "teams": tables["teams"],
        "players": tables["players"],
        "fixtures_forecast": fixtures_forecast,
        "shot_profiles": shot_profiles,
        "player_fixture": player_fixture,
        "weekly": weekly,
        "monte_carlo": mc,
    }
    if write_snapshot:
        _write_projection_snapshot(results, event, config)
    return results
