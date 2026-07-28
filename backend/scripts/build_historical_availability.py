from __future__ import annotations

import argparse
import gzip
import io
import json
import re
import subprocess
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd


CORE_REPO = "olbauday/FPL-Core-Insights"
AYANAB_REPO = "Ayanab01/FPL_Stats"
S3_BUCKET = "fpl-2018-19-data"
USER_AGENT = "Codex-FPL-availability-inventory"
OUTPUT_COLUMNS = [
    "season",
    "GW",
    "element_id",
    "status",
    "chance_of_playing_this_round",
    "chance_of_playing_next_round",
    "snapshot_time_utc",
    "deadline_time_utc",
    "hours_before_deadline",
    "source",
    "source_ref",
    "source_record_gw",
]


def _curl_bytes(url: str) -> bytes:
    command = [
        "curl.exe",
        "--ssl-no-revoke",
        "-f",
        "-L",
        "-sS",
        "-A",
        USER_AGENT,
        url,
    ]
    result = subprocess.run(command, check=True, capture_output=True)
    return result.stdout


def _github_commits(repo: str) -> list[dict[str, str]]:
    commits: list[dict[str, str]] = []
    page = 1
    while True:
        url = f"https://api.github.com/repos/{repo}/commits?per_page=100&page={page}"
        batch = json.loads(_curl_bytes(url))
        commits.extend(
            {
                "sha": item["sha"],
                "committed": item["commit"]["committer"]["date"],
                "message": item["commit"]["message"],
            }
            for item in batch
        )
        if len(batch) < 100:
            break
        page += 1
    return commits


def _load_commit_history(repo: str, cache_path: Path | None) -> list[dict[str, str]]:
    if cache_path is None:
        return _github_commits(repo)
    return json.loads(cache_path.read_text(encoding="utf-8-sig"))


def _data_commits(commits: list[dict[str, str]]) -> pd.DataFrame:
    frame = pd.DataFrame(commits)
    frame["committed"] = pd.to_datetime(frame["committed"], utc=True)
    return frame.loc[
        frame["message"].str.lower().str.startswith("auto-update")
    ].sort_values("committed")


def _select_commit(commits: pd.DataFrame, deadline: pd.Timestamp) -> pd.Series | None:
    eligible = commits.loc[commits["committed"] < deadline]
    return None if eligible.empty else eligible.iloc[-1]


def _s3_keys() -> list[str]:
    url = f"https://{S3_BUCKET}.s3.amazonaws.com/?list-type=2&max-keys=1000"
    root = ET.fromstring(_curl_bytes(url))
    namespace = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
    return [
        element.text or ""
        for element in root.findall("s3:Contents/s3:Key", namespace)
    ]


def _s3_snapshot_time(key: str) -> pd.Timestamp:
    match = re.fullmatch(r"bootstrap-static-(\d{4}-\d{2}-\d{2}T\d{4}Z)\.json\.gz", key)
    if not match:
        raise ValueError(f"Unexpected S3 snapshot key: {key}")
    return pd.to_datetime(match.group(1), format="%Y-%m-%dT%H%MZ", utc=True)


def _latest_s3_bootstrap(keys: list[str]) -> dict:
    latest = max(keys, key=_s3_snapshot_time)
    url = f"https://{S3_BUCKET}.s3.amazonaws.com/{latest}"
    return json.loads(gzip.decompress(_curl_bytes(url)))


def _deadlines_2018_19(keys: list[str]) -> dict[int, pd.Timestamp]:
    bootstrap = _latest_s3_bootstrap(keys)
    return {
        int(event["id"]): pd.Timestamp(event["deadline_time"])
        for event in bootstrap["events"]
    }


def _deadlines_2024_25(root: Path) -> dict[int, pd.Timestamp]:
    merged = pd.read_csv(
        root / "data" / "vaastav" / "2024-25_merged_gw.csv",
        usecols=["GW", "kickoff_time"],
    )
    merged["kickoff_time"] = pd.to_datetime(merged["kickoff_time"], utc=True)
    deadlines = merged.groupby("GW")["kickoff_time"].min() - pd.Timedelta(minutes=90)
    return {int(gw): deadline for gw, deadline in deadlines.items()}


