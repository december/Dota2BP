# Dota2BP

Dota2BP is a GitHub Pages draft assistant for Dota 2 hero selection.

Live site:

https://december.github.io/Dota2BP/

## What it does

- Loads current hero win-rate data from OpenDota `heroStats`.
- Fetches enemy matchup data on demand from OpenDota `heroes/{hero_id}/matchups`.
- Scores unpicked heroes from three explainable signals:
  - base win rate,
  - counter-pick value against selected enemies,
  - ally lineup fit from inferred position demand, role coverage, role synergy, and composition balance.
- Uses a role/attack-type heuristic immediately while OpenDota matchup requests are still loading, so draft changes affect the ranking without waiting on a slow API response.
- Infers position demand for positions 1-5. If allies already contain several supports, the model strongly boosts missing core positions; if the draft is core-heavy, it boosts support/control/frontline picks.
- Loads a personal aggregate model from `data/player_model.json` and boosts heroes you have strong historical results or recent volume on.
- Loads a team aggregate model from `data/team_model.json`; choose a teammate in the UI to recommend heroes for that specific player's hero pool and inferred positions.
- Adds a history-review page that fetches recent OpenDota matches for a selected player, analyzes each picked hero against the visible enemy lineup, draft order, team fit, rank bracket, personal comfort, and win/loss outcome.
- Runs fully as a static website on GitHub Pages.

## Data refresh

GitHub Pages cannot run a resident backend process. The site therefore uses two data paths:

- Browser runtime fetches OpenDota data directly when a user opens the page.
- `scripts/build_model.py` can generate `data/model.json` as a static snapshot for faster startup and fallback behavior.

Run locally:

```powershell
python scripts/build_model.py --out data/model.json
```

Build the personal model for LouisEX:

```powershell
python scripts/build_player_model.py --steam-profile "https://steamcommunity.com/profiles/76561198182670383/" --out data/player_model.json --max-matches 300
```

Build the team model:

```powershell
python scripts/build_team_model.py `
  --player "https://steamcommunity.com/profiles/76561198182670383/" `
  --player "https://steamcommunity.com/profiles/76561198320148933/" `
  --player "https://steamcommunity.com/profiles/76561198287146196/" `
  --player "https://steamcommunity.com/id/amituofo6666/" `
  --player "https://steamcommunity.com/profiles/76561198146231486/" `
  --player "https://steamcommunity.com/profiles/76561198106506910/" `
  --player "https://steamcommunity.com/profiles/76561198343705647/" `
  --out data/team_model.json --max-matches 300
```

Include the full opponent matchup matrix:

```powershell
python scripts/build_model.py --out data/model.json --with-matchups
```

The teammate pair matrix is intentionally left as a data-source extension point. OpenDota exposes opponent matchup aggregates publicly, but it does not expose a no-key teammate-synergy endpoint equivalent to the requested pair matrix. The frontend currently estimates ally synergy from role coverage until a richer source such as STRATZ GraphQL or a custom match parser is wired in.

`data/player_model.json` stores aggregate hero stats only by default. It does not publish raw match IDs or frequent peer identities.

`data/team_model.json` is explicitly intended for the requested team model, so it includes the provided teammates' public Steam account IDs, nicknames, aggregate hero stats, and inferred position preferences. It still omits raw match IDs.
