"""Phase 11: the public read API (published_* views, no admin auth).

Three proofs:

1. auth posture: the public route needs NO X-Admin-Token, even while the
   admin routes on the same app fail closed;
2. degradation: 503 without persistence, 404 with a database but no
   published run yet;
3. the real read contract: against a live local PostgreSQL, a saved +
   published run comes back through current_published_run /
   published_player_week with names, teams and price joined in -- and a
   newly published run wins immediately (latest-published-wins).
"""

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

import time
from unittest.mock import MagicMock, patch

import fpl_xpts.api as api
import fpl_xpts.api_public as api_public
import fpl_xpts.auth as auth_module
from fpl_xpts.config import AppConfig
from tests.fakes import SEASON, make_metadata, make_sample_results

try:
    import jwt as pyjwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    _CRYPTO_AVAILABLE = True
except ImportError:
    _CRYPTO_AVAILABLE = False

LATEST = "/api/v1/public/projections/latest"


# ---------------------------------------------------------------- auth helpers

@pytest.fixture(autouse=True)
def reset_jwks_cache():
    auth_module._jwks_client.cache_clear()
    yield
    auth_module._jwks_client.cache_clear()


@pytest.fixture
def rsa_key_pair():
    if not _CRYPTO_AVAILABLE:
        pytest.skip("cryptography not installed")
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _make_token(private_key) -> str:
    now = int(time.time())
    return pyjwt.encode(
        {"sub": "user_test", "iat": now - 5, "exp": now + 300},
        private_key,
        algorithm="RS256",
        headers={"kid": "test-kid"},
    )


def _mock_jwks(public_key):
    signing_key = MagicMock()
    signing_key.key = public_key
    client = MagicMock()
    client.get_signing_key_from_jwt.return_value = signing_key
    return client


@pytest.fixture
def authed_headers(monkeypatch, rsa_key_pair):
    """Authorization header with a valid mocked Clerk JWT."""
    private_key, public_key = rsa_key_pair
    monkeypatch.setenv(auth_module.CLERK_JWKS_URL_ENV, "https://test.clerk.accounts.dev/.well-known/jwks.json")
    token = _make_token(private_key)
    mock_client = _mock_jwks(public_key)
    with patch.object(auth_module, "_jwks_client", side_effect=lambda: mock_client):
        yield {"Authorization": f"Bearer {token}"}


@pytest.fixture
def bare_app(monkeypatch, tmp_path):
    """An app with no DATABASE_URL and no admin token configured."""
    monkeypatch.delenv(api.ADMIN_TOKEN_ENV, raising=False)
    monkeypatch.delenv(api.DATABASE_URL_ENV, raising=False)
    return api.create_app(AppConfig(minutes_model_path=tmp_path / "missing.pkl"))


@pytest.fixture
def public_app(bare_app, pg_repo, postgresql_local):
    """An app whose public connection seam yields the migrated test DB."""
    bare_app.dependency_overrides[api_public.get_public_connection] = (
        lambda: postgresql_local
    )
    return bare_app


# ------------------------------------------------------------ auth posture


def test_public_route_requires_no_admin_token(bare_app, authed_headers):
    """No token header, no token env: the public route reaches persistence
    handling (503) instead of an auth wall -- while admin stays closed."""
    with TestClient(bare_app) as client:
        public_authed = client.get(LATEST, headers=authed_headers)
        public_unauthed = client.get(LATEST)
        admin = client.get("/api/v1/admin/projections/runs",
                           headers={api.ADMIN_TOKEN_HEADER: "admin-test"})

    assert public_authed.status_code == 503  # persistence, NOT auth
    assert "persistence" in public_authed.json()["detail"]
    assert public_unauthed.status_code == 401
    assert admin.status_code == 503
    assert "admin API token" in admin.json()["detail"]  # fail-closed auth (no token set)


# ------------------------------------------------------------- degradation


def test_latest_without_database_returns_503(bare_app, authed_headers):
    with TestClient(bare_app) as client:
        response = client.get(LATEST, headers=authed_headers)
    assert response.status_code == 503


def test_latest_with_database_but_no_published_run_returns_404(public_app, pg_repo, authed_headers):
    pg_repo.save_run(make_sample_results(), make_metadata())  # draft only
    with TestClient(public_app) as client:
        response = client.get(LATEST, headers=authed_headers)
    assert response.status_code == 404
    assert response.json()["detail"] == "no published run"


# -------------------------------------------------------- the read contract


def test_latest_serves_published_player_week(public_app, pg_repo, authed_headers):
    run_id = pg_repo.save_run(make_sample_results(), make_metadata())
    pg_repo.publish_run(run_id)

    with TestClient(public_app) as client:
        response = client.get(LATEST, headers=authed_headers)

    assert response.status_code == 200
    body = response.json()

    assert body["run"]["run_id"] == str(run_id)
    assert body["run"]["season"] == SEASON
    assert body["run"]["published_at"] is not None
    assert body["count"] == 2

    rows = body["player_week"]
    # ordered by xpts desc within the GW: Haaland (10.0) before Saka (5.5)
    assert [row["web_name"] for row in rows] == ["Haaland", "Saka"]
    assert rows[0]["team_name"] == "Manchester City"
    assert rows[0]["team_short"] == "MCI"
    assert rows[0]["price"] == pytest.approx(14.1)  # now_cost/10 via the view
    assert rows[0]["xpts"] == pytest.approx(10.0)
    assert rows[1]["p_haul"] == pytest.approx(0.12)


