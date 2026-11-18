"""Learn how item prices move through past leagues, and test whether that helps the 7-day forecast.

Usage:  python scripts/history.py [out_file]      (default: data/priors.json)

Steps
1. Download poe.ninja's economy dumps for recent finished leagues (DUMPS below). Each zip holds a
   currency CSV and an items CSV, semicolon-separated, one row per item per day.
2. Keep the softcore challenge league, convert every item's daily chaos value to Divines, and index
   days from the league's first day of data ("league day").
3. Prior: for each item and league day d, the median over leagues of the 7-day log return
   log(p[d+7] / p[d]) in Divines (smoothed over d-1..d+1). Also the same for the Divine Orb in chaos.
4. Backtest (leave one league out): at every day of every league, compare
     momentum  - the live model (75% last daily move + 25% the one before, damped 0.55/day)
     prior     - median move of the same item at the same league day in the OTHER leagues
     blend     - a*momentum + b*prior, over a grid of a, b
   against what actually happened, and keep the weights with the lowest median absolute error.
Output: data/priors.json with per-item priors, the Divine prior, the chosen weights and the scores.
"""
import csv
import datetime
import io
import json
import math
import os
import statistics
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict

DUMPS = os.environ.get("HISTORY_LEAGUES", "Allflame,Mirage,Keepers,Mercenaries,Settlers").split(",")
URL = "https://poe.ninja/poe1/api/data/dumps/dump?name={}"
UA = "poe-market-trends (github.com/Strix-YT/poe-market-trends; history build)"
MAX_DAY = 70           # league days covered by priors
PHI, MAX_DAILY = 0.55, 0.6
MIN_CHAOS = 5          # ignore values at the chaos floor
BAD_LEAGUE_WORDS = ("hardcore", "hc ", "standard", "ruthless", "ssf", "solo")
TYPES = {}            # item name -> category
GRID = [0, 0.25, 0.5, 0.75, 1.0]
SAMPLE = 400_000      # backtest rows used for the weight search


def log(*a):
    print(*a, flush=True)


def download(name):
    req = urllib.request.Request(URL.format(urllib.parse.quote(name)), headers={"User-Agent": UA})
    for i in range(3):
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return r.read()
        except Exception as e:
            log(f"  retry {name}: {e}")
            time.sleep(10 * (i + 1))
    raise RuntimeError(f"could not download {name}")


def rows_from(zf, member):
    with zf.open(member) as fh:
        text = io.TextIOWrapper(fh, encoding="utf-8-sig", newline="")
        rd = csv.reader(text, delimiter=";")
        header = [h.strip() for h in next(rd)]
        for r in rd:
            if len(r) >= len(header):
                yield {h: v.strip() for h, v in zip(header, r)}


def item_key(r):
    name = r.get("Name") or ""
    links = (r.get("Links") or "").strip()
    variant = (r.get("Variant") or "").strip()
    if links and links not in ("0", ""):
        name += f" ({links}L)"
    if variant:
        name += f" [{variant}]"
    return name


def load_league(name):
    """Return (league_name, start_date, divine{day: chaos}, items{name: {day: chaos}})."""
    t0 = time.time()
    blob = download(name)
    zf = zipfile.ZipFile(io.BytesIO(blob))
    members = zf.namelist()
    log(f"{name}: {len(blob)/1e6:.0f} MB, files {members}")
    cur_files = [m for m in members if "currency" in m.lower() and m.lower().endswith(".csv")]
    item_files = [m for m in members if "item" in m.lower() and m.lower().endswith(".csv")]

    # pick the softcore challenge league: the most common league name that isn't HC/Standard/Ruthless/SSF
    counts = defaultdict(int)
    cur_rows = []
    for m in cur_files:
        for r in rows_from(zf, m):
            cur_rows.append(r)
            counts[r.get("League", "")] += 1
    cands = {k: v for k, v in counts.items() if k and not any(w in (k.lower() + " ") for w in BAD_LEAGUE_WORDS)}
    if not cands:
        raise RuntimeError(f"{name}: no softcore league among {dict(counts)}")
    league = max(cands, key=cands.get)
    log(f"  leagues in dump {dict(counts)} -> using {league!r}; header {list(cur_rows[0].keys()) if cur_rows else '-'}")

    def day_of(s):
        return datetime.date.fromisoformat(s[:10])

    lr = [r for r in cur_rows if r.get("League") == league]
    start = min(day_of(r["Date"]) for r in lr)
    divine, cur = {}, defaultdict(dict)
    for r in lr:
        if r.get("Pay") != "Chaos Orb":
            continue
        try:
            v = float(r["Value"])
        except (KeyError, ValueError):
            continue
        d = (day_of(r["Date"]) - start).days
        if r.get("Get") == "Divine Orb":
            divine[d] = v
        else:
            cur[r["Get"]][d] = v
            TYPES.setdefault(r["Get"], "Currency")
    items = dict(cur)
    n_item_rows = 0
    for m in item_files:
        first = True
        for r in rows_from(zf, m):
            if first:
                log(f"  items header {list(r.keys())}")
                first = False
            if r.get("League") != league:
                continue
            if (r.get("Confidence") or "").lower() == "low":
                continue
            try:
                v = float(r["Value"])
            except (KeyError, ValueError):
                continue
            d = (day_of(r["Date"]) - start).days
            k = item_key(r)
            items.setdefault(k, {})[d] = v
            TYPES.setdefault(k, r.get("Type") or "Other")
            n_item_rows += 1
    log(f"  start {start}, {len(divine)} divine days, {len(items)} items ({n_item_rows} item rows), {time.time()-t0:.0f}s")
    return league, start.isoformat(), divine, items


