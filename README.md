# PoE market trends

Hourly Path of Exile 1 (Covens league) price trends and 7-day forecasts for Currency, Fragments, Essences, Fossils, Oils, Tattoos, Divination Cards and more, built from [poe.ninja](https://poe.ninja) Currency Exchange data.

**Live site:** https://strix-yt.github.io/poe-market-trends/

## How it works

- `.github/workflows/refresh.yml` runs every hour (and on demand from **Actions → Refresh prices and deploy → Run workflow**).
- `scripts/build.py` pulls each category from poe.ninja, converts prices to Divine terms, and fits a damped-momentum 7-day forecast. It writes `site/data/*.json`.
- The workflow publishes `site/` to GitHub Pages. No data is committed to the repo.

## Changing league

Edit `LEAGUE` in `.github/workflows/refresh.yml` (use the league name as it appears in poe.ninja's URL, e.g. `Covens`).

## Local preview

```bash
python scripts/build.py site/data
cd site && python -m http.server 8000
```

Forecasts are short-term momentum reads, not financial or trading advice. Not affiliated with Grinding Gear Games or poe.ninja.
