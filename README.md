# PoE market trends

A trading-terminal style market page for Path of Exile 1 (Covens league): Currency, Fragments, Essences, Fossils, Oils, Tattoos, Divination Cards and Unique items, with 1h / 6h / 24h moves, hourly charts and 7-day forecasts.

Hourly trades come from GGG's own [currency exchange API](https://www.pathofexile.com/developer/docs/reference#currencyexchange) (Faustus); daily history, Uniques and cards come from [poe.ninja](https://poe.ninja).

**Live site:** https://strix-yt.github.io/poe-market-trends/

## Pages

- `/` — the trading-terminal layout: filter sidebar, 1h / 6h / 24h / 7-day movers, sortable table, Divine hourly chart, category heat map, and item details with hourly and daily charts. Unique items link to the official trade site.
- `/simple/` — a simplified, phone-first layout with category tabs and item cards.

## How it works

- `.github/workflows/refresh.yml` runs every hour (and on demand from **Actions → Refresh prices and deploy → Run workflow**).
- `scripts/build.py` pulls each category from poe.ninja, converts prices to Divine terms and fits a damped-momentum 7-day forecast. It also pulls the last 48 completed hours from the Faustus API (volume-weighted price, volume, low/high rate) and keeps a rolling hourly history by reading the previous `hourly.json` from the live site. Output: `site/data/market.json` and `site/data/hourly.json`.
- The workflow publishes `site/` to GitHub Pages. No data is committed to the repo.

## Changing league

Edit `LEAGUE` in `.github/workflows/refresh.yml` (use the league name as it appears in poe.ninja's URL, e.g. `Covens`).

## Local preview

```bash
python scripts/build.py site/data
cd site && python -m http.server 8000
```

Forecasts are short-term momentum reads, not financial or trading advice. Not affiliated with Grinding Gear Games or poe.ninja.
