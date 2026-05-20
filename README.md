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
  - ally lineup fit from role coverage and composition balance.
- Runs fully as a static website on GitHub Pages.

## Data refresh

GitHub Pages cannot run a resident backend process. The site therefore uses two data paths:

- Browser runtime fetches OpenDota data directly when a user opens the page.
- `scripts/build_model.py` can generate `data/model.json` as a static snapshot for faster startup and fallback behavior.

Run locally:

```powershell
python scripts/build_model.py --out data/model.json
```

Include the full opponent matchup matrix:

```powershell
python scripts/build_model.py --out data/model.json --with-matchups
```

The teammate pair matrix is intentionally left as a data-source extension point. OpenDota exposes opponent matchup aggregates publicly, but it does not expose a no-key teammate-synergy endpoint equivalent to the requested pair matrix. The frontend currently estimates ally synergy from role coverage until a richer source such as STRATZ GraphQL or a custom match parser is wired in.