def _deadlines_2025_26(root: Path) -> dict[int, pd.Timestamp]:
    bootstrap = json.loads(
        (root / "data" / "fpl_history" / "bootstrap-static.json").read_text(
            encoding="utf-8"
        )
    )
    return {
        int(event["id"]): pd.Timestamp(event["deadline_time"])
        for event in bootstrap["events"]
    }


def _normalise_snapshot(
    frame: pd.DataFrame,
    *,
    season: str,
    gw: int,
    snapshot_time: pd.Timestamp,
    deadline: pd.Timestamp,
    source: str,
    source_ref: str,
) -> pd.DataFrame:
    required = {
        "id",
        "status",
        "chance_of_playing_this_round",
        "chance_of_playing_next_round",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"{source_ref} is missing columns: {sorted(missing)}")

    if "gw" in frame.columns:
        frame = frame.sort_values(["id", "gw"], kind="mergesort")
        frame = frame.drop_duplicates("id", keep="last")
        source_record_gw = pd.to_numeric(frame["gw"], errors="coerce")
    else:
        frame = frame.drop_duplicates("id", keep="last")
        source_record_gw = pd.Series(pd.NA, index=frame.index, dtype="Int64")

    output = pd.DataFrame(index=frame.index)
    output["season"] = season
    output["GW"] = gw
    output["element_id"] = pd.to_numeric(frame["id"], errors="raise").astype(int)
    output["status"] = frame["status"].astype("string")
    output["chance_of_playing_this_round"] = pd.to_numeric(
        frame["chance_of_playing_this_round"], errors="coerce"
    ).astype("Int64")
    output["chance_of_playing_next_round"] = pd.to_numeric(
        frame["chance_of_playing_next_round"], errors="coerce"
    ).astype("Int64")
    output["snapshot_time_utc"] = snapshot_time.isoformat()
    output["deadline_time_utc"] = deadline.isoformat()
    output["hours_before_deadline"] = round(
        (deadline - snapshot_time).total_seconds() / 3600.0, 6
    )
    output["source"] = source
    output["source_ref"] = source_ref
    output["source_record_gw"] = source_record_gw.astype("Int64")
    return output[OUTPUT_COLUMNS].reset_index(drop=True)


def _read_csv_bytes(content: bytes) -> pd.DataFrame:
    return pd.read_csv(io.BytesIO(content))


def _extract_s3(keys: list[str], deadlines: dict[int, pd.Timestamp]) -> list[pd.DataFrame]:
    snapshots = sorted((_s3_snapshot_time(key), key) for key in keys)
    outputs: list[pd.DataFrame] = []
    for gw, deadline in sorted(deadlines.items()):
        eligible = [(timestamp, key) for timestamp, key in snapshots if timestamp < deadline]
        if not eligible:
            continue
        snapshot_time, key = eligible[-1]
        url = f"https://{S3_BUCKET}.s3.amazonaws.com/{key}"
        bootstrap = json.loads(gzip.decompress(_curl_bytes(url)))
        outputs.append(
            _normalise_snapshot(
                pd.DataFrame(bootstrap["elements"]),
                season="2018-19",
                gw=gw,
                snapshot_time=snapshot_time,
                deadline=deadline,
                source=f"s3:{S3_BUCKET}",
                source_ref=url,
            )
        )
    return outputs


def _raw_github_url(repo: str, sha: str, path: str) -> str:
    encoded_path = urllib.parse.quote(path, safe="/")
    return f"https://raw.githubusercontent.com/{repo}/{sha}/{encoded_path}"


