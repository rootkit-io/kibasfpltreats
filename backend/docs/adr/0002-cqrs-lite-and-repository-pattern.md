# ADR-0002: CQRS-lite persistence — write-only repository, read-only views

- **Status:** Accepted
- **Date:** 2026-07-27
- **Scope:** projection persistence — `db/migrations/`, `projection_repository.py`,
  `postgres_repository.py`, the persistence wiring in `api.py`, and every
  future consumer of projection data
- **Related:** ADR-0001 (isolate the Minutes Engine and push all I/O to the
  boundary) — this ADR extends the same adapter discipline to the database.

## Context

The weekly workflow produces one projection run at a time from the Admin API
(a single human, weekly cadence), but the public SaaS dashboard reads that
data continuously and heavily. The two sides have opposite needs:

- **Writes** are rare, large, and must be all-or-nothing: a run is a header
  plus thousands of fact rows across four tables, and a partially saved run
  would poison the dashboard. Runs also need a review step — the admin
  previews a draft before the public sees it — and a safe rollback story.
- **Reads** are constant, latency-sensitive, and shaped around one hot query:
  "top-N players by xPts / MC mean for gameweek G, from the projection set
  currently in force."

Two hazards shaped the design:

1. **FPL recycles ids every season.** Player, team, and fixture ids are only
   unique within a season. A schema keyed on bare FPL ids silently corrupts
   the moment season 2 is loaded — last season's Haaland rows would collide
   with this season's holder of element id 101.
2. **Accretion risk.** Without a hard rule, write logic leaks into API
   routes as ad-hoc SQL and read logic leaks into Python helpers, until the
   database contract exists nowhere in particular. The minutes arc
   (ADR-0001) showed the cost of letting I/O spread; persistence starts
   disciplined instead of being disciplined later.

## Decision

A strict read/write split — CQRS-lite (one database, no event sourcing, no
separate stores; just segregated contracts):

1. **Write side: a pure protocol.** `ProjectionRepository`
   (`projection_repository.py`) is the only write path: `save_run` /
   `publish_run` / `archive_run` plus the minimal read-backs the Admin Panel
   needs (`get_run`, `latest_published_run`, `list_runs`). Semantics are
   part of the contract: `save_run` is atomic (one transaction, header +
   dimension upserts + facts, all-or-nothing); runs are **append-only and
   immutable** after save — corrections are a new run; publishing touches
   only the header. The protocol imports no database driver.
2. **The production adapter is deliberately ORM-free.**
   `PostgresProjectionRepository` uses psycopg 3 with `COPY FROM STDIN` for
   fact tables and `executemany` + `ON CONFLICT` upserts for dimensions —
   the data is already flat DataFrames; an ORM would add a mapping layer
   with nothing to map.
3. **Read side: raw PostgreSQL views.** The dashboard reads only the
   `published_*` views (`published_player_week`,
   `published_player_week_simulations`, `published_fixture_projections`,
   `published_fixture_forecasts`), which resolve the current run via
   `current_published_run` — **latest published wins**, ordered by
   `published_at`. Rollback is republishing an earlier run. The views are
   the read contract; they can be re-tuned (indexes, materialization,
   caching, replicas) without touching Python.
4. **Season-scoped identity.** Every dimension is keyed `(season, fpl_id)`
   (season code like `'2627'`); every fact row carries `season` with
   composite foreign keys; `RunMetadata.season` is required and stamped onto
   everything a run writes. Bare-FPL-id joins are structurally impossible.
5. **Injection at the edge.** The Admin API receives the repository through
   FastAPI dependencies: a lifespan-owned connection pool
   (`DATABASE_URL`), a request-scoped checkout in
   `get_optional_repository`, and a fail-closed strict variant
   (`get_repository`, 503 when unconfigured). Previews never require a
   database.

## Consequences

- **API routes must never write direct SQL.** All writes go through the
  protocol. A route needing a new write capability extends the protocol
  (and both implementations, and the contract tests) — it does not open a
  cursor.
- **Dashboard queries must never use the repository.** Public reads go to
  the `published_*` views (or future views added by migration). If the
  dashboard needs a new shape, that is a schema/view change, not a Python
  endpoint that re-aggregates facts.
- **The fake is load-bearing.** `tests/fakes.py::FakeProjectionRepository`
  passes the same contract-test suite as the Postgres adapter
  (`tests/test_postgres_repository.py` parametrizes every protocol test over
  both). API tests inject it via one `dependency_overrides` entry and stay
  fast and DB-free. Any protocol change must keep both implementations
  green against the shared suite — a fake that drifts from the adapter is a
  bug, not a convenience.
- **Schema changes are migrations.** `db/migrations/` is append-only
  numbered SQL; the test harness applies migrations to a real PostgreSQL via
  pytest-postgresql, so view and constraint behaviour is tested against the
  engine that runs in production (this caught real bugs: pandas float
  upcasts breaking `smallint` COPY, `numeric` leaking `Decimal` to clients).
- **Immutability is the audit trail.** Because runs are append-only with
  provenance (`inputs` JSONB from the minutes boundary), "what did the
  dashboard show in GW12 and why" is a query, not an investigation.
  Storage is accepted as cheap; there is no archival machinery by decision.
- Future reviews should not re-propose: an ORM layer for these writes,
  routing dashboard reads through Python, merging the read and write
  contracts, or dropping season from the keys. Those shapes were
  considered and rejected here.
