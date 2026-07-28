from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fpl_xpts.solio_compare import (  # noqa: E402
    SOLIO_URL,
    _name_score,
    _position,
    fetch_solio_latest,
    solio_top_projected_frame,
)


ACTUALS_PATH = ROOT / "data" / "vaastav" / "2024-25_merged_gw.csv"
OUT_DIR = ROOT / "outputs" / "external_comparison"

KFT_CANDIDATES = [
    ROOT / "outputs" / "live_smoke" / "weekly.csv",
    ROOT / "outputs" / "minutes_template_run" / "weekly.csv",
    ROOT / "outputs" / "legacy_live_smoke" / "weekly_player_week.csv",
    ROOT / "outputs" / "legacy_live" / "weekly_player_week.csv",
]

POSITION_ORDER = ["GK", "DEF", "MID", "FWD"]


def _norm(text: object) -> str:
    import re
    import unicodedata

    value = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii")
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9 ]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _canonical_team(text: object) -> str:
    aliases = {
        "man city": "manchester city",
        "manchester city": "manchester city",
        "man utd": "manchester united",
        "man united": "manchester united",
        "manchester united": "manchester united",
        "newcastle": "newcastle united",
        "newcastle united": "newcastle united",
        "nott m forest": "nottingham forest",
        "nottingham forest": "nottingham forest",
        "spurs": "tottenham",
        "tottenham": "tottenham",
        "wolves": "wolverhampton wanderers",
        "wolverhampton wanderers": "wolverhampton wanderers",
        "brighton": "brighton",
        "brighton hove albion": "brighton",
    }
    key = _norm(text)
    return aliases.get(key, key)


def _numeric(series: pd.Series, default: float = np.nan) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").fillna(default)


def _spearman(frame: pd.DataFrame, pred_col: str, actual_col: str = "actual_points") -> float:
    values = frame[[pred_col, actual_col]].dropna()
    if len(values) < 2:
        return math.nan
    return float(values[pred_col].rank().corr(values[actual_col].rank(), method="pearson"))


def _format_gws(gws: list[int]) -> str:
    return ",".join(str(gw) for gw in sorted(set(gws)))


def load_actuals(path: Path = ACTUALS_PATH) -> tuple[pd.DataFrame, str]:
    if not path.exists():
        raise FileNotFoundError(f"Actuals file not found: {path}")
    raw = pd.read_csv(path)
    required = {"GW", "element", "name", "position", "team", "total_points"}
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"{path} missing required columns: {sorted(missing)}")

    xp_col = next((col for col in ["xP", "ep_next", "expected_points"] if col in raw.columns), None)
    if xp_col is None:
        raise ValueError(f"{path} has no official FPL expected-points column.")

    raw["GW"] = _numeric(raw["GW"]).astype("Int64")
    raw["element"] = _numeric(raw["element"]).astype("Int64")
    raw["total_points"] = _numeric(raw["total_points"], default=0.0)
    raw[xp_col] = _numeric(raw[xp_col])

    grouped = (
        raw.dropna(subset=["GW", "element"])
        .groupby(["GW", "element"], as_index=False)
        .agg(
            actual_name=("name", "first"),
            actual_position=("position", "first"),
            actual_team=("team", "first"),
            actual_points=("total_points", "sum"),
            fpl_official_xp=(xp_col, "max"),
        )
    )
    grouped["GW"] = grouped["GW"].astype(int)
    grouped["element"] = grouped["element"].astype(int)
    grouped["actual_position"] = grouped["actual_position"].map(_position)
    grouped["actual_team_key"] = grouped["actual_team"].apply(_canonical_team)
    return grouped, xp_col


def _full_player_name(row: pd.Series) -> str:
    name = f"{row.get('first_name', '')} {row.get('second_name', '')}".strip()
    return name or str(row.get("web_name", ""))


