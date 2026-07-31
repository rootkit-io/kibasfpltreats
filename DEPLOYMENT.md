# Deployment — kibasfpltreats

> **Production (VPS + HTTPS):** see **[DEPLOYMENT_VPS.md](DEPLOYMENT_VPS.md)** —
> Caddy terminates TLS and is the only public listener; the port mappings
> below apply to **local development only** (restored via
> `docker-compose.override.yml`).

Containerized stack for the FPL projection platform:

| Service     | Source             | Image base        | Host port | Purpose                                   |
|-------------|--------------------|-------------------|-----------|-------------------------------------------|
| `db`        | —                  | `postgres:16`     | (internal)| Projection persistence (runs, published views) |
| `backend`   | `./backend`        | `python:3.12-slim`| `8000`    | FastAPI Admin + public API (`uvicorn app.main:app`)|
| `frontend`  | `./apps/admin`     | `node:20-alpine`  | `3000`    | Next.js Admin Panel (BFF proxies to backend) |
| `dashboard` | `./apps/dashboard` | `node:20-alpine`  | `3001`    | Next.js public Projections Dashboard (shell) |
| `landing`   | `./landing`        | `nginx:alpine`    | `8080`    | Static landing site                        |

## Prerequisites

- Docker Engine 24+ with the Compose plugin (`docker compose version`).
- ~2 GB free disk for images (the backend image bakes in `models/` and `data/`).

## 1. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and set a real secret:

```dotenv
ADMIN_API_TOKEN=<generate-a-long-random-string>
POSTGRES_PASSWORD=<something-not-default>
```

`ADMIN_API_TOKEN` is **required** — compose refuses to start without it, and
the backend's admin endpoints fail closed (503) if it is unset at runtime.
The frontend BFF sends the same token upstream, so one value covers both.

## 2. Build and launch

From the repository root:

```bash
docker compose up --build
```

Add `-d` to run detached:

```bash
docker compose up --build -d
```

What happens on first boot:

1. `db` starts, applies `backend/db/migrations/*.sql` to a fresh volume, and
   becomes healthy via `pg_isready`.
2. `backend` builds and starts **only after** `db` reports healthy. Before
   Uvicorn starts, it applies any later migrations to persistent volumes,
   then loads the minutes-model bundle and connects using `DATABASE_URL`.
3. `frontend` (multi-stage standalone Next.js build) starts and proxies
   server-side API calls to `http://backend:8000`.
4. `landing` serves the static site.

## 3. Verify

```bash
# Backend liveness (no auth required)
curl http://localhost:8000/api/v1/health
# -> {"status":"ok","minutes_model_loaded":true}

# Public projections API (no auth; 404 until a run is published)
curl http://localhost:8000/api/v1/public/projections/latest

# Admin Panel
open http://localhost:3000

# Public dashboard (shell)
open http://localhost:3001

# Landing page
open http://localhost:8080

# Service status / health
docker compose ps
```

An authenticated smoke call (admin endpoints use the `X-Admin-Token` header):

```bash
curl -X POST http://localhost:8000/api/v1/admin/projections/run \
  -H "X-Admin-Token: $ADMIN_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"manual_minutes": [], "overrides": [], "include_mc": false}'
```

## 4. Day-2 operations

```bash
docker compose logs -f backend      # tail one service's logs
docker compose restart backend      # restart after config changes
docker compose up --build backend   # rebuild a single service
docker compose down                 # stop the stack (data volume kept)
docker compose down -v              # stop AND delete the Postgres volume
```

Postgres data persists in the named volume `pgdata` across restarts and
rebuilds; only `docker compose down -v` (or `docker volume rm`) destroys it.
The Postgres entrypoint initializes an empty volume, while the backend startup
migrator records and applies later versioned migrations before serving traffic.

## Notes

- **Ports**: host `8000` → backend, `3000` → admin frontend, `3001` →
  public dashboard, `8080` → landing (container port 80). `db` is not exposed to the host; add a `ports:`
  mapping (`5432:5432`) to `db` in `docker-compose.yml` if you need psql
  access from the host.
- **Path resolution**: the backend anchors every default file path
  (models, CSVs, caches) to `FPL_XPTS_ROOT=/app` inside the container —
  the CWD never matters (see `backend/src/fpl_xpts/config.py`).
- **Refreshing model/data artifacts**: `models/` and `data/` are baked into
  the backend image; after retraining, rebuild with
  `docker compose up --build backend`.
