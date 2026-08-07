#!/usr/bin/env python3
"""Fetch the official FPL season dimensions and emit ingest-ready CSVs.

    python3 scripts/fetch_fpl_dimensions.py --out outputs/fpl_2627 --season 2627

Writes three files in exactly the shape
``POST /api/v1/admin/projections/ingest-csvs`` already accepts:

    teams.csv             id, name, short_name
    players.csv           id, first_name, second_name, web_name
    fixtures_forecast.csv id, event, team_h, team_a, kickoff_time, finished,
                          team_h_difficulty, team_a_difficulty

WHY THIS EXISTS
---------------
The modelling pipeline only exports fixtures for its projection horizon
(``--start-gw``/``--end-gw``, default 37-38), so the ticker could never show
more than a couple of gameweeks. FPL publishes all 380 fixtures with official
difficulty ratings before a ball is kicked, and those ratings do not change
during the season -- so one fetch per season is enough.

The goal-expectation columns the model produces (home_xg, cs_prob, ...) are
deliberately absent. They are projections, not schedule facts; the ingest
treats them as optional and writes NULL. The ticker reads
``COALESCE(team_h_fdr_override, team_h_fdr_fpl)`` from ``fixtures``, so the
official ratings alone are sufficient to render it.

SEASON SAFETY
-------------
Team ids are recycled between seasons: id 3 is Bournemouth in 2026/27 and was
Burnley in 2025/26. Teams and fixtures MUST therefore be loaded as a matched
set. The script refuses to emit a partial set for that reason.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.request
from pathlib import Path

FPL_BASE = "https://fantasy.premierleague.com/api"
BOOTSTRAP_URL = f"{FPL_BASE}/bootstrap-static/"
FIXTURES_URL = f"{FPL_BASE}/fixtures/"

# FPL rejects requests without a browser-ish UA.
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; kibasfpltreats/1.0)"}

EXPECTED_TEAMS = 20
EXPECTED_FIXTURES = 380


def _get_json(url: str, timeout: int = 30) -> object:
    request = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  wrote {path}  ({len(rows)} rows)")


def build(out_dir: Path, *, strict: bool = True) -> dict[str, int]:
    print(f"fetching {BOOTSTRAP_URL}")
    bootstrap = _get_json(BOOTSTRAP_URL)
    print(f"fetching {FIXTURES_URL}")
    fixtures = _get_json(FIXTURES_URL)

    teams = [
        {"id": t["id"], "name": t["name"], "short_name": t.get("short_name")}
        for t in bootstrap["teams"]
    ]
    players = [
        {
            "id": e["id"],
            "first_name": e.get("first_name"),
            "second_name": e.get("second_name"),
            "web_name": e.get("web_name"),
        }
        for e in bootstrap["elements"]
    ]

    # Unscheduled fixtures carry event=None and cannot be placed on the ticker;
    # they reappear once FPL assigns a gameweek.
    scheduled = [f for f in fixtures if f.get("event") is not None]
    dropped = len(fixtures) - len(scheduled)
    fixture_rows = [
        {
            "id": f["id"],
            "event": f["event"],
            "team_h": f["team_h"],
            "team_a": f["team_a"],
            "kickoff_time": f.get("kickoff_time") or "",
            "finished": bool(f.get("finished")),
            "team_h_difficulty": f.get("team_h_difficulty"),
            "team_a_difficulty": f.get("team_a_difficulty"),
        }
        for f in scheduled
    ]

    missing_fdr = [
        r["id"] for r in fixture_rows
        if r["team_h_difficulty"] is None or r["team_a_difficulty"] is None
    ]

    if strict:
        if len(teams) != EXPECTED_TEAMS:
            raise SystemExit(f"expected {EXPECTED_TEAMS} teams, got {len(teams)}")
        if len(fixtures) != EXPECTED_FIXTURES:
            raise SystemExit(f"expected {EXPECTED_FIXTURES} fixtures, got {len(fixtures)}")
        if missing_fdr:
            raise SystemExit(f"{len(missing_fdr)} fixtures have no FDR: {missing_fdr[:10]}")

    _write_csv(out_dir / "teams.csv", ["id", "name", "short_name"], teams)
    _write_csv(out_dir / "players.csv",
               ["id", "first_name", "second_name", "web_name"], players)
    _write_csv(out_dir / "fixtures_forecast.csv",
               ["id", "event", "team_h", "team_a", "kickoff_time", "finished",
                "team_h_difficulty", "team_a_difficulty"], fixture_rows)

    gameweeks = sorted({r["event"] for r in fixture_rows})
    print()
    print(f"  teams        : {len(teams)}")
    print(f"  players      : {len(players)}")
    print(f"  fixtures     : {len(fixture_rows)} across GW {min(gameweeks)}-{max(gameweeks)}"
          + (f"  ({dropped} unscheduled dropped)" if dropped else ""))
    print(f"  first deadline: {bootstrap['events'][0]['deadline_time']}")
    print(f"  last deadline : {bootstrap['events'][-1]['deadline_time']}")
    return {"teams": len(teams), "players": len(players), "fixtures": len(fixture_rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="outputs/fpl_current", help="Output directory")
    parser.add_argument("--no-strict", action="store_true",
                        help="Do not fail on unexpected team/fixture counts")
    args = parser.parse_args()
    build(Path(args.out), strict=not args.no_strict)
    return 0


if __name__ == "__main__":
    sys.exit(main())