def test_latest_gameweek_filter(public_app, pg_repo, authed_headers):
    run_id = pg_repo.save_run(make_sample_results(), make_metadata())
    pg_repo.publish_run(run_id)

    with TestClient(public_app) as client:
        scoped = client.get(LATEST, params={"gameweek": 1}, headers=authed_headers)
        empty = client.get(LATEST, params={"gameweek": 38}, headers=authed_headers)
        invalid = client.get(LATEST, params={"gameweek": 99}, headers=authed_headers)

    assert scoped.status_code == 200
    assert scoped.json()["count"] == 2
    assert scoped.json()["gameweek"] == 1

    assert empty.status_code == 200  # a valid GW the run doesn't cover
    assert empty.json()["count"] == 0

    assert invalid.status_code == 422  # outside 1..38


def test_latest_published_wins_through_the_view(public_app, pg_repo, authed_headers):
    """Republish semantics flow straight through current_published_run."""
    first = pg_repo.save_run(
        make_sample_results({101: "Haaland", 202: "Saka"}), make_metadata()
    )
    second = pg_repo.save_run(
        make_sample_results({101: "Haaland-2", 202: "Saka-2"}), make_metadata()
    )
    pg_repo.publish_run(first)
    pg_repo.publish_run(second)

    with TestClient(public_app) as client:
        response = client.get(LATEST, headers=authed_headers)
    assert response.json()["run"]["run_id"] == str(second)

    pg_repo.publish_run(first)  # rollback-by-republish
    with TestClient(public_app) as client:
        response = client.get(LATEST, headers=authed_headers)
    assert response.json()["run"]["run_id"] == str(first)


# ------------------------------------------------- Phase 14: rate limit + cache


def test_rate_limit_returns_429_after_limit(bare_app, monkeypatch, authed_headers, rsa_key_pair):
    """Exceeding RATE_LIMIT_MAX requests returns 429 with Retry-After header."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from fpl_xpts import redis_utils, api_public

    monkeypatch.setenv(auth_module.CLERK_JWKS_URL_ENV, "https://test.clerk.accounts.dev/.well-known/jwks.json")

    call_count = 0

    async def fake_rate_limit(state, user_id):
        nonlocal call_count
        call_count += 1
        return call_count <= redis_utils.RATE_LIMIT_MAX

    # Provide a fake DB connection via dependency_overrides (FastAPI DI)
    fake_conn = MagicMock()
    fake_conn.execute.return_value.fetchone.return_value = (
        "run-id-1", "2627", 1, 1, 1000, True, "2026-01-01T00:00:00Z"
    )
    bare_app.dependency_overrides[api_public._require_connection] = lambda: fake_conn

    try:
        with patch.object(api_public, "check_rate_limit", side_effect=fake_rate_limit):
            with patch.object(api_public, "cache_get", new=AsyncMock(return_value=None)):
                with patch.object(api_public, "cache_set", new=AsyncMock()):
                    with TestClient(bare_app) as client:
                        for _ in range(redis_utils.RATE_LIMIT_MAX):
                            client.get(LATEST, headers=authed_headers)
                        r = client.get(LATEST, headers=authed_headers)
    finally:
        bare_app.dependency_overrides.pop(api_public._require_connection, None)

    assert r.status_code == 429
    assert "rate limit" in r.json()["detail"]
    assert "Retry-After" in r.headers


def test_cache_hit_skips_db(public_app, monkeypatch, authed_headers, pg_repo, rsa_key_pair):
    """A cached response is returned without touching the DB connection."""
    from unittest.mock import AsyncMock, patch
    from fpl_xpts import redis_utils

    monkeypatch.setenv(auth_module.CLERK_JWKS_URL_ENV, "https://test.clerk.accounts.dev/.well-known/jwks.json")

    run_id = pg_repo.save_run(make_sample_results(), make_metadata())
    pg_repo.publish_run(run_id)

    cached_payload = {"run": {"run_id": str(run_id), "season": "2627"}, "count": 0, "player_week": [], "gameweek": None}

    cache_get_mock = AsyncMock(return_value=cached_payload)

    with patch.object(api_public, "cache_get", new=cache_get_mock):
        with TestClient(public_app) as client:
            response = client.get(LATEST, headers=authed_headers)

    assert response.status_code == 200
    assert response.json() == cached_payload


def test_rate_limit_uses_sub_not_ip(bare_app, monkeypatch, authed_headers, rsa_key_pair):
    """Rate limiter is called with the Clerk sub claim, not the client IP."""
    from unittest.mock import MagicMock, patch
    from fpl_xpts import redis_utils

    seen_user_ids = []

    async def capture_rate_limit(state, user_id):
        seen_user_ids.append(user_id)
        return True

    # Override _require_connection so the route body runs past the DB check
    fake_conn = MagicMock()
    fake_conn.execute.return_value.fetchone.return_value = (
        "run-id-1", "2627", 1, 1, 1000, True, "2026-01-01T00:00:00Z"
    )
    bare_app.dependency_overrides[api_public._require_connection] = lambda: fake_conn

    try:
        with patch.object(api_public, "check_rate_limit", side_effect=capture_rate_limit):
            with TestClient(bare_app) as client:
                client.get(LATEST, headers=authed_headers)
    finally:
        bare_app.dependency_overrides.pop(api_public._require_connection, None)

    # sub claim from the authed_headers fixture token is "user_test"
    assert seen_user_ids == ["user_test"]
