#!/usr/bin/env python3
"""Build a Dota2BP team model from several public Steam/OpenDota profiles."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import time
import urllib.request
from pathlib import Path
from typing import Any

from build_player_model import (
    USER_AGENT,
    aggregate_opendota,
    build_model,
    hero_lookup,
    parse_steam64,
    steam64_to_account_id,
)


POSITION_BUCKETS = {
    1: {
        "Anti-Mage", "Arc Warden", "Bloodseeker", "Chaos Knight", "Clinkz", "Drow Ranger",
        "Faceless Void", "Gyrocopter", "Juggernaut", "Kez", "Lifestealer", "Lone Druid",
        "Luna", "Lycan", "Medusa", "Meepo", "Monkey King", "Morphling", "Muerta",
        "Naga Siren", "Nature's Prophet", "Phantom Assassin", "Phantom Lancer", "Razor",
        "Riki", "Slark", "Sniper", "Spectre", "Sven", "Templar Assassin", "Terrorblade",
        "Troll Warlord", "Ursa", "Weaver", "Wraith King",
    },
    2: {
        "Alchemist", "Arc Warden", "Batrider", "Broodmother", "Death Prophet", "Dragon Knight",
        "Ember Spirit", "Huskar", "Invoker", "Kez", "Kunkka", "Leshrac", "Lina",
        "Lone Druid", "Meepo", "Monkey King", "Necrophos", "Outworld Devourer",
        "Pangolier", "Puck", "Pudge", "Queen of Pain", "Razor", "Shadow Fiend",
        "Sniper", "Storm Spirit", "Templar Assassin", "Tinker", "Tiny", "Viper",
        "Visage", "Void Spirit", "Windranger", "Zeus",
    },
    3: {
        "Abaddon", "Axe", "Batrider", "Beastmaster", "Brewmaster", "Bristleback",
        "Broodmother", "Centaur Warrunner", "Dark Seer", "Dawnbreaker", "Death Prophet",
        "Doom", "Dragon Knight", "Elder Titan", "Enigma", "Legion Commander", "Lycan",
        "Magnus", "Marci", "Mars", "Nature's Prophet", "Necrophos", "Night Stalker",
        "Omniknight", "Pangolier", "Primal Beast", "Pudge", "Razor", "Sand King",
        "Slardar", "Spirit Breaker", "Tidehunter", "Timbersaw", "Tiny", "Underlord",
        "Undying", "Venomancer", "Viper", "Visage", "Windranger", "Wraith King",
    },
    4: {
        "Bane", "Bounty Hunter", "Clockwerk", "Dark Willow", "Earth Spirit", "Earthshaker",
        "Elder Titan", "Grimstroke", "Hoodwink", "Io", "Keeper of the Light", "Lion",
        "Marci", "Mirana", "Nyx Assassin", "Phoenix", "Pudge", "Rubick", "Sand King",
        "Shadow Demon", "Skywrath Mage", "Snapfire", "Spirit Breaker", "Techies",
        "Tiny", "Treant Protector", "Tusk", "Vengeful Spirit", "Venomancer", "Windranger",
        "Winter Wyvern",
    },
    5: {
        "Abaddon", "Ancient Apparition", "Bane", "Chen", "Crystal Maiden", "Dark Willow",
        "Dazzle", "Disruptor", "Enchantress", "Grimstroke", "Hoodwink", "Io", "Jakiro",
        "Keeper of the Light", "Largo", "Lich", "Lion", "Ogre Magi", "Omniknight",
        "Oracle", "Phoenix", "Pugna", "Ring Master", "Rubick", "Shadow Demon",
        "Shadow Shaman", "Silencer", "Skywrath Mage", "Snapfire", "Treant Protector",
        "Undying", "Vengeful Spirit", "Visage", "Warlock", "Winter Wyvern", "Witch Doctor",
    },
}

POSITION_LABELS = {
    1: "1号位核心",
    2: "2号位中单",
    3: "3号位劣单",
    4: "4号位游走",
    5: "5号位辅助",
}


def load_hero_meta(path: str) -> dict[int, dict[str, Any]]:
    try:
        model = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return {int(hero["id"]): hero for hero in model.get("heroes", [])}


def request_text(url: str, timeout: int = 30) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def resolve_steam64(value: str) -> int:
    parsed = parse_steam64(value)
    if parsed:
        return parsed
    html = request_text(value)
    match = re.search(r'"steamid"\s*:\s*"(\d+)"', html)
    if match:
        return int(match.group(1))
    match = re.search(r"profiles/(\d{17})", html)
    if match:
        return int(match.group(1))
    raise RuntimeError(f"Unable to resolve SteamID64 from {value}")


def position_profile_for_hero(hero: dict[str, Any]) -> dict[int, float]:
    profile = {position: 0.0 for position in range(1, 6)}
    roles = set(hero.get("roles") or [])
    name = hero.get("localized_name") or hero.get("name") or ""

    def add(position: int, value: float) -> None:
        profile[position] += value

    if "Carry" in roles:
        add(1, 0.72)
        add(2, 0.34)
        add(3, 0.22)
    if "Support" in roles:
        add(4, 0.48)
        add(5, 0.64)
    if "Initiator" in roles:
        add(3, 0.46)
        add(4, 0.22)
    if "Durable" in roles:
        add(3, 0.48)
        add(1, 0.12)
    if "Disabler" in roles:
        add(4, 0.34)
        add(5, 0.18)
        add(3, 0.16)
    if "Nuker" in roles:
        add(2, 0.44)
        add(4, 0.22)
    if "Escape" in roles:
        add(2, 0.30)
        add(1, 0.16)
    if "Pusher" in roles:
        add(1, 0.20)
        add(3, 0.22)
        add(5, 0.08)
    for position, names in POSITION_BUCKETS.items():
        if name in names:
            add(position, 1.85)
    total = sum(profile.values()) or 1.0
    return {position: value / total for position, value in profile.items()}


def add_position_summary(player_model: dict[str, Any], hero_meta: dict[int, dict[str, Any]]) -> None:
    totals = {position: 0.0 for position in range(1, 6)}
    for hero in player_model.get("heroes", {}).values():
        hero_id = int(hero["hero_id"])
        games = int(hero.get("games") or 0)
        meta = hero_meta.get(hero_id)
        if not meta or games <= 0:
            continue
        profile = position_profile_for_hero(meta)
        hero["positions"] = {
            str(position): round(value, 4)
            for position, value in profile.items()
            if value >= 0.12
        }
        hero["primary_position"] = max(profile, key=profile.get)
        for position, value in profile.items():
            totals[position] += value * games

    total_games = sum(totals.values()) or 1.0
    normalized = {position: totals[position] / total_games for position in range(1, 6)}
    player_model["position_summary"] = {
        str(position): {
            "label": POSITION_LABELS[position],
            "share": round(normalized[position], 4),
            "weighted_games": round(totals[position], 1),
        }
        for position in range(1, 6)
    }
    player_model["common_positions"] = [
        {
            "position": position,
            "label": POSITION_LABELS[position],
            "share": round(normalized[position], 4),
        }
        for position in sorted(normalized, key=normalized.get, reverse=True)[:2]
    ]


def compact_player_model(player_model: dict[str, Any]) -> dict[str, Any]:
    keep = {
        "status",
        "generated_at",
        "player",
        "summary",
        "heroes",
        "top_heroes",
        "top_recent_heroes",
        "position_summary",
        "common_positions",
        "errors",
    }
    return {key: player_model.get(key) for key in keep if key in player_model}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Dota2BP team model.")
    parser.add_argument("--player", action="append", required=True, help="Steam profile URL or SteamID64. Repeat for each teammate.")
    parser.add_argument("--hero-model", default="data/model.json")
    parser.add_argument("--out", default="data/team_model.json")
    parser.add_argument("--max-matches", type=int, default=300)
    args = parser.parse_args()

    hero_meta = load_hero_meta(args.hero_model)
    hero_names = hero_lookup(args.hero_model)
    players = []
    seen: set[int] = set()

    for index, profile in enumerate(args.player, start=1):
        print(f"[{index}/{len(args.player)}] building team player model: {profile}")
        steam64 = resolve_steam64(profile)
        account_id = steam64_to_account_id(steam64)
        if account_id in seen:
            continue
        seen.add(account_id)
        errors: list[str] = []
        opendota_data = aggregate_opendota(account_id, args.max_matches, errors)
        model = build_model(account_id, steam64, opendota_data, [], hero_names, errors, include_peers_public=False)
        add_position_summary(model, hero_meta)
        players.append(compact_player_model(model))
        time.sleep(0.6)

    team_model = {
        "schema": 1,
        "status": "ready" if players else "needs_data",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source": "OpenDota public aggregate endpoints plus public Steam profile pages",
        "players": players,
        "privacy": "Explicit team model: publishes provided teammates' Steam account IDs, nicknames, aggregate hero stats, and inferred position preferences. Raw match IDs are not included.",
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(team_model, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out_path} players={len(players)}")


if __name__ == "__main__":
    main()
