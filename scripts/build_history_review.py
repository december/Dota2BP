#!/usr/bin/env python3
"""Build precomputed Dota2BP match-history draft reviews."""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import datetime as dt
import json
import math
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any


OPENDOTA = "https://api.opendota.com/api"
USER_AGENT = "Dota2BP-history-review/1.0"

RANK_MEDALS = {
    1: "Herald",
    2: "Guardian",
    3: "Crusader",
    4: "Archon",
    5: "Legend",
    6: "Ancient",
    7: "Divine",
    8: "Immortal",
}

ROLE_TARGETS = {
    "Carry": 1,
    "Support": 2,
    "Disabler": 2,
    "Initiator": 1,
    "Durable": 1,
    "Nuker": 1,
    "Pusher": 1,
}

ROLE_IMPORTANCE = {
    "Carry": 1.05,
    "Support": 1.25,
    "Disabler": 1.2,
    "Initiator": 1.05,
    "Durable": 0.9,
    "Nuker": 0.72,
    "Pusher": 0.55,
}

POSITION_LABELS = {
    1: "1号位核心",
    2: "2号位中单",
    3: "3号位劣单",
    4: "4号位游走",
    5: "5号位辅助",
}

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


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def fetch_json(url: str, timeout: int, retries: int = 1) -> Any:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = error
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"{url}: {last_error}") from last_error


def pct(value: float | None, digits: int = 1) -> str:
    if value is None or not math.isfinite(value):
        return "无数据"
    return f"{value * 100:.{digits}f}%"


def bayes_win_rate(wins: Any, games: Any, prior: float = 0.5, prior_games: int = 800) -> float:
    g = int(games or 0)
    w = int(wins or 0)
    return (w + prior * prior_games) / (g + prior_games)


def rank_tier_info(rank_tier: Any) -> dict[str, Any]:
    tier = int(rank_tier or 0)
    medal = tier // 10
    star = tier % 10
    if not medal or medal not in RANK_MEDALS:
        return {"known": False, "bracket": "pub", "label": "未知分段", "short_label": "未知"}
    label = RANK_MEDALS[medal] if medal >= 8 else f"{RANK_MEDALS[medal]} {star}".strip()
    return {
        "known": True,
        "rank_tier": tier,
        "medal": medal,
        "star": star,
        "bracket": str(max(1, min(7, medal))),
        "label": label,
        "short_label": RANK_MEDALS[medal],
    }


def hero_win_rate_for_bracket(hero: dict[str, Any] | None, bracket: str) -> dict[str, Any]:
    if not hero:
        return {"rate": 0.5, "games": 0}
    if bracket == "pro":
        return {"rate": bayes_win_rate(hero.get("pro_win"), hero.get("pro_pick"), 0.5, 45), "games": int(hero.get("pro_pick") or 0)}
    if bracket != "pub":
        pick = int(hero.get(f"{bracket}_pick") or 0)
        win = int(hero.get(f"{bracket}_win") or 0)
        return {"rate": bayes_win_rate(win, pick, 0.5, 900), "games": pick}
    pick = int(hero.get("pub_pick") or 0) or sum(int(hero.get(f"{i}_pick") or 0) for i in range(1, 9))
    win = int(hero.get("pub_win") or 0) or sum(int(hero.get(f"{i}_win") or 0) for i in range(1, 9))
    return {"rate": bayes_win_rate(win, pick, 0.5, 1600), "games": pick}


