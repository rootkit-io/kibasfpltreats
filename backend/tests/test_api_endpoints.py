"""Phase 5: Admin Panel API endpoint guards.

Covers, via the framework test client:

- 200 OK: a valid Admin Panel payload reaches the five-argument in-memory
  ``run_live_projection`` call -- cached bundle injected, snapshot writes
  off, contract states delivered;
- 400 Bad Request: a contract violation (120 minutes) is caught in-handler
  and surfaced with specific field errors; the pipeline is never invoked;
- 401 Unauthorized / 503 fail-closed: the auth placeholder never ships an
  open compute endpoint;
- the bundle loads exactly once at startup, across multiple requests.
"""

import pytest

pytest.importorskip("fastapi")

from types import SimpleNamespace

import pandas as pd
from fastapi.testclient import TestClient

import fpl_xpts.api as api
from fpl_xpts.config import AppConfig
from fpl_xpts.minutes_contract import MinuteOverrideState, PlayerMinutesState
from fpl_xpts.projection_repository import RunStatus
from tests.fakes import FakeProjectionRepository

TOKEN = "test-admin-token"
AUTH = {api.ADMIN_TOKEN_HEADER: TOKEN}

VALID_PAYLOAD = {
    "manual_minutes": [
        {
            "gameweek": 1,
            "player_id": 101,
            "likely_minutes": 75,
            "start_probability": 0.8,
            "chance_of_playing": 90,  # percent; contract normalises to 0.9
        }
    ],
    "overrides": [{"gameweek": 1, "player_id": 101, "minutes": 15}],
}


@pytest.fixture
def env(monkeypatch, tmp_path):
    """A wired app: fake bundle loader (counted), fake pipeline (captured)."""
    monkeypatch.setenv(api.ADMIN_TOKEN_ENV, TOKEN)

    model_path = tmp_path / "minutes_model.pkl"
    model_path.write_bytes(b"stub")  # exists() -> startup load path is taken

    sentinel_bundle = {"sentinel": "bundle"}
    load_calls: list = []

    def _fake_load(path):
        load_calls.append(path)
        return sentinel_bundle

    monkeypatch.setattr(api, "load_minutes_bundle", _fake_load)

    runs: list[dict] = []

    def _fake_run(**kwargs):
        runs.append(kwargs)
        return {
            "weekly": pd.DataFrame([{"player_id": 101, "xPts": 5.5}]),
            "player_fixture": pd.DataFrame([{"player_id": 101, "expected_minutes": 15.0}]),
        }

    monkeypatch.setattr(api, "run_live_projection", _fake_run)
    monkeypatch.delenv(api.DATABASE_URL_ENV, raising=False)

    app = api.create_app(AppConfig(minutes_model_path=model_path))

    # Isolated persistence: one override on the optional dependency covers
    # every persistence-touching route (get_repository composes on top).
    repo = FakeProjectionRepository()
    app.dependency_overrides[api.get_optional_repository] = lambda: repo

    return SimpleNamespace(
        app=app, bundle=sentinel_bundle, load_calls=load_calls, runs=runs, repo=repo
    )


# ------------------------------------------------------------------ 200 OK


def test_run_endpoint_success(env):
    with TestClient(env.app) as client:
        response = client.post(
            "/api/v1/admin/projections/run", json=VALID_PAYLOAD, headers=AUTH
        )

    assert response.status_code == 200
    body = response.json()
    assert body["minutes_model_loaded"] is True
    assert body["manual_minutes_layers"] == 1
    assert body["override_count"] == 1
    assert body["tables"]["weekly"][0]["xPts"] == 5.5

    # The five-argument in-memory call from Phase 3, exactly.
    assert len(env.runs) == 1
    kwargs = env.runs[0]
    assert kwargs["write_snapshot"] is False
    assert kwargs["minutes_model_bundle"] is env.bundle
    assert kwargs["include_mc"] is False

    (layer,) = kwargs["manual_minutes_states"]
    (state,) = layer
    assert isinstance(state, PlayerMinutesState)
    assert state.chance_of_playing == 0.9  # percent normalised by the contract

    (override,) = kwargs["minute_override_states"]
    assert isinstance(override, MinuteOverrideState)
    assert override.minutes == 15.0


def test_empty_payload_means_no_manual_inputs_not_disk_fallback(env):
    """[] from the Admin Panel = 'no manual inputs', never the CSV route."""
    with TestClient(env.app) as client:
        response = client.post(
            "/api/v1/admin/projections/run",
            json={"manual_minutes": [], "overrides": []},
            headers=AUTH,
        )
    assert response.status_code == 200
    kwargs = env.runs[0]
    # Empty tuples (not None): the states route stays selected, so the
    # boundary never falls back to legacy CSV discovery.
    assert kwargs["manual_minutes_states"] == ()
    assert kwargs["minute_override_states"] == ()


# ----------------------------------------------------------- 400 mapping


