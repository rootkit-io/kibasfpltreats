from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import urllib3

from .config import AppConfig

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class DataSourceError(RuntimeError):
    pass


def _curl_get_json(url: str) -> Any:
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if curl is None:
        raise DataSourceError("curl executable was not found for FPL request fallback")
    command = [
        curl,
        "--ssl-no-revoke",
        "-L",
        "-sS",
        "--max-time",
        "30",
        "-H",
        "User-Agent: fpl-xpts/0.1",
        url,
    ]
    result = subprocess.run(command, check=False, capture_output=True)
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        stdout = result.stdout.decode("utf-8", errors="replace").strip()
        message = stderr or stdout or f"curl exited {result.returncode}"
        raise DataSourceError(f"FPL request failed: {url} ({message})")
    try:
        return json.loads(result.stdout.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise DataSourceError(f"FPL request returned invalid JSON: {url}") from exc


@dataclass
class FplClient:
    config: AppConfig = AppConfig()
    request_pause: float = 0.03

    def _get_json(self, path: str) -> Any:
        url = f"{self.config.fpl_base_url}/{path.lstrip('/')}"
        if shutil.which("curl.exe") or shutil.which("curl"):
            return _curl_get_json(url)
        try:
            response = requests.get(url, headers={"User-Agent": "fpl-xpts/0.1"}, timeout=30, verify=False)
            response.raise_for_status()
            return response.json()
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "unknown"
            raise DataSourceError(f"FPL request failed {status}: {url}") from exc
        except requests.RequestException as exc:
            raise DataSourceError(f"FPL request failed: {url} ({exc})") from exc

    def bootstrap(self) -> dict[str, Any]:
        return self._get_json("bootstrap-static/")

    def fixtures(self, event: int | None = None) -> list[dict[str, Any]]:
        suffix = "fixtures/" if event is None else f"fixtures/?event={event}"
        return self._get_json(suffix)

    def element_summary(self, element_id: int) -> dict[str, Any]:
        time.sleep(self.request_pause)
        return self._get_json(f"element-summary/{element_id}/")

    def event_live(self, event: int) -> dict[str, Any]:
        return self._get_json(f"event/{event}/live/")

    def snapshot_raw(self, out_dir: Path | None = None) -> dict[str, Path]:
        out = out_dir or self.config.raw_dir
        out.mkdir(parents=True, exist_ok=True)

        paths = {
            "bootstrap": out / "bootstrap-static.json",
            "fixtures": out / "fixtures.json",
        }
        paths["bootstrap"].write_text(json.dumps(self.bootstrap(), indent=2), encoding="utf-8")
        paths["fixtures"].write_text(json.dumps(self.fixtures(), indent=2), encoding="utf-8")
        return paths


def bootstrap_tables(bootstrap: dict[str, Any]) -> dict[str, pd.DataFrame]:
    return {
        "events": pd.DataFrame(bootstrap.get("events", [])),
        "teams": pd.DataFrame(bootstrap.get("teams", [])),
        "players": pd.DataFrame(bootstrap.get("elements", [])),
        "element_types": pd.DataFrame(bootstrap.get("element_types", [])),
        "element_stats": pd.DataFrame(bootstrap.get("element_stats", [])),
    }


def current_event(events: pd.DataFrame) -> int | None:
    if events.empty or "is_current" not in events.columns:
        return None
    current = events.loc[events["is_current"] == True, "id"]
    if current.empty:
        next_event = events.loc[events.get("is_next", False) == True, "id"]
        return int(next_event.iloc[0]) if not next_event.empty else None
    return int(current.iloc[0])