def _extract_core_2024_25(
    commits: pd.DataFrame,
    deadlines: dict[int, pd.Timestamp],
) -> list[pd.DataFrame]:
    outputs: list[pd.DataFrame] = []
    for gw, deadline in sorted(deadlines.items()):
        commit = _select_commit(commits, deadline)
        if commit is None or gw <= 1:
            continue
        source_gw = gw - 1
        path = (
            f"data/2024-2025/playerstats/gameweeks/GW{source_gw}/playerstats.csv"
        )
        url = _raw_github_url(CORE_REPO, commit["sha"], path)
        try:
            frame = _read_csv_bytes(_curl_bytes(url))
        except subprocess.CalledProcessError:
            path = "data/2024-2025/playerstats/playerstats.csv"
            url = _raw_github_url(CORE_REPO, commit["sha"], path)
            try:
                frame = _read_csv_bytes(_curl_bytes(url))
            except subprocess.CalledProcessError:
                continue
        outputs.append(
            _normalise_snapshot(
                frame,
                season="2024-25",
                gw=gw,
                snapshot_time=commit["committed"],
                deadline=deadline,
                source=f"github:{CORE_REPO}",
                source_ref=url,
            )
        )
    return outputs


def _extract_core_2025_26(
    commits: pd.DataFrame,
    deadlines: dict[int, pd.Timestamp],
) -> list[pd.DataFrame]:
    outputs: list[pd.DataFrame] = []
    for gw, deadline in sorted(deadlines.items()):
        commit = _select_commit(commits, deadline)
        if commit is None:
            continue
        if gw == 1:
            path = "data/2025-2026/playerstats.csv"
        else:
            path = f"data/2025-2026/By Gameweek/GW{gw}/playerstats.csv"
        url = _raw_github_url(CORE_REPO, commit["sha"], path)
        try:
            frame = _read_csv_bytes(_curl_bytes(url))
        except subprocess.CalledProcessError:
            frame = pd.DataFrame()
        if frame.empty:
            path = "data/2025-2026/playerstats.csv"
            url = _raw_github_url(CORE_REPO, commit["sha"], path)
            try:
                frame = _read_csv_bytes(_curl_bytes(url))
            except subprocess.CalledProcessError:
                continue
        outputs.append(
            _normalise_snapshot(
                frame,
                season="2025-26",
                gw=gw,
                snapshot_time=commit["committed"],
                deadline=deadline,
                source=f"github:{CORE_REPO}",
                source_ref=url,
            )
        )
    return outputs


def _coverage(frame: pd.DataFrame) -> pd.DataFrame:
    return (
        frame.groupby(["season", "GW"], as_index=False)
        .agg(
            players=("element_id", "nunique"),
            non_null_this_round=("chance_of_playing_this_round", "count"),
            non_null_next_round=("chance_of_playing_next_round", "count"),
            snapshot_time_utc=("snapshot_time_utc", "first"),
            deadline_time_utc=("deadline_time_utc", "first"),
            source=("source", "first"),
        )
        .sort_values(["season", "GW"])
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/availability/availability_by_gw.csv"),
    )
    parser.add_argument("--core-commits-json", type=Path)
    parser.add_argument("--ayanab-commits-json", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    output_path = args.output if args.output.is_absolute() else root / args.output

    core_history = _load_commit_history(CORE_REPO, args.core_commits_json)
    ayanab_history = _load_commit_history(AYANAB_REPO, args.ayanab_commits_json)
    core_commits = _data_commits(core_history)
    s3_keys = [key for key in _s3_keys() if key.startswith("bootstrap-static-")]

    frames = [
        *_extract_s3(s3_keys, _deadlines_2018_19(s3_keys)),
        *_extract_core_2024_25(core_commits, _deadlines_2024_25(root)),
        *_extract_core_2025_26(core_commits, _deadlines_2025_26(root)),
    ]
    availability = pd.concat(frames, ignore_index=True)
    availability = availability.sort_values(
        ["season", "GW", "element_id"], kind="mergesort"
    ).reset_index(drop=True)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    availability.to_csv(output_path, index=False)

    core_shas = {commit["sha"] for commit in core_history}
    ayanab_shas = {commit["sha"] for commit in ayanab_history}
    print(
        json.dumps(
            {
                "output": str(output_path),
                "rows": len(availability),
                "core_commits": len(core_history),
                "ayanab_commits": len(ayanab_history),
                "shared_commit_shas": len(core_shas & ayanab_shas),
                "ayanab_only_commit_shas": len(ayanab_shas - core_shas),
                "s3_bootstrap_snapshots": len(s3_keys),
            },
            indent=2,
        )
    )
    print(_coverage(availability).to_string(index=False))


if __name__ == "__main__":
    main()