def test_contract_violation_returns_400_with_field_errors(env):
    bad = {
        "manual_minutes": [
            {"gameweek": 1, "player_id": 101, "likely_minutes": 120, "start_probability": 0.5}
        ],
        "overrides": [],
    }
    with TestClient(env.app) as client:
        response = client.post("/api/v1/admin/projections/run", json=bad, headers=AUTH)

    assert response.status_code == 400  # explicitly mapped, never a 500
    detail = response.json()["detail"]
    assert detail["message"] == "minutes payload violates the contract"
    errors = detail["errors"]
    assert any("likely_minutes" in [str(part) for part in err["loc"]] for err in errors)
    assert any("0..90" in err["msg"] for err in errors)
    assert env.runs == []  # the pipeline was never invoked


def test_bad_override_returns_400(env):
    bad = {"manual_minutes": [], "overrides": [{"gameweek": 1, "minutes": 45}]}  # no identity
    with TestClient(env.app) as client:
        response = client.post("/api/v1/admin/projections/run", json=bad, headers=AUTH)
    assert response.status_code == 400
    assert env.runs == []


# ------------------------------------------------------------------- auth


def test_missing_token_returns_401(env):
    with TestClient(env.app) as client:
        response = client.post("/api/v1/admin/projections/run", json=VALID_PAYLOAD)
    assert response.status_code == 401
    assert env.runs == []


def test_wrong_token_returns_401(env):
    with TestClient(env.app) as client:
        response = client.post(
            "/api/v1/admin/projections/run",
            json=VALID_PAYLOAD,
            headers={api.ADMIN_TOKEN_HEADER: "wrong"},
        )
    assert response.status_code == 401
    assert env.runs == []


def test_unconfigured_token_fails_closed_503(env, monkeypatch):
    monkeypatch.delenv(api.ADMIN_TOKEN_ENV)
    with TestClient(env.app) as client:
        response = client.post(
            "/api/v1/admin/projections/run", json=VALID_PAYLOAD, headers=AUTH
        )
    assert response.status_code == 503  # never an open compute endpoint
    assert env.runs == []


# ------------------------------------------------------- bundle lifecycle


def test_bundle_loads_exactly_once_across_requests(env):
    with TestClient(env.app) as client:
        for _ in range(3):
            response = client.post(
                "/api/v1/admin/projections/run", json=VALID_PAYLOAD, headers=AUTH
            )
            assert response.status_code == 200

    assert len(env.load_calls) == 1  # startup only -- no per-request loads
    assert all(run["minutes_model_bundle"] is env.bundle for run in env.runs)


def test_health_is_open_and_reports_bundle(env):
    with TestClient(env.app) as client:
        response = client.get("/api/v1/health")  # no auth required
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "minutes_model_loaded": True}


# ---------------------------------------------------- draft saving (Phase 8)


def test_preview_run_does_not_touch_the_repository(env):
    with TestClient(env.app) as client:
        response = client.post(
            "/api/v1/admin/projections/run", json=VALID_PAYLOAD, headers=AUTH
        )
    assert response.status_code == 200
    assert response.json()["run_id"] is None
    assert env.repo.list_runs() == []


def test_save_as_draft_returns_run_id_and_persists(env):
    payload = {**VALID_PAYLOAD, "save_as_draft": True, "season": "2627", "notes": "gw1"}
    with TestClient(env.app) as client:
        response = client.post(
            "/api/v1/admin/projections/run", json=payload, headers=AUTH
        )

    assert response.status_code == 200
    run_id = response.json()["run_id"]
    assert run_id is not None
    assert response.json()["tables"]["weekly"]  # preview still returned

    record = env.repo.get_run(run_id)
    assert record.status is RunStatus.DRAFT
    assert record.metadata.season == "2627"
    assert record.metadata.source.value == "admin_api"
    assert record.metadata.manual_minutes_layers == 1
    assert record.metadata.override_count == 1
    assert record.metadata.notes == "gw1"
    # what the pipeline produced is what got saved
    assert "weekly" in env.repo.saved_results(run_id)


def test_save_as_draft_requires_season(env):
    payload = {**VALID_PAYLOAD, "save_as_draft": True}
    with TestClient(env.app) as client:
        response = client.post(
            "/api/v1/admin/projections/run", json=payload, headers=AUTH
        )
    assert response.status_code == 400
    assert "season" in str(response.json()["detail"])
    assert env.runs == []  # failed fast: no compute burned


# ------------------------------------------------------- publish endpoint


def _saved_draft(env, client) -> str:
    payload = {**VALID_PAYLOAD, "save_as_draft": True, "season": "2627"}
    response = client.post(
        "/api/v1/admin/projections/run", json=payload, headers=AUTH
    )
    return response.json()["run_id"]


def test_publish_endpoint_publishes_a_draft(env):
    with TestClient(env.app) as client:
        run_id = _saved_draft(env, client)
        response = client.post(
            f"/api/v1/admin/projections/runs/{run_id}/publish", headers=AUTH
        )

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == run_id
    assert body["status"] == "published"
    assert body["published_at"] is not None

    assert env.repo.get_run(run_id).status is RunStatus.PUBLISHED
    assert env.repo.latest_published_run().run_id == run_id


def test_publish_unknown_run_returns_404(env):
    with TestClient(env.app) as client:
        response = client.post(
            "/api/v1/admin/projections/runs/00000000-0000-0000-0000-000000000000/publish",
            headers=AUTH,
        )
    assert response.status_code == 404


