from __future__ import annotations

import math
import re
from pathlib import Path

import numpy as np
import pandas as pd

from .config import AppConfig
from .features import build_player_rates
from .monte_carlo import simulate_player_week
from .pipeline import run_live_projection


FIXTURE_COLS = [
    "GW", "week", "fixture_in_week", "player_key", "player", "team", "Pos", "mins",
    "team_xG", "team_xA", "xG_scaled", "xA_scaled", "xGA_exp", "cs_prob", "xPts", "P1_GA",
    "AppPts", "GoalPts", "AssistPts", "CSPts", "SavePts", "DefconPts", "CardPts", "PenMissPts",
    "ConcedePts",
]

WEEKLY_COLS = [
    "GW", "week", "player_key", "player", "team", "Pos", "mins", "xG_scaled", "xA_scaled",
    "xGA_exp", "cs_prob", "xPts", "P1_GA", "AppPts", "GoalPts", "AssistPts", "CSPts",
    "SavePts", "DefconPts", "CardPts", "PenMissPts", "ConcedePts", "fixtures_in_week",
    "expected_minutes", "pred_play_prob", "pred_start_prob", "minutes_model_source", "ml_xpts",
    "P_return", "P_haul", "opponent", "was_home", "now_cost", "selected_by_percent", "status",
    "chance_of_playing_this_round", "news", "prior_based", "prior_source",
]

TOTAL_COLS = [
    "player_key", "player", "team", "Pos", "xPts_6W", "mins_6W", "xG_6W", "xA_6W",
    "xGA_6W", "P1_GA_sum",
]

QC_WEEK_COLS = ["team", "week", "GW", "team_xG", "team_xA", "sum_xG", "sum_xA", "xG_diff", "xA_diff"]
QC_FIXTURE_COLS = ["team", "week", "GW", "fixture_in_week", "team_xG", "team_xA", "sum_xG", "sum_xA", "xG_diff", "xA_diff"]

FORM_AUDIT_COLS = ["player_key", "NPxG90_raw", "xA90_raw", "form_NPxG90", "form_xA90", "NPxG90", "xA90", "fpl_match_status"]
SHOT_PROFILE_COLS = [
    "player_key", "player", "team", "understat_season", "understat_minutes", "xG", "npxG", "xA",
    "understat_shots", "understat_key_passes", "understat_shots90", "understat_xG_per_shot",
    "understat_chances_created90", "understat_xA_per_chance", "understat_big_chance_threshold_xg",
    "understat_big_chance_received", "understat_big_chance_received90", "understat_big_chance_xG_share",
    "understat_big_chance_created", "understat_big_chance_created90", "understat_profile_source",
]

MC_FIXTURE_COLS = [
    "GW", "week", "player_key", "player", "team", "Pos", "mins", "xG_scaled", "xA_scaled",
    "xGA_exp", "cs_prob", "xPts", "P1_GA", "AppPts", "GoalPts", "AssistPts", "CSPts",
    "SavePts", "DefconPts", "CardPts", "PenMissPts", "ConcedePts",
    "fixtures_in_week", "fixture_in_week", "defcon90", "saves90", "rc_rate", "yc_rate",
    "yc_prob_90", "rc_prob_90", "gc_lambda", "skip_sim", "MC_MeanPts", "MC_StdPts", "MC_Floor",
    "MC_P25", "MC_P75", "MC_Upside", "MC_P1_Return", "MC_P2_Return", "P_return", "P_haul", "Bracket_LE_2",
    "Bracket_3_to_6", "Bracket_7_to_9", "Bracket_10_to_14", "Bracket_15_plus", "MC_MinPts", "MC_MaxPts",
]

