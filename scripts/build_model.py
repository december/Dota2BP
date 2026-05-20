#!/usr/bin/env python3
"""Build a static Dota2BP model snapshot from public OpenDota data."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


HERO_STATS_URL = "https://api.opendota.com/api/heroStats"
MATCHUP_URL = "https://api.opendota.com/api/heroes/{hero_id}/matchups"
USER_AGENT = "Dota2BP-model-builder/1.0"


def fetch_json(url: str, timeout: int = 90, retries: int = 3) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = error
            if attempt < retries:
                time.sleep(2 * attempt)
    raise RuntimeError(f"Unable to fetch {url}: {last_error}") from last_error


def compact_hero(hero: dict[str, Any]) -> dict[str, Any]:
    keep = {
        "id",
        "name",
        "localized_name",
        "primary_attr",
        "attack_type",
        "roles",
        "img",
        "icon",
        "pro_pick",
        "pro_win",
        "pro_ban",
        "pub_pick",
        "pub_win",
    }
    compact = {key: hero.get(key) for key in keep if key in hero}
    for bracket in range(1, 9):
        compact[f"{bracket}_pick"] = hero.get(f"{bracket}_pick", 0)
        compact[f"{bracket}_win"] = hero.get(f"{bracket}_win", 0)
    return compact


def build_matchups(hero_ids: list[int], sleep_seconds: float) -> dict[str, dict[str, dict[str, int]]]:
    matrix: dict[str, dict[str, dict[str, int]]] = {}
    for index, hero_id in enumerate(hero_ids, start=1):
        print(f"[{index}/{len(hero_ids)}] fetching matchups for hero {hero_id}")
        try:
            rows = fetch_json(MATCHUP_URL.format(hero_id=hero_id), timeout=120, retries=2)
        except RuntimeError as error:
            print(f"warning: {error}")
            continue
        matrix[str(hero_id)] = {
            str(row["hero_id"]): {
                "hero_id": int(row["hero_id"]),
                "games_played": int(row.get("games_played", 0)),
                "wins": int(row.get("wins", 0)),
            }
            for row in rows
            if row.get("hero_id")
        }
        time.sleep(sleep_seconds)
    return matrix


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Dota2BP data/model.json.")
    parser.add_argument("--out", default="data/model.json", help="Output JSON path.")
    parser.add_argument("--with-matchups", action="store_true", help="Fetch full opponent matchup matrix.")
    parser.add_argument("--sleep", type=float, default=0.5, help="Delay between matchup requests.")
    args = parser.parse_args()

    heroes = fetch_json(HERO_STATS_URL)
    compact_heroes = [compact_hero(hero) for hero in heroes if hero.get("id") and hero.get("localized_name")]
    compact_heroes.sort(key=lambda hero: hero["localized_name"])
    hero_ids = [int(hero["id"]) for hero in compact_heroes]

    model = {
        "schema": 1,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source": {
            "name": "OpenDota",
            "hero_stats": HERO_STATS_URL,
            "matchups": "https://api.opendota.com/api/heroes/{hero_id}/matchups",
        },
        "notes": {
            "ally_synergy": "OpenDota public endpoints do not provide teammate pair win-rate aggregates; frontend uses role-fit heuristics unless a richer data source is added.",
        },
        "heroes": compact_heroes,
        "matchups": build_matchups(hero_ids, args.sleep) if args.with_matchups else {},
        "ally_synergy": {},
        "weights": {
            "meta": 24,
            "counter": 34,
            "team": 42,
        },
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(model, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {out_path} with {len(compact_heroes)} heroes")


if __name__ == "__main__":
    main()