def _enrich_kft_identity(frame: pd.DataFrame, source_path: Path) -> pd.DataFrame:
    out = frame.copy()
    out["kft_player"] = out.get("web_name", out.get("player", "")).astype(str)
    out["kft_position"] = out.get("position", out.get("Pos", "")).map(_position)
    out["kft_team"] = out.get("team", "")

    players_path = source_path.parent / "players.csv"
    teams_path = source_path.parent / "teams.csv"
    if players_path.exists():
        players = pd.read_csv(players_path)
        if {"id", "web_name"}.issubset(players.columns):
            players = players.copy()
            players["element"] = _numeric(players["id"]).astype("Int64")
            players["kft_player_full"] = players.apply(_full_player_name, axis=1)
            players["kft_player_web"] = players["web_name"].astype(str)
            player_cols = ["element", "kft_player_full", "kft_player_web"]
            if "team" in players.columns:
                player_cols.append("team")
            out = out.merge(players[player_cols], on="element", how="left", suffixes=("", "_player_meta"))
            out["kft_player"] = out["kft_player_full"].fillna(out["kft_player_web"]).fillna(out["kft_player"])
            if "team_player_meta" in out.columns:
                out["kft_team"] = out["team_player_meta"].fillna(out["kft_team"])

    if teams_path.exists():
        teams = pd.read_csv(teams_path)
        if {"id", "name"}.issubset(teams.columns):
            team_map = dict(zip(pd.to_numeric(teams["id"], errors="coerce"), teams["name"]))
            team_numeric = pd.to_numeric(out["kft_team"], errors="coerce")
            out["kft_team"] = team_numeric.map(team_map).fillna(out["kft_team"])

    out["kft_team_key"] = out["kft_team"].apply(_canonical_team)
    return out


def load_kft_projection() -> tuple[pd.DataFrame, dict[str, Any]]:
    candidates = []
    for path in KFT_CANDIDATES:
        if not path.exists():
            continue
        df = pd.read_csv(path)
        gw_col = "event" if "event" in df.columns else "GW" if "GW" in df.columns else None
        id_col = "player_id" if "player_id" in df.columns else "element" if "element" in df.columns else None
        if gw_col is None or id_col is None or "xPts" not in df.columns:
            candidates.append(
                {
                    "path": path,
                    "usable": False,
                    "rows": len(df),
                    "gws": 0,
                    "reason": "missing GW, player ID, or xPts",
                }
            )
            continue
        gws = pd.to_numeric(df[gw_col], errors="coerce").dropna().astype(int).nunique()
        candidates.append(
            {
                "path": path,
                "usable": True,
                "rows": len(df),
                "gws": int(gws),
                "gw_col": gw_col,
                "id_col": id_col,
            }
        )

    usable = [candidate for candidate in candidates if candidate["usable"]]
    if not usable:
        raise FileNotFoundError("No usable KFT weekly projection file with GW, player ID, and xPts was found.")

    chosen = sorted(usable, key=lambda item: (item["gws"], item["rows"]), reverse=True)[0]
    raw = pd.read_csv(chosen["path"])
    out = pd.DataFrame(
        {
            "GW": _numeric(raw[chosen["gw_col"]]).astype("Int64"),
            "element": _numeric(raw[chosen["id_col"]]).astype("Int64"),
            "kft_xpts": _numeric(raw["xPts"]),
        }
    )
    for source_col, target_col in [
        ("web_name", "web_name"),
        ("player", "player"),
        ("position", "position"),
        ("Pos", "Pos"),
        ("team", "team"),
    ]:
        if source_col in raw.columns:
            out[target_col] = raw[source_col]

    out = out.dropna(subset=["GW", "element"])
    out["GW"] = out["GW"].astype(int)
    out["element"] = out["element"].astype(int)
    out = _enrich_kft_identity(out, chosen["path"])
    out = (
        out.groupby(["GW", "element"], as_index=False)
        .agg(
            kft_xpts=("kft_xpts", "sum"),
            kft_player=("kft_player", "first"),
            kft_position=("kft_position", "first"),
            kft_team=("kft_team", "first"),
            kft_team_key=("kft_team_key", "first"),
        )
        .sort_values(["GW", "kft_xpts"], ascending=[True, False])
        .reset_index(drop=True)
    )
    chosen["all_candidates"] = candidates
    return out, chosen


