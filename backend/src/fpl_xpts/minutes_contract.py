"""Data contract (seam) for weekly Manual Minutes Inputs.

Phase 1 of deepening the Minutes module. This module owns:

- the strict row contract for a player's manually-declared minutes state
  (:class:`PlayerMinutesState`),
- resolution of which manual CSV(s) feed a run
  (:func:`resolve_manual_minutes_paths` -- no hardcoded paths in the pipeline),
- loud validation before the run starts (:func:`load_manual_minutes_csv`).

Since Phase 2 the validated states feed the pure precedence engine in
``minutes_engine.py``; the contract gates what may enter the seam, and the
engine never touches the file system.

Divergence from the legacy loader, by design: the legacy path silently coerced
garbage (``errors="coerce"`` -> ``fillna(0)`` -> ``clip``). The contract rejects
invalid rows with CSV line numbers so a bad weekly edit fails before the run,
not silently inside it.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from .config import BACKEND_ROOT, AppConfig

#: Magic filename previously hardcoded inside ``pipeline.run_live_projection``.
#: Kept as the default second manual-minutes source so the current weekly
#: workflow is byte-identical. Override by passing explicit paths to
#: ``run_live_projection(manual_minutes_paths=[...])``. Anchored to the
#: backend root so discovery does not depend on the process CWD.
LEGACY_EXTRA_MANUAL_MINUTES_PATH = BACKEND_ROOT / "player_minutes_inputs_gw37_to_38.csv"

#: Canonical column names of the manual minutes CSV (as written by
#: ``minute_overrides.build_player_minutes_inputs`` and edited weekly).
REQUIRED_COLUMNS = ("start", "mins")
IDENTITY_COLUMNS = ("player_id", "player_key")


class ManualMinutesError(ValueError):
    """Raised when a manual minutes CSV violates the contract."""


def _normalise_probability(value: float) -> float:
    """Mirror ``minute_overrides._manual_probability``: >1 means percent."""
    if value > 1.0:
        value = value / 100.0
    return value


def _is_blank(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _str_or_none(value: object) -> str | None:
    if _is_blank(value):
        return None
    return str(value).strip()


def _int_or_none(value: object) -> int | None:
    if _is_blank(value):
        return None
    return int(float(value))


def _float_or_none(value: object) -> float | None:
    if _is_blank(value):
        return None
    return float(value)


def _float_or_default(value: object, default: float) -> float:
    out = _float_or_none(value)
    return float(default) if out is None else out


class PlayerMinutesState(BaseModel):
    """One player's manually-declared minutes state for (at most) one gameweek.

    This is the strict per-row contract of the weekly manual minutes CSV.
    ``start_probability`` and ``chance_of_playing`` accept either 0..1 or
    percent (0..100) on input and are normalised to 0..1.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    gameweek: int | None = None
    player_id: int | None = None
    player_key: str | None = None
    player: str | None = None
    team: str | None = None
    position: str | None = None
    likely_minutes: float
    start_probability: float
    chance_of_playing: float | None = None

    @field_validator("likely_minutes")
    @classmethod
    def _minutes_in_range(cls, value: float) -> float:
        if not 0.0 <= value <= 90.0:
            raise ValueError(f"likely_minutes must be within 0..90, got {value}")
        return float(value)

    @field_validator("start_probability", "chance_of_playing")
    @classmethod
    def _probability_in_range(cls, value: float | None) -> float | None:
        if value is None:
            return None
        value = _normalise_probability(float(value))
        if not 0.0 <= value <= 1.0:
            raise ValueError(
                f"probability must be within 0..1 (or 0..100 as percent), got {value}"
            )
        return value

    @model_validator(mode="after")
    def _identity_present(self) -> "PlayerMinutesState":
        if self.player_id is None and not self.player_key:
            raise ValueError("row needs player_id or player_key")
        return self