MC_WEEKLY_COLS = [
    "GW", "week", "player_key", "player", "team", "Pos", "mins", "xG_scaled", "xA_scaled", "xPts",
    "fixtures_in_week", "MC_MeanPts", "MC_StdPts", "MC_Floor", "MC_P25", "MC_P75", "MC_Upside",
    "MC_CaptainMean", "MC_CaptainUpside", "MC_P1_Return", "MC_P2_Return", "P_return", "P_haul", "Bracket_LE_2",
    "Bracket_3_to_6", "Bracket_7_to_9", "Bracket_10_to_14", "Bracket_15_plus", "MC_MinPts", "MC_MaxPts",
    "expected_minutes", "pred_play_prob", "pred_start_prob", "minutes_model_source", "ml_xpts",
    "opponent", "was_home", "now_cost", "selected_by_percent", "status",
    "chance_of_playing_this_round", "news", "prior_based", "prior_source",
]


def _norm_key(text: object) -> str:
    s = str(text).strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def _canonical_team_name(name: object) -> str:
    aliases = {
        "Man City": "Manchester City",
        "Man Utd": "Manchester United",
        "Newcastle": "Newcastle United",
        "Nott'm Forest": "Nottingham Forest",
        "Spurs": "Tottenham",
        "Wolves": "Wolverhampton Wanderers",
    }
    text = str(name).strip()
    return aliases.get(text, text)


def _player_name(row: pd.Series) -> str:
    full = f"{row.get('first_name', '')} {row.get('second_name', '')}".strip()
    return full or str(row.get("web_name", ""))


