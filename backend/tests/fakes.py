"""Test doubles for the persistence seam (Phase 7).

``FakeProjectionRepository`` adheres strictly to the ``ProjectionRepository``
protocol semantics (atomicity is trivial in memory; publish/archive rules,
latest-published-wins ordering, and immutability are honoured for real) so
API- and admin-flow tests stay fast and DB-free.

Also hosts the shared sample-run builders used by both the fake's contract
tests and the Postgres adapter's integration tests -- one source of truth for
"a small but complete run_live_projection output".
"""

from __future__ import annotations

import itertools
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from typing import Mapping

import pandas as pd

from fpl_xpts.projection_repository import (
    ProjectionRepository,
    RunId,
    RunMetadata,
    RunRecord,
    RunSource,
    RunStatus,
)


class FakeProjectionRepository:
    """Dict-backed, protocol-faithful projection repository."""

    def __init__(self) -> None:
        self._runs: dict[RunId, RunRecord] = {}
        self._results: dict[RunId, dict[str, pd.DataFrame]] = {}
        self._save_seq = itertools.count(1)
        self._publish_seq = itertools.count(1)
        self._save_order: dict[RunId, int] = {}
        self._publish_order: dict[RunId, int] = {}

    # ------------------------------------------------------------- protocol

    def save_run(
        self,
        results: Mapping[str, pd.DataFrame],
        metadata: RunMetadata,
    ) -> RunId:
        run_id = RunId(str(uuid.uuid4()))
        # Copy frames: saved runs are immutable even if the caller mutates.
        self._results[run_id] = {
            name: frame.copy()
            for name, frame in results.items()
            if isinstance(frame, pd.DataFrame)
        }
        self._runs[run_id] = RunRecord(
            run_id=run_id,
            status=RunStatus.DRAFT,
            created_at=datetime.now(timezone.utc),
            published_at=None,
            metadata=metadata,
        )
        self._save_order[run_id] = next(self._save_seq)
        return run_id

    def publish_run(self, run_id: RunId) -> None:
        record = self._require(run_id)
        if record.status is RunStatus.ARCHIVED:
            raise ValueError(f"run {run_id} is archived and cannot be published")
        self._runs[run_id] = replace(
            record,
            status=RunStatus.PUBLISHED,
            published_at=datetime.now(timezone.utc),
        )
        # Monotonic publish ordering: republishing bumps a run back to the
        # front ("latest published wins", incl. rollback-by-republish).
        self._publish_order[run_id] = next(self._publish_seq)

    def archive_run(self, run_id: RunId) -> None:
        record = self._require(run_id)
        self._runs[run_id] = replace(
            record, status=RunStatus.ARCHIVED, published_at=None
        )
        self._publish_order.pop(run_id, None)

    def get_run(self, run_id: RunId) -> RunRecord | None:
        return self._runs.get(run_id)

    def latest_published_run(self) -> RunRecord | None:
        published = [
            run_id
            for run_id, record in self._runs.items()
            if record.status is RunStatus.PUBLISHED
        ]
        if not published:
            return None
        current = max(published, key=lambda run_id: self._publish_order[run_id])
        return self._runs[current]

    def list_runs(self, limit: int = 20) -> list[RunRecord]:
        ordered = sorted(
            self._runs, key=lambda run_id: self._save_order[run_id], reverse=True
        )
        return [self._runs[run_id] for run_id in ordered[:limit]]

    def load_run_tables(self, run_id: RunId) -> dict[str, pd.DataFrame] | None:
        """Re-hydrate the persisted result tables (copies: immutability)."""
        if run_id not in self._runs:
            return None
        return {
            name: frame.copy() for name, frame in self._results[run_id].items()
        }

    # --------------------------------------------------------- test helpers

    def saved_results(self, run_id: RunId) -> dict[str, pd.DataFrame]:
        """Inspect what was persisted (test-only; not part of the protocol)."""
        return self._results[run_id]

    def _require(self, run_id: RunId) -> RunRecord:
        record = self._runs.get(run_id)
        if record is None:
            raise KeyError(f"unknown run: {run_id}")
        return record


assert isinstance(FakeProjectionRepository(), ProjectionRepository)


# ---------------------------------------------------------------------------
# Shared sample data: one small but complete run_live_projection output.
# Player 101 (Haaland, team 1) has a Double Gameweek (fixtures 10 and 12);
# player 202 (Saka, team 2) has a single fixture (11). All in GW 1.
# ---------------------------------------------------------------------------

SEASON = "2627"


def make_metadata(**overrides) -> RunMetadata:
    kwargs = dict(
        season=SEASON,
        source=RunSource.ADMIN_API,
        gw_start=1,
        gw_end=1,
        n_sim=1000,
        include_mc=True,
        minutes_model_loaded=True,
        manual_minutes_layers=1,
        override_count=1,
        inputs={"manual_minutes_layers": 1, "override_count": 1},
    )
    kwargs.update(overrides)
    return RunMetadata(**kwargs)


def _points_block(xpts: float) -> dict:
    return {
        "xPts": xpts,
        "AppPts": 2.0,
        "GoalPts": xpts * 0.5,
        "AssistPts": 0.6,
        "CSPts": 0.4,
        "SavePts": 0.0,
        "DefconPts": 0.1,
        "CardPts": -0.1,
        "PenMissPts": 0.0,
        "ConcedePts": -0.2,
    }