def to_divine_logs(divine, items):
    """{name: {day: log(price in divines)}} for days with a Divine rate and a price above the chaos floor."""
    out = {}
    for n, s in items.items():
        lv = {d: math.log(v / divine[d]) for d, v in s.items() if v >= MIN_CHAOS and divine.get(d)}
        if len(lv) >= 10:
            out[n] = lv
    return out


def fwd7(lv, d):
    if d in lv and d + 7 in lv:
        return lv[d + 7] - lv[d]
    return None


def momentum7(lv, d):
    if not all(k in lv for k in (d - 2, d - 1, d)):
        return None
    m = 0.75 * (lv[d] - lv[d - 1]) + 0.25 * (lv[d - 1] - lv[d - 2])
    m = max(-MAX_DAILY, min(MAX_DAILY, m))
    return m * PHI * (1 - PHI ** 7) / (1 - PHI)


def prior_from(per_league, d, exclude=None):
    """Median 7-day move at league day d (±1) across leagues, one value per league."""
    vals = []
    for lg, lv in per_league.items():
        if lg == exclude:
            continue
        xs = [x for x in (fwd7(lv, k) for k in (d - 1, d, d + 1)) if x is not None]
        if xs:
            vals.append(statistics.median(xs))
    return (statistics.median(vals), len(vals)) if vals else (None, 0)


