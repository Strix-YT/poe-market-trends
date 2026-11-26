# PoE market trends

A trading-terminal style market page for Path of Exile 1 (Covens league): Currency, Fragments, Essences, Fossils, Oils, Tattoos, Divination Cards and Unique items, with 1h / 6h / 24h moves, hourly charts and 7-day forecasts.

Hourly trades come from GGG's own [currency exchange API](https://www.pathofexile.com/developer/docs/reference#currencyexchange) (Faustus); daily history, Uniques and cards come from [poe.ninja](https://poe.ninja).

**Live site:** https://strix-yt.github.io/poe-market-trends/

## Pages

- `/` — the trading-terminal layout: filter sidebar, 1h / 6h / 24h / 7-day movers, sortable table, Divine hourly chart, category heat map, and item details with hourly and daily charts. Unique items link to the official trade site.
- `/simple/` — a simplified, phone-first layout with category tabs and item cards.
- `/#tracker` — **Tracker** tab: pick currencies or items to follow (☆ on any market row, quick-add, or search), optionally enter how many you hold, and see net worth in Divines, the 24h change, a 7-day value chart with all three predictions, allocation, and price alerts (type `150` for chaos or `2d` for Divines). Saved in the browser's localStorage, with Export/Import to move it between devices. Import also takes a Wealthy Exile stash export (.csv): quantities are summed across tabs and replace the tracked amounts for those items.

## Builds (character import)

The Builds tab imports a character from a **public** Path of Exile profile by account name (`Name#1234`) and prices its gear with the site's data. Browsers can't call pathofexile.com directly, so a small Cloudflare Worker (`worker/`, deployed by the `Deploy character import` workflow using the `CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_ACCOUNT_ID` repository secrets) reads the profile the same way Path of Building does. It never accepts cookies or session ids, only answers this site, and caches results for 2–5 minutes. These profile endpoints are not part of GGG's official API, so the feature can stop working if GGG changes or blocks them.

## Past leagues by the hour

`scripts/pastfx.py` (workflow `Past leagues hourly`, twice a day) fetches GGG's hourly currency-exchange digests for each past league at the same age as the current league (3 days back to 4 days ahead), cleans them with the same misprint filter as the live data, and publishes them to the orphan `pastfx` branch. The deploy serves them at `data/pastfx/`; item details show them under Compare with past leagues → Hourly. Every chart on the site can be clicked to open a large view.

## Item info

Opening an item shows **what it is and does**. Currency-exchange items use text cached from [poedb.tw](https://poedb.tw/us/) by the weekly `Item info from poedb` workflow (`scripts/poedb.py` → `data/poedb.json`, about one page every 1.5 s, refreshing entries older than 30 days), with the game's own short descriptions from RePoE as a fallback; divination card rewards come from RePoE; unique mods come from poe.ninja. The hourly build merges these into `site/data/info.json`. Each item links to its poedb page.

## How it works

- `.github/workflows/refresh.yml` runs every hour (and on demand from **Actions → Refresh prices and deploy → Run workflow**).
- `scripts/build.py` pulls each category from poe.ninja, converts prices to Divine terms and fits a damped-momentum 7-day forecast. It also pulls the last 48 completed hours from the Faustus API (volume-weighted price, volume, low/high rate) and keeps a rolling hourly history by reading the previous `hourly.json` from the live site. Output: `site/data/market.json` and `site/data/hourly.json`.
- The workflow publishes `site/` to GitHub Pages. No data is committed to the repo.

## Learning from past leagues

`.github/workflows/history.yml` (monthly, or on demand) runs `scripts/history.py`: it downloads poe.ninja's economy dumps for recent leagues (Allflame, Mirage, Keepers, Mercenaries, Settlers), replays them day by day, and compares forecasts against what actually happened (leave one league out). The winning weights and the per-item / per-category / Divine league-day patterns are saved to `data/priors.json`, which the hourly build uses. In the current replay, Gain/Lose calls came true about 63% of the time, versus 46% for plain momentum.

## Changing league

Edit `LEAGUE` and `LEAGUE_START` (first day of data) in `.github/workflows/refresh.yml`, and add the finished league to the `history.yml` list.

## Local preview

```bash
python scripts/build.py site/data
cd site && python -m http.server 8000
```

Forecasts are short-term momentum reads, not financial or trading advice. Not affiliated with Grinding Gear Games or poe.ninja.