def infer_position_profile(hero: dict[str, Any] | None) -> dict[int | str, float | int]:
    profile = {position: 0.0 for position in range(1, 6)}
    if not hero:
        profile["primary"] = 1
        return profile
    roles = set(hero.get("roles") or [])
    name = hero.get("localized_name") or ""

    def add(position: int, value: float) -> None:
        profile[position] += value

    if "Carry" in roles:
        add(1, 0.72); add(2, 0.34); add(3, 0.22)
    if "Support" in roles:
        add(4, 0.48); add(5, 0.64)
    if "Initiator" in roles:
        add(3, 0.46); add(4, 0.22)
    if "Durable" in roles:
        add(3, 0.48); add(1, 0.12)
    if "Disabler" in roles:
        add(4, 0.34); add(5, 0.18); add(3, 0.16)
    if "Nuker" in roles:
        add(2, 0.44); add(4, 0.22)
    if "Escape" in roles:
        add(2, 0.30); add(1, 0.16)
    if "Pusher" in roles:
        add(1, 0.20); add(3, 0.22); add(5, 0.08)
    for position, names in POSITION_BUCKETS.items():
        if name in names:
            add(position, 1.85)
    if hero.get("attack_type") == "Ranged":
        add(2, 0.08); add(5, 0.08)
    elif hero.get("attack_type") == "Melee":
        add(1, 0.08); add(3, 0.08)
    total = sum(profile.values()) or 1.0
    for position in range(1, 6):
        profile[position] /= total
    profile["primary"] = max(range(1, 6), key=lambda pos: profile[pos])
    return profile