def main(out_file):
    leagues = {}
    for name in DUMPS:
        name = name.strip()
        try:
            lg, start, divine, items = load_league(name)
        except Exception as e:
            log(f"skip {name}: {e}")
            continue
        leagues[lg] = {"start": start, "divine": divine, "logs": to_divine_logs(divine, items), "days": max(divine) if divine else 0}
    if len(leagues) < 2:
        raise SystemExit("need at least two leagues")

    # item -> league -> {day: log divine price}
    by_item = defaultdict(dict)
    for lg, L in leagues.items():
        for n, lv in L["logs"].items():
            by_item[n][lg] = lv
    div_logs = {lg: {d: math.log(v) for d, v in L["divine"].items() if v > 0} for lg, L in leagues.items()}

    # ---------------------------------------------------------------- category curves: league -> type -> day -> median 7d move
    cat_moves = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for n, per in by_item.items():
        t = TYPES.get(n, "Other")
        for lg, lv in per.items():
            for d in range(0, MAX_DAY + 2):
                x = fwd7(lv, d)
                if x is not None:
                    cat_moves[t][lg][d].append(x)
    cat_curves = {t: {lg: {d: statistics.median(v) for d, v in days.items()} for lg, days in lgs.items()} for t, lgs in cat_moves.items()}

    def cat_prior(t, d, exclude=None):
        vals = []
        for lg, cur in cat_curves.get(t, {}).items():
            if lg == exclude:
                continue
            xs = [cur[k] for k in (d - 1, d, d + 1) if k in cur]
            if xs:
                vals.append(statistics.median(xs))
        return statistics.median(vals) if vals else None

    # ---------------------------------------------------------------- backtest (leave one league out)
    import random
    random.seed(7)
    samples = []   # (momentum, item prior or 0, category prior or 0, actual)
    for n, per in by_item.items():
        t = TYPES.get(n, "Other")
        for lg, lv in per.items():
            for d in range(2, MAX_DAY - 7):
                a = fwd7(lv, d)
                m = momentum7(lv, d)
                if a is None or m is None:
                    continue
                samples.append((n, t, lg, d, m, a))
    log(f"backtest rows: {len(samples)}")
    if len(samples) > SAMPLE:
        samples = random.sample(samples, SAMPLE)
    rows = []
    for n, t, lg, d, m, a in samples:
        h, _ = prior_from(by_item[n], d, exclude=lg)
        c = cat_prior(t, d, exclude=lg)
        rows.append((m, h or 0.0, c or 0.0, a))

    def score(w, rows):
        wm, wh, wc = w
        err, hit, tot = [], 0, 0
        gain_ok = gain_n = lose_ok = lose_n = 0
        for m, h, c, a in rows:
            p = wm * m + wh * h + wc * c
            err.append(abs(p - a))
            if abs(a) > 0.05 and abs(p) > 1e-9:
                tot += 1
                hit += (p > 0) == (a > 0)
            if p > math.log(1.1):
                gain_n += 1; gain_ok += a > 0
            elif p < math.log(0.9):
                lose_n += 1; lose_ok += a < 0
        return {"medianAbsErrPct": round((math.exp(statistics.median(err)) - 1) * 100, 2),
                "directionHitPct": round(100 * hit / tot, 1) if tot else None,
                "gainCalls": gain_n, "gainRightPct": round(100 * gain_ok / gain_n, 1) if gain_n else None,
                "loseCalls": lose_n, "loseRightPct": round(100 * lose_ok / lose_n, 1) if lose_n else None,
                "n": len(rows)}

    results = []
    for wm in GRID:
        for wh in GRID:
            for wc in GRID:
                results.append(((wm, wh, wc), score((wm, wh, wc), rows)))
    named = {"noChange": (0, 0, 0), "momentumOnly": (1, 0, 0), "itemHistoryOnly": (0, 1, 0), "categoryHistoryOnly": (0, 0, 1)}
    table = {k: next(r[1] for r in results if r[0] == w) for k, w in named.items()}
    # best = lowest typical error among blends that still make calls in both directions
    usable = [r for r in results if r[0] != (0, 0, 0) and r[1]["gainCalls"] and r[1]["loseCalls"]]
    best = min(usable, key=lambda r: r[1]["medianAbsErrPct"])
    table["best"] = best[1]
    for k, v in table.items():
        log(f"{k:20s} {v}")
    log("best weights (momentum, item, category):", best[0])

    # ---------------------------------------------------------------- Divine in chaos: momentum vs league curve
    div_rows = []
    for lg, lv in div_logs.items():
        for d in range(2, MAX_DAY - 7):
            a, m = fwd7(lv, d), momentum7(lv, d)
            if a is None or m is None:
                continue
            h, _ = prior_from(div_logs, d, exclude=lg)
            div_rows.append((m, h or 0.0, 0.0, a))
    div_results = [((wm, wh, 0), score((wm, wh, 0), div_rows)) for wm in GRID for wh in GRID if (wm, wh) != (0, 0)]
    div_best = min(div_results, key=lambda r: r[1]["medianAbsErrPct"])
    div_table = {"noChange": score((0, 0, 0), div_rows), "momentumOnly": next(r[1] for r in div_results if r[0] == (1, 0, 0)),
                 "leagueCurveOnly": next(r[1] for r in div_results if r[0] == (0, 1, 0)), "best": div_best[1]}
    for k, v in div_table.items():
        log(f"divine {k:16s} {v}")
    log("divine best weights (momentum, league curve):", div_best[0][:2])

    # ---------------------------------------------------------------- priors for the live model
    items_out = {}
    for n, per in by_item.items():
        if len(per) < 2:
            continue
        g = []
        for d in range(MAX_DAY):
            h, k = prior_from(per, d)
            g.append(None if h is None else round(h, 3))
        if any(x is not None for x in g):
            items_out[n] = {"g": g, "n": len(per), "t": TYPES.get(n, "Other")}
    cats_out = {}
    for t in cat_curves:
        g = [cat_prior(t, d) for d in range(MAX_DAY)]
        cats_out[t] = {"g": [None if x is None else round(x, 4) for x in g], "n": len(cat_curves[t])}
    div_g = []
    for d in range(MAX_DAY):
        h, k = prior_from(div_logs, d)
        div_g.append(None if h is None else round(h, 4))

    out = {
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "leagues": [{"name": lg, "start": L["start"], "days": L["days"], "items": len(L["logs"])} for lg, L in leagues.items()],
        "maxDay": MAX_DAY,
        "weights": {"momentum": best[0][0], "item": best[0][1], "category": best[0][2]},
        "divineWeights": {"momentum": div_best[0][0], "prior": div_best[0][1]},
        "backtest": {"items": table, "divine": div_table, "sampledRows": len(rows)},
        "divine": {"g": div_g},
        "categories": cats_out,
        "items": items_out,
    }
    os.makedirs(os.path.dirname(out_file) or ".", exist_ok=True)
    with open(out_file, "w") as fh:
        json.dump(out, fh, separators=(",", ":"))
    log(f"wrote {out_file}: {len(items_out)} items, {len(cats_out)} categories, {os.path.getsize(out_file)/1e6:.2f} MB")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/priors.json")
