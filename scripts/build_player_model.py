#!/usr/bin/env python3
"""Build a personalized Dota2BP model from public player match data.

The output intentionally stores aggregate hero/peer stats only. It does not
publish raw match IDs or a full match-by-match history.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


STEAM_OFFSET = 76561197960265728
OPENDOTA = "https://api.opendota.com/api"
STEAM = "https://api.steampowered.com"
USER_AGENT = "Dota2BP-personal-model/1.0"


def steam64_to_account_id(steam64: int) -> int:
    return steam64 - STEAM_OFFSET


def parse_steam64(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"7656119\d{10}", value)
    if match:
        return int(match.group(0))
    if value.isdigit() and len(value) >= 16:
        return int(value)
    return None


def fetch_json(url: str, timeout: int = 40, retries: int = 2) -> Any:
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
    raise RuntimeError(f"{url}: {last_error}") from last_error


def hero_lookup(path: str) -> dict[int, str]:
    try:
      model = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
      return {}
    return {int(hero["id"]): hero["localized_name"] for hero in model.get("heroes", [])}


def win_from_row(row: dict[str, Any]) -> bool | None:
    if "radiant_win" not in row or "player_slot" not in row:
        return None
    is_radiant = int(row["player_slot"]) < 128
    return bool(row["radiant_win"]) == is_radiant


def aggregate_opendota(account_id: int, max_matches: int, errors: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "profile": None,
        "wl": None,
        "heroes": [],
        "matches": [],
        "peers": [],
    }
    endpoints = [
        ("profile", f"{OPENDOTA}/players/{account_id}"),
        ("wl", f"{OPENDOTA}/players/{account_id}/wl"),
        ("heroes", f"{OPENDOTA}/players/{account_id}/heroes"),
        ("matches", f"{OPENDOTA}/players/{account_id}/matches?limit={max_matches}"),
        ("peers", f"{OPENDOTA}/players/{account_id}/peers"),
    ]
    for key, url in endpoints:
        try:
            result[key] = fetch_json(url, timeout=55, retries=2)
        except RuntimeError as error:
            errors.append(f"OpenDota {key} unavailable: {error}")
    return result


def aggregate_steam(account_id: int, steam_key: str, max_matches: int, errors: list[str]) -> list[dict[str, Any]]:
    history_url = (
        f"{STEAM}/IDOTA2Match_570/GetMatchHistory/v1/"
        f"?key={urllib.parse.quote(steam_key)}&account_id={account_id}&matches_requested={min(max_matches, 500)}"
    )
    try:
        history = fetch_json(history_url, timeout=45, retries=2).get("result", {})
    except RuntimeError as error:
        errors.append(f"Steam GetMatchHistory unavailable: {error}")
        return []

    rows: list[dict[str, Any]] = []
    for match in history.get("matches", [])[:max_matches]:
        details_url = (
            f"{STEAM}/IDOTA2Match_570/GetMatchDetails/v1/"
            f"?key={urllib.parse.quote(steam_key)}&match_id={match['match_id']}"
        )
        try:
            details = fetch_json(details_url, timeout=45, retries=1).get("result", {})
        except RuntimeError as error:
            errors.append(f"Steam GetMatchDetails {match.get('match_id')} unavailable: {error}")
            continue
        player = next((p for p in details.get("players", []) if int(p.get("account_id", -1)) == account_id), None)
        if not player:
            continue
        row = {
            "hero_id": player.get("hero_id"),
            "player_slot": player.get("player_slot"),
            "radiant_win": details.get("radiant_win"),
            "start_time": details.get("start_time"),
            "duration": details.get("duration"),
            "kills": player.get("kills"),
            "deaths": player.get("deaths"),
            "assists": player.get("assists"),
        }
        rows.append(row)
        time.sleep(0.12)
    return rows


def build_model(
    account_id: int,
    steam64: int | None,
    opendota_data: dict[str, Any],
    steam_matches: list[dict[str, Any]],
    hero_names: dict[int, str],
    errors: list[str],
    include_peers_public: bool,
) -> dict[str, Any]:
    hero_rows = opendota_data.get("heroes") or []
    match_rows = opendota_data.get("matches") or steam_matches or []
    hero_stats: dict[int, dict[str, Any]] = defaultdict(lambda: {
        "games": 0,
        "wins": 0,
        "recent_games": 0,
        "recent_wins": 0,
        "kills": 0,
        "deaths": 0,
        "assists": 0,
    })

    for row in hero_rows:
        hero_id = int(row.get("hero_id") or row.get("id") or 0)
        if not hero_id:
            continue
        games = int(row.get("games") or 0)
        wins = int(row.get("win") or row.get("wins") or 0)
        hero_stats[hero_id]["games"] = max(hero_stats[hero_id]["games"], games)
        hero_stats[hero_id]["wins"] = max(hero_stats[hero_id]["wins"], wins)

    recent_counter: Counter[int] = Counter()
    for row in match_rows:
        hero_id = int(row.get("hero_id") or 0)
        if not hero_id:
            continue
        won = win_from_row(row)
        stats = hero_stats[hero_id]
        if not hero_rows:
            stats["games"] += 1
            if won is True:
                stats["wins"] += 1
        stats["recent_games"] += 1
        if won is True:
            stats["recent_wins"] += 1
        stats["kills"] += int(row.get("kills") or 0)
        stats["deaths"] += int(row.get("deaths") or 0)
        stats["assists"] += int(row.get("assists") or 0)
        recent_counter[hero_id] += 1

    heroes: dict[str, dict[str, Any]] = {}
    total_games = 0
    total_wins = 0
    for hero_id, stats in hero_stats.items():
        games = int(stats["games"])
        if games <= 0:
            continue
        wins = int(stats["wins"])
        total_games += games
        total_wins += wins
        heroes[str(hero_id)] = {
            "hero_id": hero_id,
            "name": hero_names.get(hero_id, str(hero_id)),
            "games": games,
            "wins": wins,
            "losses": max(0, games - wins),
            "win_rate": round(wins / games, 5),
            "recent_games": int(stats["recent_games"]),
            "recent_wins": int(stats["recent_wins"]),
            "kda": round((stats["kills"] + stats["assists"]) / max(stats["deaths"], 1), 3),
        }

    peers = []
    peer_rows = opendota_data.get("peers") or []
    for peer in (opendota_data.get("peers") or [])[:30]:
        games = int(peer.get("games") or 0)
        wins = int(peer.get("win") or peer.get("wins") or 0)
        peers.append({
            "account_id": peer.get("account_id"),
            "personaname": peer.get("personaname"),
            "games": games,
            "wins": wins,
            "win_rate": round(wins / games, 5) if games else None,
        })
    if not include_peers_public:
        peers = []

    profile = opendota_data.get("profile") or {}
    player_profile = profile.get("profile") if isinstance(profile, dict) else None
    status = "ready" if heroes else "needs_data"
    return {
        "schema": 1,
        "status": status,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "player": {
            "account_id": account_id,
            "steamid64": str(steam64) if steam64 else None,
            "personaname": (player_profile or {}).get("personaname"),
            "profileurl": (player_profile or {}).get("profileurl"),
            "avatar": (player_profile or {}).get("avatarfull"),
        },
        "summary": {
            "games": total_games,
            "wins": total_wins,
            "losses": max(0, total_games - total_wins),
            "win_rate": round(total_wins / total_games, 5) if total_games else None,
            "heroes": len(heroes),
            "recent_matches": len(match_rows),
            "frequent_peers": len(peer_rows),
        },
        "heroes": dict(sorted(heroes.items(), key=lambda item: (-item[1]["games"], item[1]["name"]))),
        "top_heroes": sorted(heroes.values(), key=lambda hero: (-hero["games"], -hero["win_rate"], hero["name"]))[:18],
        "top_recent_heroes": [
            {"hero_id": hero_id, "name": hero_names.get(hero_id, str(hero_id)), "games": games}
            for hero_id, games in recent_counter.most_common(12)
        ],
        "peers": peers,
        "sources": {
            "opendota": True,
            "steam_api": bool(steam_matches),
        },
        "errors": errors[:12],
        "privacy": "Aggregate hero stats only by default; raw match IDs and peer identities are not included.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build personalized Dota2BP player model.")
    parser.add_argument("--steam-profile", help="Steam profile URL containing a SteamID64.")
    parser.add_argument("--steamid64", type=int, help="17-digit SteamID64.")
    parser.add_argument("--account-id", type=int, help="32-bit Dota account ID.")
    parser.add_argument("--steam-api-key", default=os.environ.get("STEAM_API_KEY"), help="Optional Steam Web API key.")
    parser.add_argument("--include-peers-public", action="store_true", help="Publish frequent peer account IDs and names in the output JSON.")
    parser.add_argument("--max-matches", type=int, default=500, help="Maximum recent matches to aggregate.")
    parser.add_argument("--hero-model", default="data/model.json", help="Existing Dota2BP hero model for hero names.")
    parser.add_argument("--out", default="data/player_model.json", help="Output path.")
    args = parser.parse_args()

    steam64 = args.steamid64 or parse_steam64(args.steam_profile)
    account_id = args.account_id or (steam64_to_account_id(steam64) if steam64 else None)
    if not account_id:
        raise SystemExit("Provide --account-id, --steamid64, or --steam-profile containing a SteamID64.")

    errors: list[str] = []
    hero_names = hero_lookup(args.hero_model)
    opendota_data = aggregate_opendota(account_id, args.max_matches, errors)
    steam_matches: list[dict[str, Any]] = []
    if args.steam_api_key:
        steam_matches = aggregate_steam(account_id, args.steam_api_key, args.max_matches, errors)

    model = build_model(account_id, steam64, opendota_data, steam_matches, hero_names, errors, args.include_peers_public)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out_path} status={model['status']} heroes={len(model['heroes'])}")


if __name__ == "__main__":
    main()
