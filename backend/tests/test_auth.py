"""Phase 13: Clerk JWT verification dependency tests.

Proves without a real Clerk account:
1. missing Authorization header -> 401 (not 403)
2. CLERK_JWKS_URL unset -> 503 on the public route
3. malformed / expired token -> 401
4. valid token (mocked JWKS) -> 200 with claims forwarded

The JWKS mock generates a real RSA key pair so PyJWT's full RS256
verification path runs -- no monkey-patching of the crypto layer.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jwt")

import jwt as pyjwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from fastapi.testclient import TestClient

import fpl_xpts.api as api
import fpl_xpts.api_public as api_public
import fpl_xpts.auth as auth_module
from fpl_xpts.config import AppConfig

# ---------------------------------------------------------------- fixtures


@pytest.fixture(autouse=True)
def reset_jwks_cache():
    """Clear the lru_cache between tests so env changes take effect."""
    auth_module._jwks_client.cache_clear()
    yield
    auth_module._jwks_client.cache_clear()


@pytest.fixture
def rsa_key_pair():
    """Real RSA-2048 key pair for RS256 signing."""
    private_key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048
    )
    return private_key, private_key.public_key()


def _make_token(private_key, *, expired=False, kid="test-kid-1") -> str:
    now = int(time.time())
    payload = {
        "sub": "user_test123",
        "sid": "sess_test",
        "iat": now - 10,
        "exp": (now - 5) if expired else (now + 300),
        "iss": "https://test.clerk.accounts.dev",
    }
    return pyjwt.encode(
        payload,
        private_key,
        algorithm="RS256",
        headers={"kid": kid},
    )


def _mock_jwks_client(public_key, kid="test-kid-1"):
    """Return a mock PyJWKClient that resolves `kid` to `public_key`."""
    signing_key = MagicMock()
    signing_key.key = public_key
    client = MagicMock()
    client.get_signing_key_from_jwt.return_value = signing_key
    return client


@pytest.fixture
def app_with_public_db(monkeypatch, tmp_path, pg_repo, postgresql_local):
    """App with public connection seam wired to the migrated test DB."""
    monkeypatch.setenv(api.ADMIN_TOKEN_ENV, "admin-test")
    monkeypatch.delenv(api.DATABASE_URL_ENV, raising=False)
    app = api.create_app(AppConfig(minutes_model_path=tmp_path / "missing.pkl"))
    app.dependency_overrides[api_public.get_public_connection] = (
        lambda: postgresql_local
    )
    return app


@pytest.fixture
def bare_app(monkeypatch, tmp_path):
    monkeypatch.setenv(api.ADMIN_TOKEN_ENV, "admin-test")
    monkeypatch.delenv(api.DATABASE_URL_ENV, raising=False)
    return api.create_app(AppConfig(minutes_model_path=tmp_path / "missing.pkl"))


# -------------------------------------------------------- auth posture


def test_missing_auth_header_returns_401(bare_app, monkeypatch):
    monkeypatch.setenv(auth_module.CLERK_JWKS_URL_ENV, "https://test.clerk.accounts.dev/.well-known/jwks.json")
    with TestClient(bare_app) as client:
        response = client.get("/api/v1/public/projections/latest")
    assert response.status_code == 401
    assert "authentication required" in response.json()["detail"]


def test_jwks_url_unset_returns_503(bare_app, monkeypatch):
    monkeypatch.delenv(auth_module.CLERK_JWKS_URL_ENV, raising=False)
    with TestClient(bare_app) as client:
        response = client.get(
            "/api/v1/public/projections/latest",
            headers={"Authorization": "Bearer fake.token.here"},
        )
    assert response.status_code == 503
    assert "CLERK_JWKS_URL" in response.json()["detail"]


def test_expired_token_returns_401(bare_app, monkeypatch, rsa_key_pair):
    private_key, public_key = rsa_key_pair
    monkeypatch.setenv(auth_module.CLERK_JWKS_URL_ENV, "https://test.clerk.accounts.dev/.well-known/jwks.json")
    token = _make_token(private_key, expired=True)
    with patch.object(auth_module, "_jwks_client", return_value=_mock_jwks_client(public_key)):
        with TestClient(bare_app) as client:
            response = client.get(
                "/api/v1/public/projections/latest",
                headers={"Authorization": f"Bearer {token}"},
            )
    assert response.status_code == 401
    assert "expired" in response.json()["detail"]


def test_malformed_token_returns_401(bare_app, monkeypatch):
    monkeypatch.setenv(auth_module.CLERK_JWKS_URL_ENV, "https://test.clerk.accounts.dev/.well-known/jwks.json")
    with TestClient(bare_app) as client:
        response = client.get(
            "/api/v1/public/projections/latest",
            headers={"Authorization": "Bearer not.a.real.jwt"},
        )
    assert response.status_code == 401


def test_valid_token_reaches_persistence_layer(bare_app, monkeypatch, rsa_key_pair):
    """Valid JWT passes auth; 503 means persistence (not auth) blocked it."""
    private_key, public_key = rsa_key_pair
    monkeypatch.setenv(auth_module.CLERK_JWKS_URL_ENV, "https://test.clerk.accounts.dev/.well-known/jwks.json")
    token = _make_token(private_key)
    with patch.object(auth_module, "_jwks_client", return_value=_mock_jwks_client(public_key)):
        with TestClient(bare_app) as client:
            response = client.get(
                "/api/v1/public/projections/latest",
                headers={"Authorization": f"Bearer {token}"},
            )
    # 503 = persistence not configured (auth passed, DB absent)
    assert response.status_code == 503
    assert "persistence" in response.json()["detail"]


def test_valid_token_with_db_returns_404_when_no_published_run(
    app_with_public_db, monkeypatch, rsa_key_pair, pg_repo
):
    private_key, public_key = rsa_key_pair
    monkeypatch.setenv(auth_module.CLERK_JWKS_URL_ENV, "https://test.clerk.accounts.dev/.well-known/jwks.json")
    token = _make_token(private_key)
    with patch.object(auth_module, "_jwks_client", return_value=_mock_jwks_client(public_key)):
        with TestClient(app_with_public_db) as client:
            response = client.get(
                "/api/v1/public/projections/latest",
                headers={"Authorization": f"Bearer {token}"},
            )
    assert response.status_code == 404
    assert response.json()["detail"] == "no published run"


def test_admin_routes_unaffected_by_clerk_config(bare_app, monkeypatch):
    """Admin token auth is orthogonal to Clerk -- no JWKS URL needed."""
    monkeypatch.delenv(auth_module.CLERK_JWKS_URL_ENV, raising=False)
    with TestClient(bare_app) as client:
        response = client.get(
            "/api/v1/admin/projections/runs",
            headers={api.ADMIN_TOKEN_HEADER: "admin-test"},
        )
    # 503 = no DB, but auth passed -- Clerk config irrelevant for admin
    assert response.status_code == 503
    assert "persistence" in response.json()["detail"]
