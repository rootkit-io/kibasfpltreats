"""Projection persistence interface (Phase 6 -- design only, no implementation).

This module defines the seam between the projection engine's output and the
public dashboard's database. It is the WRITE side and the admin lifecycle
only:

- ``save_run``      -- persist one ``run_live_projection`` output atomically
- ``publish_run``   -- make a saved run the one the dashboard serves
- read helpers      -- the minimum the Admin Panel needs to manage runs

The public dashboard does NOT read through this interface. It reads the
``published_*`` SQL views defined in ``db/schema.sql`` directly -- the views
are the read contract, this protocol is the write contract. Keeping the two
apart keeps this interface narrow and lets the read path be tuned (indexes,
caching, replicas) without touching Python.

Implementation note (future phase): a concrete ``PostgresProjectionRepository``
will implement this protocol. Nothing in this module may import a database
driver -- the protocol is dependency-free by design so the API layer can be
tested against an in-memory fake.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, NewType, Protocol, Sequence, runtime_checkable

import pandas as pd

#: Opaque run identifier (uuid string in the Postgres implementation).
RunId = NewType("RunId", str)


class RunStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class RunSource(str, Enum):
    ADMIN_API = "admin_api"
    CLI = "cli"
    NOTEBOOK = "notebook"


@dataclass(frozen=True)
class RunMetadata:
    """Everything about a run that is not in the result tables.

    Mirrors the ``projection_runs`` header row. ``season`` is the short FPL
    season code (e.g. ``'2627'``): FPL recycles ids every year, so every
    dimension and fact row this run produces is scoped by it. ``inputs``
    carries the provenance summary from the minutes boundary (which manual
    layers and overrides fed the run) and is stored as JSONB -- it is audit
    data, not a query surface.
    """

    season: str
    source: RunSource
    gw_start: int | None
    gw_end: int | None
    n_sim: int
    include_mc: bool
    minutes_model_loaded: bool
    manual_minutes_layers: int = 0
    override_count: int = 0
    inputs: Mapping[str, Any] = field(default_factory=dict)
    notes: str | None = None


@dataclass(frozen=True)
class RunRecord:
    """A saved run as the Admin Panel sees it."""

    run_id: RunId
    status: RunStatus
    created_at: datetime
    published_at: datetime | None
    metadata: RunMetadata


@runtime_checkable
class ProjectionRepository(Protocol):
    """Write-side contract for persisting projection runs.

    Semantics every implementation MUST honour:

    - **Atomicity.** ``save_run`` commits the run header, dimension upserts,
      and all fact rows in one transaction. A failure anywhere rolls back
      everything; there are no partially saved runs.
    - **Immutability.** Fact rows are never updated after ``save_run``.
      Corrections are a new run. ``publish_run``/``archive_run`` touch only
      the run header (status, published_at).
    - **Latest published wins.** Publishing does not delete or modify other
      runs; the dashboard's views resolve the current run by
      ``published_at``. Rollback is republishing an earlier run.
    - **Dimension upserts are non-destructive.** Player/team/fixture rows are
      inserted or updated from the run's tables, never deleted (facts from
      older runs reference them).
    """

    def save_run(
        self,
        results: Mapping[str, pd.DataFrame],
        metadata: RunMetadata,
    ) -> RunId:
        """Persist one ``run_live_projection`` output as a draft run.

        ``results`` is the pipeline's result mapping; the implementation
        consumes:

        ==================  =========================================
        results key         destination
        ==================  =========================================
        ``events``          ``gameweeks`` (upsert)
        ``teams``           ``teams`` (upsert)
        ``players``         ``players`` (upsert)
        ``fixtures_forecast``  ``fixtures`` (upsert) + ``fixture_forecasts``
        ``player_fixture``  ``player_fixture_projections``
        ``weekly``          ``player_gameweek_projections``
        ``monte_carlo``     ``player_gameweek_simulations`` (skipped when
                            empty / ``include_mc`` was False)
        ``shot_profiles``   not persisted (re-derivable enrichment)
        ==================  =========================================

        Returns the new run's id; the run is left in ``DRAFT`` status.
        """
        ...

    def publish_run(self, run_id: RunId) -> None:
        """Mark a draft run as published (sets ``published_at`` to now).

        Raises ``KeyError`` if the run does not exist and ``ValueError`` if
        it is archived. Publishing an already-published run refreshes its
        ``published_at`` (this is how rollback-by-republish works).
        """
        ...

    def archive_run(self, run_id: RunId) -> None:
        """Retire a run from the publishable pool (header-only change)."""
        ...

    def get_run(self, run_id: RunId) -> RunRecord | None:
        """Fetch one run header, or None."""
        ...

    def latest_published_run(self) -> RunRecord | None:
        """The run the dashboard is currently serving, or None."""
        ...

    def list_runs(self, limit: int = 20) -> Sequence[RunRecord]:
        """Most recent runs first -- the Admin Panel's run history screen."""
        ...

    def load_run_tables(self, run_id: RunId) -> Mapping[str, pd.DataFrame] | None:
        """Reload a saved run's result tables for preview re-hydration.

        The Admin Panel's history screen uses this to repopulate the preview
        grids for a past run without re-computing anything. Returns the
        persisted fact tables keyed by their ``results`` names (``weekly``,
        ``player_fixture``, ``monte_carlo``, ``fixtures_forecast``) with the
        original frame column names, or ``None`` for an unknown run.

        This is a read helper on the write-side contract (Admin lifecycle
        only); the public dashboard still reads the ``published_*`` views.
        Implementations MUST NOT mutate anything here.
        """
        ...