def test_publish_archived_run_returns_409(env):
    with TestClient(env.app) as client:
        run_id = _saved_draft(env, client)
        env.repo.archive_run(run_id)
        response = client.post(
            f"/api/v1/admin/projections/runs/{run_id}/publish", headers=AUTH
        )
    assert response.status_code == 409
    assert env.repo.get_run(run_id).status is RunStatus.ARCHIVED


def test_publish_requires_admin_token(env):
    with TestClient(env.app) as client:
        run_id = _saved_draft(env, client)
        response = client.post(f"/api/v1/admin/projections/runs/{run_id}/publish")
    assert response.status_code == 401
    assert env.repo.get_run(run_id).status is RunStatus.DRAFT


# ------------------------------------------- unconfigured persistence


def test_without_database_preview_works_but_draft_and_publish_fail_closed(
    env, monkeypatch, tmp_path
):
    """No DATABASE_URL, no override: previews fine, persistence 503."""
    bare_app = api.create_app(
        AppConfig(minutes_model_path=tmp_path / "missing_model.pkl")
    )

    with TestClient(bare_app) as client:
        preview = client.post(
            "/api/v1/admin/projections/run", json=VALID_PAYLOAD, headers=AUTH
        )
        assert preview.status_code == 200
        assert preview.json()["run_id"] is None

        draft = client.post(
            "/api/v1/admin/projections/run",
            json={**VALID_PAYLOAD, "save_as_draft": True, "season": "2627"},
            headers=AUTH,
        )
        assert draft.status_code == 503

        publish = client.post(
            "/api/v1/admin/projections/runs/whatever/publish", headers=AUTH
        )
        assert publish.status_code == 503


# --------------------------------------------- run history (Phase 10)


def test_list_runs_empty_returns_empty_list(env):
    with TestClient(env.app) as client:
        response = client.get("/api/v1/admin/projections/runs", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {"runs": []}


def test_list_runs_returns_metadata_most_recent_first(env):
    with TestClient(env.app) as client:
        first = _saved_draft(env, client)
        second = _saved_draft(env, client)
        client.post(f"/api/v1/admin/projections/runs/{second}/publish", headers=AUTH)

        response = client.get("/api/v1/admin/projections/runs", headers=AUTH)

    assert response.status_code == 200
    runs = response.json()["runs"]
    assert [run["run_id"] for run in runs] == [second, first]

    newest = runs[0]
    assert newest["season"] == "2627"
    assert newest["status"] == "published"
    assert newest["created_at"] is not None
    assert newest["published_at"] is not None
    assert newest["gameweek"] is not None  # scoped runs carry a GW label
    # metadata only: the history list never hauls fact tables
    assert "tables" not in newest


def test_list_runs_respects_limit(env):
    with TestClient(env.app) as client:
        for _ in range(3):
            _saved_draft(env, client)
        response = client.get(
            "/api/v1/admin/projections/runs", params={"limit": 2}, headers=AUTH
        )
    assert response.status_code == 200
    assert len(response.json()["runs"]) == 2


def test_list_runs_requires_admin_token(env):
    with TestClient(env.app) as client:
        response = client.get("/api/v1/admin/projections/runs")
    assert response.status_code == 401


def test_get_run_rehydrates_preview_tables(env):
    with TestClient(env.app) as client:
        run_id = _saved_draft(env, client)
        response = client.get(
            f"/api/v1/admin/projections/runs/{run_id}", headers=AUTH
        )

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == run_id
    assert body["season"] == "2627"
    assert body["status"] == "draft"

    # same tables shape as the run endpoint's preview -> the grid re-hydrates
    assert body["tables"]["weekly"] == [{"player_id": 101, "xPts": 5.5}]
    assert body["tables"]["player_fixture"] == [
        {"player_id": 101, "expected_minutes": 15.0}
    ]


def test_get_run_unknown_returns_404(env):
    with TestClient(env.app) as client:
        response = client.get(
            "/api/v1/admin/projections/runs/00000000-0000-0000-0000-000000000000",
            headers=AUTH,
        )
    assert response.status_code == 404


def test_get_run_requires_admin_token(env):
    with TestClient(env.app) as client:
        run_id = _saved_draft(env, client)
        response = client.get(f"/api/v1/admin/projections/runs/{run_id}")
    assert response.status_code == 401


def test_history_endpoints_fail_closed_without_database(monkeypatch, tmp_path):
    monkeypatch.setenv(api.ADMIN_TOKEN_ENV, TOKEN)
    monkeypatch.delenv(api.DATABASE_URL_ENV, raising=False)
    bare_app = api.create_app(
        AppConfig(minutes_model_path=tmp_path / "missing_model.pkl")
    )
    with TestClient(bare_app) as client:
        listing = client.get("/api/v1/admin/projections/runs", headers=AUTH)
        assert listing.status_code == 503
        detail = client.get(
            "/api/v1/admin/projections/runs/00000000-0000-0000-0000-000000000000",
            headers=AUTH,
        )
        assert detail.status_code == 503