def _with_legacy_identity(df: pd.DataFrame, players: pd.DataFrame, teams: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    player_meta = players.copy()
    player_meta["player"] = player_meta.apply(_player_name, axis=1)
    player_meta = player_meta[["id", "player"]].rename(columns={"id": "player_id"})
    team_meta = teams[["id", "name"]].rename(columns={"id": "team", "name": "team_name"})

    out = out.merge(player_meta, on="player_id", how="left")
    out = out.merge(team_meta, on="team", how="left")
    out["team"] = out["team_name"].apply(_canonical_team_name)
    out["player"] = out["player"].fillna(out.get("web_name", ""))
    out["player_key"] = out.apply(lambda r: f"{_norm_key(r['player'])}|{_norm_key(r['team'])}", axis=1)
    out["Pos"] = out["position"]
    out["GW"] = pd.to_numeric(out["event"], errors="coerce").astype("Int64")
    start_gw = int(out["GW"].dropna().min()) if out["GW"].notna().any() else 1
    out["week"] = (out["GW"].astype(int) - start_gw + 1).astype(int)
    return out.drop(columns=["team_name"], errors="ignore")


def _add_fixture_in_week(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    fixtures = (
        out[["team", "GW", "fixture", "kickoff_time"]]
        .drop_duplicates()
        .sort_values(["team", "GW", "kickoff_time", "fixture"])
    )
    fixtures["fixture_in_week"] = fixtures.groupby(["team", "GW"]).cumcount() + 1
    return out.drop(columns=["fixture_in_week"], errors="ignore").merge(
        fixtures[["team", "GW", "fixture", "fixture_in_week"]],
        on=["team", "GW", "fixture"],
        how="left",
    )


def _joined_fixture_values(values: pd.Series) -> object:
    cleaned = [value for value in values.tolist() if not pd.isna(value)]
    if not cleaned:
        return np.nan
    rendered = [str(value) for value in cleaned]
    return rendered[0] if len(rendered) == 1 else "|".join(rendered)


def _website_player_week_contract(
    raw_weekly: pd.DataFrame,
    player_fixture: pd.DataFrame,
    players: pd.DataFrame,
    teams: pd.DataFrame,
) -> pd.DataFrame:
    fixture = player_fixture.loc[player_fixture["event"].notna()].copy()
    fixture = _with_legacy_identity(fixture, players, teams)
    fixture = _add_fixture_in_week(fixture)
    opponent_names = teams.set_index("id")["name"].map(_canonical_team_name)
    fixture["opponent_name"] = fixture["opponent"].map(opponent_names)
    fixture["minutes_model_source"] = fixture.get(
        "minutes_model_source",
        pd.Series("rolling_heuristic_fallback", index=fixture.index),
    ).fillna("rolling_heuristic_fallback")
    for col in ["expected_minutes", "play_probability", "start_probability"]:
        if col not in fixture.columns:
            fixture[col] = np.nan
        fixture[col] = pd.to_numeric(fixture[col], errors="coerce")
    if "prior_based" not in fixture.columns:
        fixture["prior_based"] = False
    fixture["prior_based"] = fixture["prior_based"].fillna(False).astype(bool)
    if "prior_source" not in fixture.columns:
        fixture["prior_source"] = "observed_pl"
    fixture["prior_source"] = fixture["prior_source"].fillna("observed_pl").astype(str)

    def aggregate_context(group: pd.DataFrame) -> pd.Series:
        ordered = group.sort_values(["fixture_in_week", "kickoff_time", "fixture"], kind="mergesort")
        fixture_rows = ordered.drop_duplicates("fixture", keep="first")
        sources = fixture_rows["minutes_model_source"].dropna().astype(str).drop_duplicates().tolist()
        return pd.Series(
            {
                "expected_minutes": float(ordered["expected_minutes"].fillna(0.0).sum()),
                "pred_play_prob": float(fixture_rows["play_probability"].mean()),
                "pred_start_prob": float(fixture_rows["start_probability"].mean()),
                "minutes_model_source": "|".join(sources) if sources else np.nan,
                "opponent": _joined_fixture_values(fixture_rows["opponent_name"]),
                "was_home": _joined_fixture_values(fixture_rows["was_home"]),
                "prior_based": bool(fixture_rows["prior_based"].any()),
                "prior_source": _joined_fixture_values(fixture_rows["prior_source"].drop_duplicates()),
            }
        )

    contract = (
        fixture.groupby(["GW", "player_key", "player_id"], as_index=False)
        .apply(aggregate_context, include_groups=False)
        .reset_index(drop=True)
    )

    raw = raw_weekly.loc[raw_weekly["event"].notna()].copy()
    raw = _with_legacy_identity(raw, players, teams)
    for col in ["ml_xpts", "P_return", "P_haul"]:
        if col not in raw.columns:
            raw[col] = np.nan
    raw = raw[["GW", "player_key", "ml_xpts", "P_return", "P_haul"]].drop_duplicates(
        ["GW", "player_key"],
        keep="first",
    )
    contract = contract.merge(raw, on=["GW", "player_key"], how="left")

    player_cols = [
        "id", "now_cost", "selected_by_percent", "status", "chance_of_playing_this_round", "news",
    ]
    available_player_cols = [col for col in player_cols if col in players.columns]
    player_meta = players[available_player_cols].rename(columns={"id": "player_id"}).copy()
    contract = contract.merge(player_meta, on="player_id", how="left")
    for col in player_cols[1:]:
        if col not in contract.columns:
            contract[col] = np.nan
    return contract.drop(columns=["player_id"], errors="ignore")


def fixture_player_week(player_fixture: pd.DataFrame, players: pd.DataFrame, teams: pd.DataFrame) -> pd.DataFrame:
    out = player_fixture.loc[player_fixture["event"].notna()].copy()
    out = _with_legacy_identity(out, players, teams)
    out = _add_fixture_in_week(out)
    out = out.rename(
        columns={
            "expected_minutes": "mins",
            "team_xg": "team_xG",
            "team_xa": "team_xA",
            "xG": "xG_scaled",
            "xA": "xA_scaled",
        }
    )
    return out[FIXTURE_COLS].sort_values(["GW", "week", "team", "player_key", "fixture_in_week"]).reset_index(drop=True)


def weekly_player_week(fixture_df: pd.DataFrame, website_contract: pd.DataFrame | None = None) -> pd.DataFrame:
    def agg(g: pd.DataFrame) -> pd.Series:
        cs_prob = 1.0 - float(np.prod(1.0 - g["cs_prob"].clip(0, 1).to_numpy(float)))
        lam = float(g["xG_scaled"].sum() + g["xA_scaled"].sum())
        return pd.Series(
            {
                "player": g["player"].iloc[0],
                "team": g["team"].iloc[0],
                "Pos": g["Pos"].iloc[0],
                "mins": g["mins"].sum(),
                "xG_scaled": g["xG_scaled"].sum(),
                "xA_scaled": g["xA_scaled"].sum(),
                "xGA_exp": g["xGA_exp"].sum(),
                "cs_prob": cs_prob,
                "xPts": g["xPts"].sum(),
                "P1_GA": 1.0 - math.exp(-lam),
                "AppPts": g["AppPts"].sum(),
                "GoalPts": g["GoalPts"].sum(),
                "AssistPts": g["AssistPts"].sum(),
                "CSPts": g["CSPts"].sum(),
                "SavePts": g["SavePts"].sum(),
                "DefconPts": g["DefconPts"].sum(),
                "CardPts": g["CardPts"].sum(),
                "PenMissPts": g["PenMissPts"].sum(),
                "ConcedePts": g["ConcedePts"].sum(),
                "fixtures_in_week": g["fixture_in_week"].nunique(),
            }
        )

    out = (
        fixture_df.groupby(["GW", "week", "player_key"], as_index=False)
        .apply(agg, include_groups=False)
        .reset_index(drop=True)
    )
    if website_contract is not None and not website_contract.empty:
        out = out.merge(website_contract, on=["GW", "player_key"], how="left")
    for col in WEEKLY_COLS:
        if col not in out.columns:
            out[col] = np.nan
    out["expected_minutes"] = pd.to_numeric(out["expected_minutes"], errors="coerce").fillna(out["mins"])
    out["mins"] = out["expected_minutes"]
    return out[WEEKLY_COLS].sort_values(["GW", "xPts"], ascending=[True, False]).reset_index(drop=True)


def six_week_totals(weekly_df: pd.DataFrame) -> pd.DataFrame:
    out = (
        weekly_df.groupby(["player_key", "player", "team", "Pos"], as_index=False)
        .agg(
            xPts_6W=("xPts", "sum"),
            mins_6W=("mins", "sum"),
            xG_6W=("xG_scaled", "sum"),
            xA_6W=("xA_scaled", "sum"),
            xGA_6W=("xGA_exp", "sum"),
            P1_GA_sum=("P1_GA", "sum"),
        )
        .sort_values("xPts_6W", ascending=False)
    )
    return out[TOTAL_COLS].reset_index(drop=True)


def qc_tables(fixture_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    qc_fixture = (
        fixture_df.groupby(["team", "week", "GW", "fixture_in_week"], as_index=False)
        .agg(team_xG=("team_xG", "first"), team_xA=("team_xA", "first"), sum_xG=("xG_scaled", "sum"), sum_xA=("xA_scaled", "sum"))
    )
    qc_fixture["xG_diff"] = qc_fixture["sum_xG"] - qc_fixture["team_xG"]
    qc_fixture["xA_diff"] = qc_fixture["sum_xA"] - qc_fixture["team_xA"]

    qc_week = (
        qc_fixture.groupby(["team", "week", "GW"], as_index=False)
        .agg(team_xG=("team_xG", "sum"), team_xA=("team_xA", "sum"), sum_xG=("sum_xG", "sum"), sum_xA=("sum_xA", "sum"))
    )
    qc_week["xG_diff"] = qc_week["sum_xG"] - qc_week["team_xG"]
    qc_week["xA_diff"] = qc_week["sum_xA"] - qc_week["team_xA"]
    return qc_week[QC_WEEK_COLS], qc_fixture[QC_FIXTURE_COLS]


def form_weighting_audit(players: pd.DataFrame, teams: pd.DataFrame) -> pd.DataFrame:
    rates = build_player_rates(players)
    team_meta = teams[["id", "name"]].rename(columns={"id": "team", "name": "team_name"})
    rates = rates.merge(team_meta, on="team", how="left")
    rates["player"] = rates.apply(_player_name, axis=1)
    rates["team_name"] = rates["team_name"].apply(_canonical_team_name)
    rates["player_key"] = rates.apply(lambda r: f"{_norm_key(r['player'])}|{_norm_key(r['team_name'])}", axis=1)
    out = pd.DataFrame(
        {
            "player_key": rates["player_key"],
            "NPxG90_raw": rates["expected_goals_per_90"],
            "xA90_raw": rates["expected_assists_per_90"],
            "form_NPxG90": np.nan,
            "form_xA90": np.nan,
            "NPxG90": rates["expected_goals_per_90_shrunk"],
            "xA90": rates["expected_assists_per_90_shrunk"],
            "fpl_match_status": "live_fpl_current",
        }
    )
    return out[FORM_AUDIT_COLS]


def shot_profile_audit(shot_profiles: pd.DataFrame) -> pd.DataFrame:
    if shot_profiles is None or shot_profiles.empty:
        return pd.DataFrame(columns=SHOT_PROFILE_COLS)
    out = shot_profiles.copy()
    for col in SHOT_PROFILE_COLS:
        if col not in out.columns:
            out[col] = np.nan
    return out[SHOT_PROFILE_COLS].sort_values(["team", "player"]).reset_index(drop=True)


def _rate_columns(players: pd.DataFrame, teams: pd.DataFrame) -> pd.DataFrame:
    rates = build_player_rates(players)
    rates = rates.rename(
        columns={
            "id": "player_id",
            "defensive_contribution_per_90_shrunk": "defcon90",
            "saves_per_90_shrunk": "saves90",
            "rc_rate90": "rc_rate",
            "yc_rate90": "yc_rate",
        }
    )
    return rates[["player_id", "defcon90", "saves90", "rc_rate", "yc_rate"]]


def mc_legacy_tables(
    player_fixture: pd.DataFrame,
    fixture_df: pd.DataFrame,
    weekly_df: pd.DataFrame,
    players: pd.DataFrame,
    teams: pd.DataFrame,
    n_sim: int,
    seed: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    mc_week, mc_fixture_raw = simulate_player_week(player_fixture.loc[player_fixture["event"].notna()], n_sim, seed, return_fixture=True)
    mc_fixture = _with_legacy_identity(mc_fixture_raw, players, teams)
    mc_fixture = _add_fixture_in_week(mc_fixture)
    mc_fixture = mc_fixture.rename(
        columns={
            "expected_minutes": "mins",
            "xG": "xG_scaled",
            "xA": "xA_scaled",
        }
    )
    mc_fixture = mc_fixture.merge(weekly_df[["GW", "player_key", "fixtures_in_week"]], on=["GW", "player_key"], how="left")
    mc_fixture = mc_fixture.merge(_rate_columns(players, teams), on="player_id", how="left", suffixes=("", "_rate"))
    for col in ["defcon90", "saves90", "rc_rate", "yc_rate"]:
        rate_col = f"{col}_rate"
        if col not in mc_fixture.columns and rate_col in mc_fixture.columns:
            mc_fixture[col] = mc_fixture[rate_col]
        elif rate_col in mc_fixture.columns:
            mc_fixture[col] = mc_fixture[col].fillna(mc_fixture[rate_col])
    mc_fixture["yc_prob_90"] = mc_fixture["yc_rate"].fillna(0.0)
    mc_fixture["rc_prob_90"] = mc_fixture["rc_rate"].fillna(0.0)
    if "opponent_xg" in mc_fixture.columns:
        mc_fixture["gc_lambda"] = pd.to_numeric(mc_fixture["opponent_xg"], errors="coerce").fillna(0.0)
    else:
        mc_fixture["gc_lambda"] = -np.log(mc_fixture["cs_prob"].clip(1e-9, 1.0))
    mc_fixture["skip_sim"] = mc_fixture["mins"].fillna(0.0) <= 0

    mc_week = _with_legacy_identity(mc_week.rename(columns={"event": "event"}), players, teams)
    mc_week = mc_week.rename(columns={"position": "Pos"})
    mc_full = weekly_df.drop(columns=["P_return", "P_haul"], errors="ignore").merge(
        mc_week[
            [
                "GW", "player_key", "MC_MeanPts", "MC_StdPts", "MC_Floor", "MC_P25", "MC_P75", "MC_Upside",
                "MC_P1_Return", "MC_P2_Return", "P_return", "P_haul", "Bracket_LE_2", "Bracket_3_to_6", "Bracket_7_to_9",
                "Bracket_10_to_14", "Bracket_15_plus", "MC_MinPts", "MC_MaxPts",
            ]
        ],
        on=["GW", "player_key"],
        how="left",
    )
    mc_full["MC_CaptainMean"] = mc_full["MC_MeanPts"] * 2.0
    mc_full["MC_CaptainUpside"] = mc_full["MC_Upside"] * 2.0
    mc_full = mc_full[MC_WEEKLY_COLS].sort_values(["week", "MC_MeanPts"], ascending=[True, False]).reset_index(drop=True)
    top50 = mc_full.groupby("week", group_keys=False).head(50).reset_index(drop=True)
    return mc_fixture[MC_FIXTURE_COLS], mc_full, top50


def build_legacy_outputs(
    config: AppConfig = AppConfig(),
    manual_minutes_paths=None,
    minute_override_paths=None,
) -> dict[str, pd.DataFrame]:
    live = run_live_projection(
        config=config,
        include_mc=False,
        manual_minutes_paths=manual_minutes_paths,
        minute_override_paths=minute_override_paths,
    )
    fixture_df = fixture_player_week(live["player_fixture"], live["players"], live["teams"])
    website_contract = _website_player_week_contract(
        live["weekly"],
        live["player_fixture"],
        live["players"],
        live["teams"],
    )
    weekly_df = weekly_player_week(fixture_df, website_contract)
    qc_week, qc_fixture = qc_tables(fixture_df)
    form_audit = form_weighting_audit(live["players"], live["teams"])
    shot_audit = shot_profile_audit(live.get("shot_profiles", pd.DataFrame()))
    mc_fixture, mc_full, mc_top50 = mc_legacy_tables(
        live["player_fixture"], fixture_df, weekly_df, live["players"], live["teams"], config.n_sim, config.random_seed
    )
    weekly_df = weekly_df.drop(columns=["P_return", "P_haul"], errors="ignore").merge(
        mc_full[["GW", "player_key", "P_return", "P_haul"]],
        on=["GW", "player_key"],
        how="left",
    )
    weekly_df = weekly_df[WEEKLY_COLS].sort_values(["GW", "xPts"], ascending=[True, False]).reset_index(drop=True)
    totals_df = six_week_totals(weekly_df)

    return {
        "fixture_player_week.csv": fixture_df,
        "weekly_player_week.csv": weekly_df,
        "six_week_totals.csv": totals_df,
        "top50_p1_ga_by_week.csv": weekly_df.sort_values(["week", "P1_GA"], ascending=[True, False]).groupby("week", group_keys=False).head(50).reset_index(drop=True)[WEEKLY_COLS],
        "top50_xga_by_week.csv": weekly_df.sort_values(["week", "xGA_exp"], ascending=[True, False]).groupby("week", group_keys=False).head(50).reset_index(drop=True)[WEEKLY_COLS],
        "qc_team_week.csv": qc_week,
        "qc_team_week_fixture.csv": qc_fixture,
        "form_weighting_audit.csv": form_audit,
        "shot_profile_audit.csv": shot_audit,
        "mc_brackets_fixture_player_week.csv": mc_fixture,
        "mc_brackets_full_player_week.csv": mc_full,
        "mc_brackets_top50_by_week.csv": mc_top50,
    }


def write_legacy_outputs(
    out_dir: Path,
    config: AppConfig = AppConfig(),
    manual_minutes_paths=None,
    minute_override_paths=None,
) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs = build_legacy_outputs(
        config,
        manual_minutes_paths=manual_minutes_paths,
        minute_override_paths=minute_override_paths,
    )
    paths = {}
    for filename, df in outputs.items():
        path = out_dir / filename
        df.to_csv(path, index=False, float_format="%.6f")
        paths[filename] = path
    return paths