def identity_sanity(joined: pd.DataFrame) -> dict[str, float]:
    if joined.empty:
        return {"name_match_rate": math.nan, "team_match_rate": math.nan}
    name_scores = joined.apply(lambda row: _name_score(row.get("kft_player"), row.get("actual_name")), axis=1)
    team_matches = joined["kft_team_key"].eq(joined["actual_team_key"])
    return {
        "name_match_rate": float((name_scores >= 0.5).mean()),
        "team_match_rate": float(team_matches.mean()),
    }


def _top50_spearman_by_gw(frame: pd.DataFrame, pred_col: str) -> float:
    values = []
    for _, gw_frame in frame.groupby("GW"):
        if len(gw_frame) < 50:
            continue
        top = gw_frame.nlargest(50, "actual_points")
        values.append(_spearman(top, pred_col))
    if not values:
        return math.nan
    finite_values = [value for value in values if not pd.isna(value)]
    if not finite_values:
        return math.nan
    return float(np.mean(finite_values))


def metrics_row(
    model: str,
    frame: pd.DataFrame,
    pred_col: str,
    source: str,
    comparison_scope: str,
    unmatched_predictions: int = 0,
    fair_same_gws: bool = True,
    fair_same_player_universe: bool = True,
    notes: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    usable = frame.dropna(subset=[pred_col, "actual_points"]).copy()
    row: dict[str, Any] = {
        "model": model,
        "source": source,
        "comparison_scope": comparison_scope,
        "gws": _format_gws(usable["GW"].astype(int).unique().tolist()) if not usable.empty else "",
        "rows": int(len(usable)),
        "unique_players": int(usable["element"].nunique()) if "element" in usable else 0,
        "unmatched_predictions": int(unmatched_predictions),
        "mae": math.nan,
        "rmse": math.nan,
        "spearman": math.nan,
        "top50_actual_spearman": math.nan,
        "fair_same_gws": bool(fair_same_gws),
        "fair_same_player_universe": bool(fair_same_player_universe),
        "notes": notes,
    }
    for pos in POSITION_ORDER:
        row[f"mean_error_{pos}"] = math.nan
    if usable.empty:
        if extra:
            row.update(extra)
        return row

    err = usable[pred_col].astype(float) - usable["actual_points"].astype(float)
    row["mae"] = float(err.abs().mean())
    row["rmse"] = float(np.sqrt(np.mean(err.to_numpy() ** 2)))
    row["spearman"] = _spearman(usable, pred_col)
    row["top50_actual_spearman"] = _top50_spearman_by_gw(usable, pred_col)
    pos_col = "actual_position" if "actual_position" in usable.columns else "position"
    for pos, group in usable.groupby(pos_col):
        normalized = _position(pos)
        if normalized in POSITION_ORDER:
            row[f"mean_error_{normalized}"] = float((group[pred_col] - group["actual_points"]).mean())
    if extra:
        row.update(extra)
    return row


def weekly_rows(
    model: str,
    frame: pd.DataFrame,
    pred_col: str,
    source: str,
    comparison_scope: str,
    fair_same_player_universe: bool,
    notes: str = "",
) -> list[dict[str, Any]]:
    rows = []
    for gw, gw_frame in frame.groupby("GW"):
        rows.append(
            metrics_row(
                model=model,
                frame=gw_frame,
                pred_col=pred_col,
                source=source,
                comparison_scope=comparison_scope,
                fair_same_gws=True,
                fair_same_player_universe=fair_same_player_universe,
                notes=notes,
            )
            | {"GW": int(gw)}
        )
    return rows


def match_solio_to_actuals(
    solio: pd.DataFrame,
    actuals: pd.DataFrame,
    kft: pd.DataFrame,
    gw: int,
) -> tuple[pd.DataFrame, dict[str, int]]:
    actual_gw = actuals.loc[actuals["GW"] == gw].copy()
    rows = []
    unmatched = 0
    for _, solio_row in solio.iterrows():
        candidates = actual_gw.loc[
            (actual_gw["actual_team_key"] == _canonical_team(solio_row.get("solio_team")))
            & (actual_gw["actual_position"].map(_position) == _position(solio_row.get("solio_position")))
        ].copy()
        if candidates.empty:
            unmatched += 1
            continue
        candidates["name_score"] = candidates["actual_name"].apply(
            lambda name: _name_score(solio_row.get("solio_name"), name)
        )
        best = candidates.sort_values(["name_score", "actual_points"], ascending=[False, False]).iloc[0]
        if float(best["name_score"]) < 0.5:
            unmatched += 1
            continue
        combined = {**best.to_dict(), **solio_row.to_dict()}
        combined["GW"] = gw
        combined["solio_xpts"] = pd.to_numeric(solio_row.get("solio_prPoints"), errors="coerce")
        combined["solio_name_score"] = float(best["name_score"])
        rows.append(combined)

    matched = pd.DataFrame(rows)
    if not matched.empty:
        kft_keys = kft.loc[kft["GW"] == gw, ["GW", "element"]].drop_duplicates()
        matched = matched.merge(kft_keys.assign(kft_row_available=True), on=["GW", "element"], how="left")
        matched["kft_row_available"] = matched["kft_row_available"].fillna(False)
        kft_matched = int(matched["kft_row_available"].sum())
    else:
        matched["kft_row_available"] = pd.Series(dtype=bool)
        kft_matched = 0
    counts = {
        "solio_rows": int(len(solio)),
        "solio_matched_actual": int(len(matched)),
        "solio_unmatched_actual": int(unmatched),
        "solio_matched_kft": kft_matched,
        "solio_unmatched_kft": int(len(matched) - kft_matched),
    }
    return matched, counts


def fetch_solio_data() -> dict[str, Any]:
    powershell_path = shutil.which("powershell.exe") or shutil.which("powershell")
    if powershell_path:
        result = subprocess.run(
            [
                powershell_path,
                "-NoProfile",
                "-Command",
                f"(Invoke-WebRequest -UseBasicParsing '{SOLIO_URL}' -TimeoutSec 30).Content",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=40,
        )
        return json.loads(result.stdout)

    curl_path = shutil.which("curl.exe") or shutil.which("curl")
    if curl_path:
        result = subprocess.run(
            [
                curl_path,
                "-L",
                "--silent",
                "--show-error",
                "--max-time",
                "30",
                SOLIO_URL,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=40,
        )
        return json.loads(result.stdout)
    return fetch_solio_latest()


def build_report(fetch_solio: bool = True) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    actuals, official_col = load_actuals()
    kft, kft_meta = load_kft_projection()
    joined = kft.merge(actuals, on=["GW", "element"], how="inner")
    identity = identity_sanity(joined)
    kft_unmatched = len(kft) - len(joined)

    actual_gws = sorted(actuals["GW"].unique().tolist())
    kft_gws = sorted(kft["GW"].unique().tolist())
    compared_gws = sorted(joined["GW"].unique().tolist())
    missing_kft_gws = sorted(set(actual_gws) - set(kft_gws))

    likely_same_universe = (
        identity["name_match_rate"] >= 0.85
        and identity["team_match_rate"] >= 0.85
        and kft_unmatched <= max(20, int(0.05 * len(kft)))
    )
    identity_note = (
        ""
        if likely_same_universe
        else "Identity sanity check failed or is weak; KFT output may be from a different FPL season/player universe."
    )

    source = str(kft_meta["path"].relative_to(ROOT))
    comparison_rows = [
        metrics_row(
            model="KFT",
            frame=joined,
            pred_col="kft_xpts",
            source=source,
            comparison_scope="KFT-covered player/GW rows matched by FPL element ID",
            unmatched_predictions=kft_unmatched,
            fair_same_gws=True,
            fair_same_player_universe=likely_same_universe,
            notes=identity_note,
            extra=identity,
        ),
        metrics_row(
            model="FPL official xP",
            frame=joined,
            pred_col="fpl_official_xp",
            source=f"{ACTUALS_PATH.relative_to(ROOT)}:{official_col}",
            comparison_scope="Same player/GW rows as KFT",
            unmatched_predictions=int(joined["fpl_official_xp"].isna().sum()),
            fair_same_gws=True,
            fair_same_player_universe=likely_same_universe,
            notes=identity_note,
            extra=identity,
        ),
    ]

    weekly = []
    weekly.extend(
        weekly_rows(
            model="KFT",
            frame=joined,
            pred_col="kft_xpts",
            source=source,
            comparison_scope="KFT-covered player/GW rows matched by FPL element ID",
            fair_same_player_universe=likely_same_universe,
            notes=identity_note,
        )
    )
    weekly.extend(
        weekly_rows(
            model="FPL official xP",
            frame=joined,
            pred_col="fpl_official_xp",
            source=f"{ACTUALS_PATH.relative_to(ROOT)}:{official_col}",
            comparison_scope="Same player/GW rows as KFT",
            fair_same_player_universe=likely_same_universe,
            notes=identity_note,
        )
    )

    status_lines = [
        f"KFT source chosen: {source}",
        f"KFT projected GWs: {_format_gws(kft_gws)}",
        f"KFT missing projection GWs from 2024-25 actuals: {_format_gws(missing_kft_gws)}",
        f"KFT matched to actuals by element ID: {len(joined)} / {len(kft)} rows; unmatched {kft_unmatched}",
        f"KFT identity sanity: name match {identity['name_match_rate']:.1%}, team match {identity['team_match_rate']:.1%}",
        f"FPL official xP usable on KFT universe: {joined['fpl_official_xp'].notna().sum()} / {len(joined)} rows",
    ]

    if fetch_solio:
        solio_error = ""
        solio_counts = {
            "solio_rows": 0,
            "solio_matched_actual": 0,
            "solio_unmatched_actual": 0,
            "solio_matched_kft": 0,
            "solio_unmatched_kft": 0,
        }
        solio_matched = pd.DataFrame()
        solio_gw: int | None = None
        try:
            solio_data = fetch_solio_data()
            solio = solio_top_projected_frame(solio_data)
            solio_generated_at = solio_data.get("generatedAt")
            solio_archive_note = (
                f"Solio generatedAt={solio_generated_at}; latest feed is not a verified 2024-25 projection archive. "
                if solio_generated_at
                else "Solio generatedAt missing; latest feed season is not verified. "
            )
            if "solio_prPoints" in solio.columns:
                solio["solio_prPoints"] = pd.to_numeric(solio["solio_prPoints"], errors="coerce")
            solio_gw = int(solio_data.get("gameweek"))
            if solio_gw in compared_gws:
                solio_matched, solio_counts = match_solio_to_actuals(solio, actuals, kft, solio_gw)
                solio_fair_same_gw = True
                solio_notes = (
                    solio_archive_note
                    + "Solio topProjected only; same numeric GW as KFT but not same full player universe."
                )
            else:
                solio_counts["solio_rows"] = int(len(solio))
                solio_fair_same_gw = False
                solio_notes = (
                    solio_archive_note
                    +
                    f"Solio latest GW {solio_gw} is not in KFT/actual comparison GWs "
                    f"({_format_gws(compared_gws)}); metrics not computed."
                )
        except Exception as exc:  # noqa: BLE001
            solio_error = str(exc)
            solio_fair_same_gw = False
            solio_notes = f"Solio fetch failed; metrics not computed: {solio_error}"

        if solio_matched.empty:
            comparison_rows.append(
                metrics_row(
                    model="Solio",
                    frame=pd.DataFrame(columns=["GW", "element", "actual_points", "solio_xpts"]),
                    pred_col="solio_xpts",
                    source="https://fpl.solioanalytics.com/api/data/latest.json",
                    comparison_scope="Solio topProjected matched by name+team+position",
                    unmatched_predictions=solio_counts["solio_unmatched_actual"],
                    fair_same_gws=solio_fair_same_gw,
                    fair_same_player_universe=False,
                    notes=solio_notes,
                    extra=solio_counts | {
                        "solio_gameweek": solio_gw if solio_gw is not None else math.nan,
                        "solio_generatedAt": locals().get("solio_generated_at", ""),
                    },
                )
            )
        else:
            comparison_rows.append(
                metrics_row(
                    model="Solio",
                    frame=solio_matched,
                    pred_col="solio_xpts",
                    source="https://fpl.solioanalytics.com/api/data/latest.json",
                    comparison_scope="Solio topProjected matched by name+team+position",
                    unmatched_predictions=solio_counts["solio_unmatched_actual"],
                    fair_same_gws=solio_fair_same_gw,
                    fair_same_player_universe=False,
                    notes=solio_notes,
                    extra=solio_counts | {"solio_gameweek": solio_gw, "solio_generatedAt": solio_generated_at},
                )
            )
            weekly.extend(
                weekly_rows(
                    model="Solio",
                    frame=solio_matched,
                    pred_col="solio_xpts",
                    source="https://fpl.solioanalytics.com/api/data/latest.json",
                    comparison_scope="Solio topProjected matched by name+team+position",
                    fair_same_player_universe=False,
                    notes=solio_notes,
                )
            )

        if solio_error:
            status_lines.append(f"Solio fetch/match: failed - {solio_error}")
        else:
            status_lines.append(
                "Solio fetch/match: "
                f"GW {solio_gw}, rows {solio_counts['solio_rows']}, "
                f"matched actual {solio_counts['solio_matched_actual']}, "
                f"unmatched actual {solio_counts['solio_unmatched_actual']}, "
                f"matched KFT {solio_counts['solio_matched_kft']}"
            )

    fair_message = (
        "Fairness: KFT and FPL official xP are compared on the same rows. "
        + (
            "The identity sanity check says this is a reasonable same-season player universe."
            if likely_same_universe
            else "The identity sanity check says this is probably not a fair same-season comparison."
        )
    )
    status_lines.append(fair_message)
    status_lines.append("FPL Review was not scraped or estimated.")

    comparison = pd.DataFrame(comparison_rows)
    weekly_df = pd.DataFrame(weekly)
    if not weekly_df.empty:
        first_cols = ["GW", "model"]
        remaining = [col for col in weekly_df.columns if col not in first_cols]
        weekly_df = weekly_df[first_cols + remaining].sort_values(["GW", "model"]).reset_index(drop=True)
    return comparison, weekly_df, status_lines


def write_outputs(comparison: pd.DataFrame, weekly: pd.DataFrame, out_dir: Path = OUT_DIR) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    comparison_path = out_dir / "model_comparison.csv"
    weekly_path = out_dir / "weekly_accuracy.csv"
    comparison.to_csv(comparison_path, index=False, float_format="%.6f")
    weekly.to_csv(weekly_path, index=False, float_format="%.6f")
    return comparison_path, weekly_path


def print_report(comparison: pd.DataFrame, weekly: pd.DataFrame, status_lines: list[str], paths: tuple[Path, Path]) -> None:
    display_cols = [
        "model",
        "rows",
        "gws",
        "mae",
        "rmse",
        "spearman",
        "top50_actual_spearman",
        "fair_same_player_universe",
    ]
    print("\nModel comparison")
    print(comparison[display_cols].to_string(index=False))
    print("\nWeekly accuracy rows:", len(weekly))
    print("\nCoverage and fairness")
    for line in status_lines:
        print("-", line)
    print("\nWrote:")
    print(f"- {paths[0]}")
    print(f"- {paths[1]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare KFT, FPL official xP, and Solio against 2024-25 actual points.")
    parser.add_argument("--no-solio", action="store_true", help="Skip fetching Solio latest.json")
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR, help="Output directory for comparison CSVs")
    args = parser.parse_args()

    comparison, weekly, status_lines = build_report(fetch_solio=not args.no_solio)
    paths = write_outputs(comparison, weekly, args.out_dir)
    print_report(comparison, weekly, status_lines, paths)


if __name__ == "__main__":
    main()