def role_counts(hero_ids: list[int], heroes: dict[int, dict[str, Any]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for hero_id in hero_ids:
        for role in heroes.get(hero_id, {}).get("roles") or []:
            counts[role] += 1
    return counts


def composition_quality(hero_ids: list[int], heroes: dict[int, dict[str, Any]]) -> float:
    if not hero_ids:
        return 0.0
    counts = role_counts(hero_ids, heroes)
    weighted = 0.0
    possible = 0.0
    for role, target in ROLE_TARGETS.items():
        importance = ROLE_IMPORTANCE.get(role, 1.0)
        possible += importance
        weighted += min(counts.get(role, 0) / target, 1.0) * importance
    hero_rows = [heroes.get(hero_id) for hero_id in hero_ids if heroes.get(hero_id)]
    attack_types = {hero.get("attack_type") for hero in hero_rows if hero.get("attack_type")}
    attrs = {hero.get("primary_attr") for hero in hero_rows if hero.get("primary_attr")}
    carry_penalty = max(0, counts.get("Carry", 0) - 2) * 0.08
    support_penalty = 0.1 if len(hero_ids) >= 4 and counts.get("Support", 0) < 1 else 0
    return clamp(weighted / (possible or 1) + (0.08 if len(attack_types) > 1 else 0) + min(len(attrs), 3) * 0.025 - carry_penalty - support_penalty, 0, 1.2)


def position_needs(hero_ids: list[int], heroes: dict[int, dict[str, Any]]) -> dict[str, Any]:
    occupancy = {position: 0.0 for position in range(1, 6)}
    for hero_id in hero_ids:
        profile = heroes.get(hero_id, {}).get("_position_profile") or infer_position_profile(heroes.get(hero_id))
        for position in range(1, 6):
            occupancy[position] += float(profile.get(position, 0))
    demand = {position: clamp(1 - occupancy[position], 0, 1.25) for position in range(1, 6)}
    open_positions = [position for position, value in sorted(demand.items(), key=lambda item: item[1], reverse=True) if value > 0.38]
    return {"occupancy": occupancy, "demand": demand, "open": open_positions}


def position_fit(hero: dict[str, Any] | None, ally_ids: list[int], heroes: dict[int, dict[str, Any]]) -> dict[str, Any]:
    profile = hero.get("_position_profile") if hero else infer_position_profile(None)
    needs = position_needs(ally_ids, heroes)
    if not ally_ids:
        primary = int(profile.get("primary", 1))
        return {"term": 0.0, "label": POSITION_LABELS[primary], "open": needs["open"], "primary": primary}
    fit = 0.0
    overfill = 0.0
    for position in range(1, 6):
        value = float(profile.get(position, 0))
        fit += value * needs["demand"][position]
        overfill += value * max(0, needs["occupancy"][position] - 0.72)
    neutral = sum(needs["demand"].values()) / 5
    primary = int(profile.get("primary", 1))
    return {"term": clamp((fit - neutral) * 74 - overfill * 28, -24, 28), "label": POSITION_LABELS[primary], "open": needs["open"], "primary": primary}


def ally_synergy(hero: dict[str, Any] | None, ally_ids: list[int], heroes: dict[int, dict[str, Any]]) -> dict[str, Any]:
    if not hero:
        return {"term": 0.0, "tags": []}
    c = set(hero.get("roles") or [])
    term = 0.0
    tags: set[str] = set()

    def add(condition: bool, points: float, tag: str) -> None:
        nonlocal term
        if condition:
            term += points
            tags.add(tag)

    for ally_id in ally_ids:
        ally = heroes.get(ally_id)
        if not ally:
            continue
        a = set(ally.get("roles") or [])
        add("Carry" in a and ("Support" in c or "Disabler" in c), 3.2, "保护核心")
        add("Carry" in a and "Initiator" in c, 1.8, "先手开团")
        add("Support" in a and "Carry" in c, 2.8, "补核心输出")
        add("Support" in a and "Durable" in c, 1.5, "补前排")
        add("Initiator" in a and "Nuker" in c, 2.7, "跟进爆发")
        add("Carry" in a and "Carry" in c, -4.2, "核心过多")
    return {"term": clamp(term, -9, 12), "tags": list(tags)[:3]}


def team_fit(hero: dict[str, Any] | None, ally_ids: list[int], heroes: dict[int, dict[str, Any]]) -> dict[str, Any]:
    if not hero:
        return {"term": 0.0, "filled": [], "position": {"label": "未知定位", "open": []}, "synergy": {"tags": []}}
    before = composition_quality(ally_ids, heroes)
    after = composition_quality([*ally_ids, int(hero["id"])], heroes)
    counts = role_counts(ally_ids, heroes)
    needed = [role for role, target in ROLE_TARGETS.items() if counts.get(role, 0) < target]
    filled = [role for role in needed if role in (hero.get("roles") or [])]
    position = position_fit(hero, ally_ids, heroes)
    synergy = ally_synergy(hero, ally_ids, heroes)
    term = position["term"]
    if ally_ids:
        term += (after - before) * 22 + sum(ROLE_IMPORTANCE.get(role, 1.0) for role in filled) * 1.05 + synergy["term"] * 0.7
    return {"term": clamp(term, -30, 34), "filled": filled, "position": position, "synergy": synergy, "quality": after}


def segment_adjusted_rate(rate: float, candidate: dict[str, Any] | None, enemy: dict[str, Any] | None, bracket: str) -> float:
    if not candidate or not enemy or bracket in {"pub", "pro"}:
        return clamp(rate, 0.38, 0.64)
    candidate_segment = hero_win_rate_for_bracket(candidate, bracket)
    candidate_pub = hero_win_rate_for_bracket(candidate, "pub")
    enemy_segment = hero_win_rate_for_bracket(enemy, bracket)
    enemy_pub = hero_win_rate_for_bracket(enemy, "pub")
    sample_confidence = clamp(min(candidate_segment["games"], enemy_segment["games"]) / 9000, 0.35, 1)
    delta = clamp(((candidate_segment["rate"] - candidate_pub["rate"]) * 0.85 - (enemy_segment["rate"] - enemy_pub["rate"]) * 0.45) * sample_confidence, -0.035, 0.035)
    return clamp(rate + delta, 0.38, 0.64)


def matchup_heuristic(candidate: dict[str, Any] | None, enemy: dict[str, Any] | None, bracket: str) -> dict[str, Any]:
    if not candidate or not enemy:
        return {"rate": 0.5, "tags": []}
    c = set(candidate.get("roles") or [])
    e = set(enemy.get("roles") or [])
    term = 0.0
    tags: set[str] = set()

    def add(condition: bool, points: float, tag: str) -> None:
        nonlocal term
        if condition:
            term += points
            tags.add(tag)

    add("Carry" in e and "Disabler" in c, 4.8, "控制核心")
    add("Carry" in e and "Initiator" in c, 2.7, "先手抓核")
    add("Escape" in e and "Disabler" in c, 4.5, "限制逃生")
    add("Pusher" in e and ("Initiator" in c or "Nuker" in c), 3.0, "打断推进")
    add("Support" in e and "Nuker" in c, 1.8, "压制辅助")
    add("Carry" in c and "Disabler" in e, -2.2, "怕控制")
    add("Escape" in c and "Disabler" in e, -1.8, "逃生受限")
    raw_rate = clamp(0.5 + term / 82, 0.39, 0.63)
    return {"rate": segment_adjusted_rate(raw_rate, candidate, enemy, bracket), "tags": list(tags)[:3]}


def matchup_rate(candidate: dict[str, Any] | None, enemy: dict[str, Any] | None, bracket: str, matchup_rows: dict[int, dict[int, dict[str, Any]]]) -> dict[str, Any]:
    if not candidate or not enemy:
        return {"rate": 0.5, "games": 0, "estimated": True, "tags": []}
    direct = matchup_rows.get(int(candidate["id"]), {}).get(int(enemy["id"]))
    if direct and direct.get("games_played"):
        return {
            "rate": segment_adjusted_rate(bayes_win_rate(direct.get("wins"), direct.get("games_played"), 0.5, 450), candidate, enemy, bracket),
            "games": int(direct.get("games_played") or 0),
            "estimated": False,
            "tags": [],
        }
    estimate = matchup_heuristic(candidate, enemy, bracket)
    return {"rate": estimate["rate"], "games": 0, "estimated": True, "tags": estimate["tags"]}


def counter_review(hero: dict[str, Any] | None, enemy_ids: list[int], bracket: str, heroes: dict[int, dict[str, Any]], matchup_rows: dict[int, dict[int, dict[str, Any]]]) -> dict[str, Any]:
    enemies = [heroes.get(hero_id) for hero_id in enemy_ids if heroes.get(hero_id)]
    if not hero or not enemies:
        return {"avg": None, "details": [], "tags": [], "best": None, "worst": None}
    details = []
    for enemy in enemies:
        row = matchup_rate(hero, enemy, bracket, matchup_rows)
        details.append({"enemy_id": enemy["id"], "enemy_name": enemy["localized_name"], **row})
    weighted = sum(item["rate"] * (min(item["games"], 5000) + (220 if item["estimated"] else 420)) for item in details)
    weight_total = sum(min(item["games"], 5000) + (220 if item["estimated"] else 420) for item in details) or 1
    avg = weighted / weight_total
    best = max(details, key=lambda item: item["rate"])
    worst = min(details, key=lambda item: item["rate"])
    tags = []
    if avg >= 0.535:
        tags.append({"tone": "good", "text": f"整体好打 {pct(avg)}"})
    if avg <= 0.475:
        tags.append({"tone": "bad", "text": f"整体被压 {pct(avg)}"})
    if best["rate"] >= 0.55:
        tags.append({"tone": "good", "text": f"克 {best['enemy_name']}"})
    if worst["rate"] <= 0.465:
        tags.append({"tone": "bad", "text": f"怕 {worst['enemy_name']}"})
    if any(item["estimated"] for item in details):
        tags.append({"tone": "warn", "text": "部分对位为估算"})
    return {"avg": avg, "details": details, "tags": tags, "best": best, "worst": worst}


def win_from_match(summary: dict[str, Any], player_slot: Any | None = None) -> bool:
    slot = int(player_slot if player_slot is not None else summary.get("player_slot", 0))
    return bool(summary.get("radiant_win")) == (slot < 128)


def lineup_from_detail(detail: dict[str, Any] | None, summary: dict[str, Any], account_id: int) -> dict[str, Any]:
    players = [player for player in (detail or {}).get("players", []) if player.get("hero_id")]
    own = next((player for player in players if int(player.get("account_id") or -1) == account_id), None)
    if own is None:
        own = next((player for player in players if int(player.get("player_slot", -1)) == int(summary.get("player_slot", -2)) and int(player.get("hero_id", 0)) == int(summary.get("hero_id", 0))), None)
    own = own or summary
    hero_id = int(own.get("hero_id") or summary.get("hero_id") or 0)
    own_slot = int(own.get("player_slot", summary.get("player_slot", 0)))
    is_radiant = own_slot < 128
    allies = [player for player in players if (int(player.get("player_slot", 0)) < 128) == is_radiant]
    enemies = [player for player in players if (int(player.get("player_slot", 0)) < 128) != is_radiant]
    return {
        "own": own,
        "hero_id": hero_id,
        "own_slot": own_slot,
        "is_radiant": is_radiant,
        "ally_ids": [int(player["hero_id"]) for player in allies if int(player["hero_id"]) != hero_id],
        "enemy_ids": [int(player["hero_id"]) for player in enemies],
        "has_detail": bool(players),
    }


def draft_order(detail: dict[str, Any] | None, hero_id: int, is_radiant: bool, enemy_ids: list[int] | None = None) -> dict[str, Any]:
    rows = []
    for index, row in enumerate((detail or {}).get("picks_bans") or []):
        is_pick = bool(row.get("is_pick", row.get("pick")))
        if is_pick and row.get("hero_id"):
            rows.append({**row, "order_value": int(row.get("order", index))})
    if not rows:
        return {"known": False, "label": "选人顺序未公开", "before_enemy_ids": [], "after_enemy_ids": []}
    team = 0 if is_radiant else 1
    own = next((row for row in rows if int(row.get("hero_id")) == hero_id and int(row.get("team", -1)) == team), None)
    own = own or next((row for row in rows if int(row.get("hero_id")) == hero_id), None)
    if not own:
        return {"known": False, "label": "未定位到你的选人手次", "before_enemy_ids": [], "after_enemy_ids": []}
    enemy_set = set(enemy_ids or [])
    before = [int(row["hero_id"]) for row in rows if int(row.get("team", -1)) != team and row["order_value"] < own["order_value"]]
    after = [int(row["hero_id"]) for row in rows if int(row.get("team", -1)) != team and row["order_value"] > own["order_value"]]
    if enemy_set:
        before = [hero_id for hero_id in before if hero_id in enemy_set]
        after = [hero_id for hero_id in after if hero_id in enemy_set]
    return {
        "known": True,
        "order": own["order_value"] + 1,
        "label": f"第 {own['order_value'] + 1} 手",
        "before_enemy_ids": list(dict.fromkeys(before)),
        "after_enemy_ids": list(dict.fromkeys(after)),
    }


def personal_fit(hero_id: int, player: dict[str, Any]) -> dict[str, Any]:
    stats = (player.get("heroes") or {}).get(str(hero_id))
    if not stats or not stats.get("games"):
        return {"ready": False, "term": 0.0, "label": "无个人样本", "games": 0, "win_rate": None}
    games = int(stats.get("games") or 0)
    wins = int(stats.get("wins") or 0)
    win_rate = float(stats.get("win_rate") or (wins / max(games, 1)))
    recent_games = int(stats.get("recent_games") or 0)
    recent_wins = int(stats.get("recent_wins") or 0)
    recent_rate = recent_wins / recent_games if recent_games else win_rate
    confidence = clamp(games / 28, 0, 1)
    mastery = clamp(math.log10(games + 1) / 2.25, 0, 1)
    term = clamp((win_rate - 0.5) * 74 * confidence + (recent_rate - 0.5) * 28 * clamp(recent_games / 10, 0, 1) + mastery * 9 - 4, -18, 24)
    return {"ready": True, "term": term, "games": games, "win_rate": win_rate, "label": f"{games}局 {pct(win_rate)}"}


def hero_name(hero_id: int, heroes: dict[int, dict[str, Any]]) -> str:
    return heroes.get(hero_id, {}).get("localized_name") or f"英雄 {hero_id}"


def analyze_match(summary: dict[str, Any], detail: dict[str, Any] | None, player: dict[str, Any], heroes: dict[int, dict[str, Any]], matchup_rows: dict[int, dict[int, dict[str, Any]]]) -> dict[str, Any]:
    account_id = int(player["player"]["account_id"])
    lineup = lineup_from_detail(detail, summary, account_id)
    hero_id = lineup["hero_id"]
    hero = heroes.get(hero_id)
    rank_info = rank_tier_info((player.get("rank_profile") or {}).get("rank_tier"))
    bracket = rank_info["bracket"]
    won = win_from_match(summary, lineup["own_slot"])
    draft = draft_order(detail, hero_id, lineup["is_radiant"], lineup["enemy_ids"])
    base = hero_win_rate_for_bracket(hero, bracket)
    counter = counter_review(hero, lineup["enemy_ids"], bracket, heroes, matchup_rows)
    fit = team_fit(hero, lineup["ally_ids"], heroes)
    personal = personal_fit(hero_id, player)
    counter_term = 0.0 if counter["avg"] is None else (counter["avg"] - 0.5) * 100
    score = clamp(50 + (base["rate"] - 0.5) * 100 * 0.85 + counter_term * 1.55 + fit["term"] * 0.42 + personal["term"] * 0.35, 0, 100)
    tone = "good" if score >= 60 else "bad" if score <= 44 else "warn"
    verdict = "这手选得好" if tone == "good" else "这手压力较大" if tone == "bad" else "这手中性"
    tags = [
        {"tone": tone, "text": verdict},
        {"tone": "good" if won else "bad", "text": "胜利" if won else "失败"},
        {"tone": "good" if base["rate"] >= 0.515 else "bad" if base["rate"] <= 0.485 else "", "text": f"{rank_info['label']} {pct(base['rate'])}"},
    ]
    tags.extend(counter["tags"][:3])
    if fit["term"] >= 5:
        tags.append({"tone": "good", "text": "阵容适配好"})
    if fit["term"] <= -5:
        tags.append({"tone": "bad", "text": "阵容位置挤压"})
    if personal["ready"]:
        tags.append({"tone": "good" if personal["win_rate"] >= 0.54 else "bad" if personal["win_rate"] <= 0.46 else "", "text": f"个人 {personal['label']}"})
    notes = [f"基础胜率 {pct(base['rate'])}（{rank_info['label']}），阵容适配 {fit['term']:+.1f}。"]
    if counter["avg"] is not None:
        notes.append(f"对敌方阵容均值 {pct(counter['avg'])}；最好打 {counter['best']['enemy_name']}，最怕 {counter['worst']['enemy_name']}。")
    else:
        notes.append("这局没有拿到完整敌方阵容，暂时只能做基础胜率和个人熟练判断。")
    if draft["known"]:
        before = " / ".join(hero_name(hero_id, heroes) for hero_id in draft["before_enemy_ids"][:3]) or "无"
        after = " / ".join(hero_name(hero_id, heroes) for hero_id in draft["after_enemy_ids"][:3]) or "无"
        notes.append(f"{draft['label']}选出；已看到敌方：{before}；后续敌方：{after}。")
    else:
        notes.append(draft["label"])
    if score >= 60 and won:
        notes.append("BP 判断和结果一致：这手选人对胜利有正向解释。")
    elif score <= 44 and not won:
        notes.append("BP 压力和结果一致：这局可能从选人阶段就比较难受。")
    elif score >= 60 and not won:
        notes.append("选人本身不差，但结果没兑现，建议复盘对线、节奏或团战执行。")
    elif score <= 44 and won:
        notes.append("这是逆克制赢下的局，说明执行或个人发挥抵消了 BP 压力。")
    own = lineup["own"]
    return {
        "match_id": int(summary.get("match_id")),
        "start_time": summary.get("start_time"),
        "duration": summary.get("duration"),
        "won": won,
        "score": round(score, 2),
        "tone": tone,
        "verdict": verdict,
        "hero_id": hero_id,
        "hero_name": hero_name(hero_id, heroes),
        "kills": int(own.get("kills", summary.get("kills") or 0) or 0),
        "deaths": int(own.get("deaths", summary.get("deaths") or 0) or 0),
        "assists": int(own.get("assists", summary.get("assists") or 0) or 0),
        "lane_role": own.get("lane_role"),
        "average_rank": summary.get("average_rank"),
        "has_detail": lineup["has_detail"],
        "allies": lineup["ally_ids"],
        "enemies": lineup["enemy_ids"],
        "draft": draft,
        "tags": tags[:8],
        "notes": notes,
        "components": {
            "base_rate": round(base["rate"], 5),
            "counter_rate": round(counter["avg"], 5) if counter["avg"] is not None else None,
            "team_fit": round(fit["term"], 3),
            "personal": round(personal["term"], 3),
        },
    }


def summarize(matches: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(matches)
    wins = sum(1 for match in matches if match["won"])
    good = sum(1 for match in matches if match["tone"] == "good")
    bad = sum(1 for match in matches if match["tone"] == "bad")
    avg = sum(float(match["score"]) for match in matches) / total if total else 0
    detail = sum(1 for match in matches if match["has_detail"])
    return {
        "matches": total,
        "wins": wins,
        "losses": max(0, total - wins),
        "win_rate": round(wins / total, 5) if total else None,
        "avg_score": round(avg, 2),
        "good_picks": good,
        "pressured_picks": bad,
        "detail_matches": detail,
    }


def load_heroes(path: Path) -> dict[int, dict[str, Any]]:
    model = json.loads(path.read_text(encoding="utf-8"))
    heroes = {int(hero["id"]): hero for hero in model.get("heroes", [])}
    for hero in heroes.values():
        hero["_position_profile"] = infer_position_profile(hero)
    return heroes


def fetch_all_details(match_ids: list[int], timeout: int, workers: int) -> dict[int, dict[str, Any] | None]:
    details: dict[int, dict[str, Any] | None] = {}

    def fetch(match_id: int) -> tuple[int, dict[str, Any] | None]:
        try:
            return match_id, fetch_json(f"{OPENDOTA}/matches/{match_id}", timeout=timeout, retries=0)
        except Exception:
            return match_id, None

    with futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {executor.submit(fetch, match_id): match_id for match_id in match_ids}
        for index, future in enumerate(futures.as_completed(future_map), start=1):
            match_id, detail = future.result()
            details[match_id] = detail
            if index % 25 == 0 or index == len(match_ids):
                print(f"details {index}/{len(match_ids)} usable={sum(1 for item in details.values() if item)}", flush=True)
    return details


def fetch_matchups(hero_ids: list[int], timeout: int, workers: int) -> dict[int, dict[int, dict[str, Any]]]:
    matrix: dict[int, dict[int, dict[str, Any]]] = {}

    def fetch(hero_id: int) -> tuple[int, dict[int, dict[str, Any]]]:
        try:
            rows = fetch_json(f"{OPENDOTA}/heroes/{hero_id}/matchups", timeout=timeout, retries=1)
        except Exception:
            return hero_id, {}
        return hero_id, {
            int(row["hero_id"]): {
                "hero_id": int(row["hero_id"]),
                "games_played": int(row.get("games_played") or 0),
                "wins": int(row.get("wins") or 0),
            }
            for row in rows
            if row.get("hero_id")
        }

    with futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {executor.submit(fetch, hero_id): hero_id for hero_id in hero_ids}
        for index, future in enumerate(futures.as_completed(future_map), start=1):
            hero_id, rows = future.result()
            matrix[hero_id] = rows
            if index % 20 == 0 or index == len(hero_ids):
                print(f"matchups {index}/{len(hero_ids)} usable={sum(1 for rows in matrix.values() if rows)}", flush=True)
    return matrix


def fetch_player_matches(players: list[dict[str, Any]], limit: int, timeout: int, workers: int) -> dict[int, list[dict[str, Any]]]:
    rows_by_account: dict[int, list[dict[str, Any]]] = {}

    def fetch(player: dict[str, Any]) -> tuple[int, list[dict[str, Any]]]:
        account_id = int(player["player"]["account_id"])
        try:
            rows = fetch_json(f"{OPENDOTA}/players/{account_id}/matches?limit={limit}", timeout=timeout, retries=1)
        except Exception as error:
            print(f"warning: matches unavailable for {account_id}: {error}", flush=True)
            rows = []
        return account_id, rows[:limit]

    with futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {executor.submit(fetch, player): player for player in players}
        for index, future in enumerate(futures.as_completed(future_map), start=1):
            account_id, rows = future.result()
            rows_by_account[account_id] = rows
            print(f"matches {index}/{len(players)} account={account_id} rows={len(rows)}", flush=True)
    return rows_by_account


def main() -> None:
    parser = argparse.ArgumentParser(description="Build data/history_review.json.")
    parser.add_argument("--team-model", default="data/team_model.json")
    parser.add_argument("--hero-model", default="data/model.json")
    parser.add_argument("--out", default="data/history_review.json")
    parser.add_argument("--matches", type=int, default=100)
    parser.add_argument("--matches-timeout", type=int, default=25)
    parser.add_argument("--matches-workers", type=int, default=7)
    parser.add_argument("--detail-timeout", type=int, default=12)
    parser.add_argument("--detail-workers", type=int, default=24)
    parser.add_argument("--matchup-timeout", type=int, default=18)
    parser.add_argument("--matchup-workers", type=int, default=10)
    args = parser.parse_args()

    heroes = load_heroes(Path(args.hero_model))
    team_model = json.loads(Path(args.team_model).read_text(encoding="utf-8"))
    players = team_model.get("players", [])
    print(f"fetching player match lists: players={len(players)} limit={args.matches}", flush=True)
    match_rows_by_account = fetch_player_matches(players, args.matches, args.matches_timeout, args.matches_workers)

    all_match_ids = sorted({int(row["match_id"]) for rows in match_rows_by_account.values() for row in rows if row.get("match_id")}, reverse=True)
    print(f"fetching unique match details: {len(all_match_ids)}", flush=True)
    details = fetch_all_details(all_match_ids, args.detail_timeout, args.detail_workers)

    picked_hero_ids = sorted({int(row.get("hero_id") or 0) for rows in match_rows_by_account.values() for row in rows if row.get("hero_id")})
    print(f"fetching picked hero matchups: {len(picked_hero_ids)}", flush=True)
    matchup_rows = fetch_matchups(picked_hero_ids, args.matchup_timeout, args.matchup_workers)

    output_players = []
    for player in players:
        account_id = int(player["player"]["account_id"])
        reviewed = [
            analyze_match(row, details.get(int(row["match_id"])), player, heroes, matchup_rows)
            for row in match_rows_by_account.get(account_id, [])
        ]
        output_players.append({
            "account_id": account_id,
            "personaname": player["player"].get("personaname"),
            "rank_profile": player.get("rank_profile"),
            "summary": summarize(reviewed),
            "matches": reviewed,
        })

    payload = {
        "schema": 1,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source": {
            "name": "OpenDota",
            "matches": "players/{account_id}/matches",
            "details": "matches/{match_id}",
            "matchups": "heroes/{hero_id}/matchups",
        },
        "limit_per_player": args.matches,
        "players": output_players,
        "privacy": "Publishes precomputed recent match IDs, picked heroes, public draft lineups when OpenDota details are available, and BP review scores for the explicitly configured team accounts.",
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out_path} players={len(output_players)} matches={sum(len(player['matches']) for player in output_players)}")


if __name__ == "__main__":
    main()