def make_sample_results(web_names: Mapping[int, str] | None = None) -> dict[str, pd.DataFrame]:
    web_names = dict(web_names or {101: "Haaland", 202: "Saka"})
    events = pd.DataFrame(
        [{"id": 1, "deadline_time": "2025-08-15T17:30:00Z", "finished": False}]
    )
    teams = pd.DataFrame(
        [
            {"id": 1, "name": "Manchester City", "short_name": "MCI"},
            {"id": 2, "name": "Arsenal", "short_name": "ARS"},
        ]
    )
    players = pd.DataFrame(
        [
            {
                "id": 101,
                "first_name": "Erling",
                "second_name": "Haaland",
                "web_name": web_names[101],
                "now_cost": 141,
                "selected_by_percent": 55.3,
                "status": "a",
                "chance_of_playing_this_round": 100,
                "news": "",
            },
            {
                "id": 202,
                "first_name": "Bukayo",
                "second_name": "Saka",
                "web_name": web_names[202],
                "now_cost": 105,
                "selected_by_percent": 32.1,
                "status": "a",
                "chance_of_playing_this_round": 100,
                "news": "",
            },
        ]
    )
    fixtures_forecast = pd.DataFrame(
        [
            {
                "id": 10, "event": 1, "team_h": 1, "team_a": 2,
                "kickoff_time": "2025-08-16T14:00:00Z",
                "home_xg": 1.9, "away_xg": 1.1,
                "home_cs_prob": 0.33, "away_cs_prob": 0.15,
                "projection_source": "live_fpl_strength",
            },
            {
                "id": 11, "event": 1, "team_h": 2, "team_a": 1,
                "kickoff_time": "2025-08-16T16:30:00Z",
                "home_xg": 1.4, "away_xg": 1.5,
                "home_cs_prob": 0.22, "away_cs_prob": 0.25,
                "projection_source": "live_fpl_strength",
            },
            {
                "id": 12, "event": 1, "team_h": 1, "team_a": 2,
                "kickoff_time": "2025-08-19T19:00:00Z",
                "home_xg": 1.8, "away_xg": 1.0,
                "home_cs_prob": 0.35, "away_cs_prob": 0.14,
                "projection_source": "live_fpl_strength",
            },
        ]
    )

    def _pf_row(player_id, team, opponent, fixture, xpts, was_home=True):
        return {
            "player_id": player_id, "team": team, "opponent": opponent,
            "fixture": fixture, "event": 1, "was_home": was_home,
            "position": "FWD" if player_id == 101 else "MID",
            "kickoff_time": "2025-08-16T14:00:00Z",
            "expected_minutes": 80.0, "likely_minutes": 85.0,
            "start_probability": 0.9, "play_probability": 0.95,
            "minutes_model_source": "trained_four_output_model",
            "xG": 0.7, "xA": 0.2, "xGA_exp": 0.9, "cs_prob": 0.3,
            "P1_GA": 0.59, "prior_based": False, "prior_source": "observed_pl",
            **_points_block(xpts),
        }

    player_fixture = pd.DataFrame(
        [
            _pf_row(101, 1, 2, 10, 5.1),
            _pf_row(101, 1, 2, 12, 4.9),
            _pf_row(202, 2, 1, 11, 5.5),
        ]
    )
    weekly = pd.DataFrame(
        [
            {
                "event": 1, "player_id": 101, "web_name": web_names[101],
                "position": "FWD", "team": 1, "fixtures": 2,
                "expected_minutes": 160.0, "xG": 1.4, "xA": 0.4,
                "xGA_exp": 1.8, "P1_GA": 0.83,
                "P_return": 0.7, "P_haul": 0.25, "ml_xpts": None,
                **_points_block(10.0),
            },
            {
                "event": 1, "player_id": 202, "web_name": web_names[202],
                "position": "MID", "team": 2, "fixtures": 1,
                "expected_minutes": 80.0, "xG": 0.5, "xA": 0.4,
                "xGA_exp": 0.9, "P1_GA": 0.59,
                "P_return": 0.5, "P_haul": 0.12, "ml_xpts": None,
                **_points_block(5.5),
            },
        ]
    )

    def _mc_row(player_id, mean):
        return {
            "event": 1, "player_id": player_id,
            "MC_MeanPts": mean, "MC_StdPts": 3.0,
            "MC_Floor": 2.0, "MC_P25": mean - 2, "MC_P75": mean + 2,
            "MC_Upside": mean + 5, "MC_MinPts": 0.0, "MC_MaxPts": mean + 12,
            "MC_P1_Return": 0.7, "MC_P2_Return": 0.3,
            "P_return": 0.6, "P_haul": 0.2,
            "Bracket_LE_2": 0.2, "Bracket_3_to_6": 0.4, "Bracket_7_to_9": 0.2,
            "Bracket_10_to_14": 0.15, "Bracket_15_plus": 0.05,
        }

    monte_carlo = pd.DataFrame([_mc_row(101, 9.8), _mc_row(202, 5.4)])

    return {
        "events": events,
        "teams": teams,
        "players": players,
        "fixtures_forecast": fixtures_forecast,
        "player_fixture": player_fixture,
        "weekly": weekly,
        "monte_carlo": monte_carlo,
        "shot_profiles": pd.DataFrame(),  # not persisted, present for realism
    }