class ManualMinutesFile(BaseModel):
    """A validated manual minutes CSV: the only object allowed through the seam."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    path: Path
    states: tuple[PlayerMinutesState, ...]


def load_manual_minutes_csv(path: Path | str) -> ManualMinutesFile:
    """Parse and validate a manual minutes CSV against the contract.

    Raises :class:`ManualMinutesError` naming missing columns or invalid rows
    (with 1-based CSV line numbers, header included) instead of silently
    coercing values the way the legacy loader did.
    """
    path = Path(path)
    if not path.exists():
        raise ManualMinutesError(f"manual minutes file not found: {path}")

    frame = pd.read_csv(path)
    missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise ManualMinutesError(
            f"{path}: missing required column(s): {', '.join(missing)}"
        )
    if not any(column in frame.columns for column in IDENTITY_COLUMNS):
        raise ManualMinutesError(
            f"{path}: must contain one of: {', '.join(IDENTITY_COLUMNS)}"
        )

    states: list[PlayerMinutesState] = []
    errors: list[str] = []
    for index, row in frame.iterrows():
        csv_line = int(index) + 2  # 1-based, plus the header row
        try:
            states.append(
                PlayerMinutesState(
                    gameweek=_int_or_none(row.get("GW")),
                    player_id=_int_or_none(row.get("player_id")),
                    player_key=_str_or_none(row.get("player_key")),
                    player=_str_or_none(row.get("player")),
                    team=_str_or_none(row.get("team")),
                    position=_str_or_none(row.get("Pos")),
                    likely_minutes=_float_or_default(row.get("mins"), 0.0),
                    start_probability=_float_or_default(row.get("start"), 0.0),
                    chance_of_playing=_float_or_none(row.get("chance_of_playing")),
                )
            )
        except (TypeError, ValueError) as exc:
            errors.append(f"line {csv_line}: {exc}")

    if errors:
        preview = "; ".join(errors[:5])
        more = f" (+{len(errors) - 5} more)" if len(errors) > 5 else ""
        raise ManualMinutesError(
            f"{path}: {len(errors)} invalid row(s): {preview}{more}"
        )
    return ManualMinutesFile(path=path, states=tuple(states))


def resolve_manual_minutes_paths(
    config: AppConfig,
    explicit_paths: Sequence[Path | str] | None = None,
) -> list[Path]:
    """Decide which manual minutes CSVs feed this run.

    - ``explicit_paths`` given (e.g. this week's Admin Panel export): those
      paths win outright and must all exist. An empty list means "no manual
      minutes this run". Explicit intent also wins over the config flag.
    - ``explicit_paths is None``: legacy behaviour -- the config path plus
      :data:`LEGACY_EXTRA_MANUAL_MINUTES_PATH`, each applied only if present,
      deduplicated by resolved path, in that order. When
      ``config.use_player_minutes_input_file`` is False (the CLI's
      ``--no-minutes-inputs``), the defaults are skipped entirely: no
      discovery, no reads, empty states to the engine.
    """
    if explicit_paths is not None:
        candidates = [Path(p) for p in explicit_paths]
        missing = [str(p) for p in candidates if not p.exists()]
        if missing:
            raise ManualMinutesError(
                f"manual minutes file(s) not found: {', '.join(missing)}"
            )
    elif not config.use_player_minutes_input_file:
        return []
    else:
        candidates = [config.player_minutes_input_path, LEGACY_EXTRA_MANUAL_MINUTES_PATH]

    resolved: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        if not path.exists():
            continue
        key = path.resolve()
        if key in seen:
            continue
        seen.add(key)
        resolved.append(path)
    return resolved


def load_manual_minutes_files(paths: Sequence[Path | str]) -> list[ManualMinutesFile]:
    """Validate every path against the contract; all-or-nothing."""
    return [load_manual_minutes_csv(path) for path in paths]


# --------------------------------------------------------------------------
# Minute overrides (layer 5 inputs)
# --------------------------------------------------------------------------

#: Filenames that ``minute_overrides.find_minutes_override_file`` used to
#: auto-discover from the working directory. Preserved as the *default*
#: resolution at the seam; the engine itself never scans directories.
#: Anchored to the backend root instead of the CWD.
LEGACY_MINUTE_OVERRIDE_FILENAMES = (
    BACKEND_ROOT / "minute_overrides.csv",
    BACKEND_ROOT / "minutes_overrides.csv",
    BACKEND_ROOT / "xmins_overrides.csv",
)

OVERRIDE_REQUIRED_COLUMNS = ("GW", "mins")


class MinuteOverrideState(BaseModel):
    """One hard minute override: pins ``expected_minutes`` for one
    player-fixture (gameweek + fixture-in-week). Touches nothing else."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    gameweek: int
    fixture_in_week: int = 1
    player_id: int | None = None
    player_key: str | None = None
    minutes: float

    @field_validator("fixture_in_week")
    @classmethod
    def _fixture_in_week_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError(f"fixture_in_week must be >= 1, got {value}")
        return value

    @field_validator("minutes")
    @classmethod
    def _minutes_in_range(cls, value: float) -> float:
        if not 0.0 <= value <= 90.0:
            raise ValueError(f"mins must be within 0..90, got {value}")
        return float(value)

    @model_validator(mode="after")
    def _identity_present(self) -> "MinuteOverrideState":
        if self.player_id is None and not self.player_key:
            raise ValueError("row needs player_id or player_key")
        return self


class MinuteOverridesFile(BaseModel):
    """A validated minute-overrides CSV."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    path: Path
    states: tuple[MinuteOverrideState, ...]


def load_minute_overrides_csv(path: Path | str) -> MinuteOverridesFile:
    """Parse and validate a minute-overrides CSV against the contract.

    Divergence from the legacy loader, by design: a blank/invalid ``GW`` made
    the legacy row silently inert; the contract rejects it with a line number.
    """
    path = Path(path)
    if not path.exists():
        raise ManualMinutesError(f"minute overrides file not found: {path}")

    frame = pd.read_csv(path)
    missing = [column for column in OVERRIDE_REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise ManualMinutesError(
            f"{path}: missing required column(s): {', '.join(missing)}"
        )
    if not any(column in frame.columns for column in IDENTITY_COLUMNS):
        raise ManualMinutesError(
            f"{path}: must contain one of: {', '.join(IDENTITY_COLUMNS)}"
        )

    states: list[MinuteOverrideState] = []
    errors: list[str] = []
    for index, row in frame.iterrows():
        csv_line = int(index) + 2
        try:
            gameweek = _int_or_none(row.get("GW"))
            if gameweek is None:
                raise ValueError("GW is required for an override row")
            fixture_in_week = _int_or_none(row.get("fixture_in_week"))
            states.append(
                MinuteOverrideState(
                    gameweek=gameweek,
                    fixture_in_week=1 if fixture_in_week is None else fixture_in_week,
                    player_id=_int_or_none(row.get("player_id")),
                    player_key=_str_or_none(row.get("player_key")),
                    minutes=_float_or_default(row.get("mins"), 0.0),
                )
            )
        except (TypeError, ValueError) as exc:
            errors.append(f"line {csv_line}: {exc}")

    if errors:
        preview = "; ".join(errors[:5])
        more = f" (+{len(errors) - 5} more)" if len(errors) > 5 else ""
        raise ManualMinutesError(
            f"{path}: {len(errors)} invalid row(s): {preview}{more}"
        )
    return MinuteOverridesFile(path=path, states=tuple(states))


def resolve_minute_override_paths(
    explicit_paths: Sequence[Path | str] | None = None,
) -> list[Path]:
    """Decide which minute-override CSVs feed this run.

    - ``explicit_paths`` given: those paths win outright and must all exist.
      An empty list means "no overrides this run".
    - ``explicit_paths is None``: legacy behaviour, preserved exactly -- the
      first existing filename from :data:`LEGACY_MINUTE_OVERRIDE_FILENAMES`
      (the old CWD auto-discovery, now explicit at the seam).
    """
    if explicit_paths is not None:
        candidates = [Path(p) for p in explicit_paths]
        missing = [str(p) for p in candidates if not p.exists()]
        if missing:
            raise ManualMinutesError(
                f"minute overrides file(s) not found: {', '.join(missing)}"
            )
        return candidates
    for candidate in LEGACY_MINUTE_OVERRIDE_FILENAMES:
        if candidate.exists():
            return [candidate]
    return []


def load_minute_override_files(paths: Sequence[Path | str]) -> list[MinuteOverridesFile]:
    """Validate every path against the contract; all-or-nothing."""
    return [load_minute_overrides_csv(path) for path in paths]


# --------------------------------------------------------------------------
# Run-input resolution (the boundary between files/JSON and the pure engine)
# --------------------------------------------------------------------------


class MinutesRunInputs(BaseModel):
    """Resolved minutes inputs for one run: what the engine consumes.

    ``manual_inputs`` is a tuple of layers (later layers win); ``overrides``
    is a flat tuple. Pydantic validates/coerces every element, so raw dicts
    (e.g. an Admin Panel JSON payload) parse into contract states for free.
    """

    model_config = ConfigDict(frozen=True)

    manual_inputs: tuple[tuple[PlayerMinutesState, ...], ...] = ()
    overrides: tuple[MinuteOverrideState, ...] = ()


def _as_manual_layers(states: Sequence) -> tuple[tuple, ...]:
    """Accept a flat sequence of states (one layer) or a sequence of layers."""
    items = list(states)
    if not items:
        return ()
    if all(isinstance(item, (list, tuple)) for item in items):
        return tuple(tuple(layer) for layer in items)
    return (tuple(items),)


def resolve_minutes_run_inputs(
    config: AppConfig,
    *,
    manual_paths: Sequence[Path | str] | None = None,
    override_paths: Sequence[Path | str] | None = None,
    manual_states: Sequence | None = None,
    override_states: Sequence | None = None,
) -> MinutesRunInputs:
    """Resolve all minutes inputs for a run at the boundary.

    Two routes per input, mutually exclusive:

    - **in-memory** (``*_states``, the Admin Panel route): validated state
      objects -- or raw dicts, coerced by the contract -- are used directly.
      The CSV adapters and any path discovery are bypassed entirely.
    - **files** (``*_paths`` or ``None`` for the legacy defaults, the
      CLI/cron route): paths resolve and validate exactly as before.

    All file-system knowledge (config defaults, legacy auto-discovery)
    terminates here; nothing downstream of this function touches a path.
    """
    if manual_states is not None and manual_paths is not None:
        raise ManualMinutesError(
            "pass either manual_states or manual_paths, not both"
        )
    if override_states is not None and override_paths is not None:
        raise ManualMinutesError(
            "pass either override_states or override_paths, not both"
        )

    if manual_states is not None:
        manual_layers = _as_manual_layers(manual_states)
    else:
        manual_files = load_manual_minutes_files(
            resolve_manual_minutes_paths(config, manual_paths)
        )
        manual_layers = tuple(manual.states for manual in manual_files)

    if override_states is not None:
        overrides = tuple(override_states)
    else:
        override_files = load_minute_override_files(
            resolve_minute_override_paths(override_paths)
        )
        overrides = tuple(state for f in override_files for state in f.states)

    return MinutesRunInputs(manual_inputs=manual_layers, overrides=overrides)
