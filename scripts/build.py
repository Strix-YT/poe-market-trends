"""Fetch Path of Exile 1 prices from poe.ninja and build the site's data files.

Usage:
    python scripts/build.py [out_dir]                 # live fetch
    python scripts/build.py out_dir fixture.json      # offline, from a saved fetch_all() result

Sources (poe.ninja, league from $LEAGUE):
  * Currency Exchange overview: Currency, Fragments, Essences, Fossils, Oils, Tattoos, Divination Cards ...
    price = primaryValue (chaos), volume = volumePrimaryValue (chaos traded per day)
  * Stash item overview: Unique weapons, armour, accessories, flasks, jewels, relics, tinctures, maps
    price = chaosValue, liquidity = listingCount
  Both give a 7-day "sparkline": cumulative % change in chaos vs the first day shown.

Model: each item's daily chaos price is converted to Divines (chaos price / Divine price that day).
Daily momentum = 0.75 * last log-change + 0.25 * previous log-change, damped by PHI each day for 7 days.
Gain / Lose = forecast beyond +/-10% in Divines over 7 days.

Outputs in out_dir:
  market.json  - everything the page needs (compact keys, see build())
  hourly.json  - rolling 48 h of hourly chaos prices, appended to the copy at $PREV_HOURLY_URL
  items.json, history.json, divine.json - legacy files for the older page layout
"""
import datetime
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter

LEAGUE = os.environ.get("LEAGUE", "Covens")
PREV_HOURLY_URL = os.environ.get("PREV_HOURLY_URL", "")
HOURLY_KEEP_HOURS = 48
PHI = 0.55
MIN_VOLUME = 50       # exchange items: chaos traded per day
MIN_LISTINGS = 10     # uniques: listings on trade

EXCHANGE_TYPES = {
    "Currency": "Currency", "Fragment": "Fragments", "Essence": "Essences", "Fossil": "Fossils",
    "Resonator": "Resonators", "Oil": "Oils", "DeliriumOrb": "Delirium Orbs", "Omen": "Omens",
    "Tattoo": "Tattoos", "Artifact": "Artifacts", "DivinationCard": "Divination Cards",
    "Runegraft": "Runegrafts", "AllflameEmber": "Allflame Embers", "Astrolabe": "Astrolabes",
    "Scarab": "Scarabs",
}
UNIQUE_TYPES = {
    "UniqueWeapon": "Unique Weapons", "UniqueArmour": "Unique Armours", "UniqueAccessory": "Unique Accessories",
    "UniqueFlask": "Unique Flasks", "UniqueJewel": "Unique Jewels", "UniqueRelic": "Unique Relics",
    "UniqueTincture": "Unique Tinctures", "UniqueMap": "Unique Maps",
}
BASE = "https://poe.ninja/poe1/api/economy"
UA = "poe-market-trends (github.com/Strix-YT/poe-market-trends; hourly)"


# ----------------------------------------------------------------------------- fetching

def get(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.load(r)
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))


def fetch_all():
    """Return raw records: dicts with group, cat, name, sub, links, price, volume, listings, spark."""
    recs = []
    for t, label in EXCHANGE_TYPES.items():
        try:
            j = get(f"{BASE}/exchange/current/overview?" + urllib.parse.urlencode({"league": LEAGUE, "type": t}))
        except Exception as e:
            print(f"warn: {t}: {e}", file=sys.stderr)
            if t == "Currency":
                raise
            continue
        names = {i["id"]: i["name"] for i in (j.get("items") or [])}
        for l in j.get("lines") or []:
            if l["id"] == "chaos":
                continue
            vol = l.get("volumePrimaryValue") or 0
            if l["id"] != "divine" and vol < MIN_VOLUME:
                continue
            recs.append(dict(group="Exchange", cat=label, name=names.get(l["id"], l["id"]), sub="", links=0,
                             price=float(l["primaryValue"]), volume=float(vol), listings=None,
                             spark=(l.get("sparkline") or {}).get("data") or []))
        time.sleep(0.5)
    for t, label in UNIQUE_TYPES.items():
        try:
            j = get(f"{BASE}/stash/current/item/overview?" + urllib.parse.urlencode({"league": LEAGUE, "type": t}))
        except Exception as e:
            print(f"warn: {t}: {e}", file=sys.stderr)
            continue
        for l in j.get("lines") or []:
            if (l.get("listingCount") or 0) < MIN_LISTINGS or not l.get("chaosValue"):
                continue
            name = l["name"]
            if l.get("links"):
                name += f" ({l['links']}L)"
            if l.get("variant"):
                name += f" [{l['variant']}]"
            spark = (l.get("sparkLine") or {}).get("data") or []
            if not any(x is not None for x in spark):
                spark = (l.get("lowConfidenceSparkLine") or {}).get("data") or []
            recs.append(dict(group="Uniques", cat=label, name=name, sub=l.get("itemType") or l.get("baseType") or "",
                             links=l.get("links") or 0, price=float(l["chaosValue"]), volume=None,
                             listings=int(l.get("listingCount") or 0), spark=spark))
        time.sleep(0.5)
    # names must be unique keys
    seen = Counter()
    for r in recs:
        seen[r["name"]] += 1
        if seen[r["name"]] > 1:
            r["name"] = f"{r['name']} #{seen[r['name']]}"
    return recs


# ----------------------------------------------------------------------------- model

def momentum(vals):
    lv = [math.log(v) for v in vals]
    d = [lv[i + 1] - lv[i] for i in range(len(lv) - 1)]
    if not d:
        return None, d
    m = 0.75 * d[-1] + 0.25 * d[-2] if len(d) >= 2 else d[-1]
    return m, d


def path(m):
    return [m * PHI * (1 - PHI ** k) / (1 - PHI) for k in range(1, 8)]


def history_from_spark(price, spark):
    s = [None if x is None else float(x) for x in spark][-7:]
    s = [None] * (7 - len(s)) + s
    known = [i for i, v in enumerate(s) if v is not None]
    if not known:
        return [None] * 6 + [price]
    last = known[-1]
    return [price * (1 + v / 100) / (1 + s[last] / 100) if v is not None else None for v in s]


def confidence(r, npts):
    if r["group"] == "Uniques":
        c = "High" if r["listings"] >= 100 else "Medium" if r["listings"] >= 25 else "Low"
    else:
        c = "High" if r["volume"] >= 20000 else "Medium" if r["volume"] >= 3000 else "Low"
    if npts < 3 and c == "High":
        c = "Medium"
    return c


def r4(x):
    return None if x is None else float(f"{x:.4g}")


def build(recs, now):
    date = now.date()
    days = [(date - datetime.timedelta(days=6 - i)).isoformat() for i in range(7)]
    fdays = [(date + datetime.timedelta(days=k)).isoformat() for k in range(1, 8)]
    for r in recs:
        r["hist"] = history_from_spark(r["price"], r["spark"])
    div = next(r for r in recs if r["name"] == "Divine Orb")
    dh = div["hist"]
    # fill any gap in the Divine series so every day has a rate
    for i in range(7):
        if dh[i] is None:
            nxt = next((dh[j] for j in range(i, 7) if dh[j] is not None), None)
            dh[i] = nxt
    dm, _ = momentum([v for v in dh if v is not None])
    div_fc = [dh[-1] * math.exp(x) for x in path(dm)]

    items = []
    for r in recs:
        if r is div:
            continue
        pts = [(i, r["hist"][i] / dh[i]) for i in range(7) if r["hist"][i] is not None and dh[i]]
        if len(pts) >= 2:
            m, d = momentum([v for _, v in pts])
            fc = path(m)
            f7 = math.exp(fc[-1]) - 1
            total = pts[-1][1] / pts[0][1] - 1
            d1 = math.exp(d[-1]) - 1
            if len(d) >= 2:
                prior = sum(d[:-1]) / len(d[:-1])
                trend = ("Accelerating" if d[-1] * prior > 0 and abs(d[-1]) > abs(prior)
                         else "Reversing" if d[-1] * prior < 0 else "Slowing")
            else:
                trend = "New"
            sig = "Gain" if f7 > 0.10 else "Lose" if f7 < -0.10 else "Hold"
            fpath = [r4(pts[-1][1] * math.exp(fc[k]) * div_fc[k]) for k in range(7)]
        else:
            f7 = total = d1 = None
            trend, sig, fpath = "New", "New", [None] * 7
        items.append({
            "n": r["name"], "g": r["group"], "c": r["cat"], "s": r["sub"], "l": r["links"],
            "p": r4(r["price"]), "dv": r4(r["price"] / dh[-1]),
            "v": None if r["volume"] is None else round(r["volume"]),
            "ls": r["listings"],
            "h": [r4(x) for x in r["hist"]], "f": fpath,
            "chg": None if total is None else round(total * 100, 1),
            "d1": None if d1 is None else round(d1 * 100, 1),
            "f7": None if f7 is None else round(f7 * 100, 1),
            "sig": sig, "conf": confidence(r, len(pts)), "tr": trend,
        })
    market = {
        "generatedAt": now.isoformat(timespec="seconds"), "league": LEAGUE,
        "days": days, "fdays": fdays,
        "divine": {"h": [r4(x) for x in dh], "f": [r4(x) for x in div_fc]},
        "items": items,
        "model": "Prices converted to Divines; 7-day damped momentum (0.75*last + 0.25*previous daily log-change, "
                 f"damped by {PHI}/day); Gain/Lose beyond +/-10%.",
    }
    return market, div


def legacy(market, out_dir):
    """Files for the original page layout (exchange items only)."""
    days, fdays = market["days"], market["fdays"]
    dh, dfc = market["divine"]["h"], market["divine"]["f"]
    gen = market["generatedAt"]
    items, history = [], []
    for it in market["items"]:
        if it["g"] != "Exchange" or it["sig"] == "New":
            continue
        first = next(i for i, x in enumerate(it["h"]) if x is not None)
        items.append(dict(currency=it["n"], category=it["c"], priceChaos=it["p"], valueDiv=it["dv"],
                          volumeChaos=it["v"], firstDay=days[first], changeSinceStartPct=it["chg"],
                          change1dPct=it["d1"], forecast7dPct=it["f7"], signal=it["sig"], confidence=it["conf"],
                          trend=it["tr"], days=sum(x is not None for x in it["h"])))
        last = max(i for i, x in enumerate(it["h"]) if x is not None)
        for i, x in enumerate(it["h"]):
            if x is not None:
                history.append(dict(currency=it["n"], date=days[i], kind="Actual", chaos=x))
        history.append(dict(currency=it["n"], date=days[last], kind="Forecast", chaos=it["h"][last]))
        for k, x in enumerate(it["f"]):
            history.append(dict(currency=it["n"], date=fdays[k], kind="Forecast", chaos=x))
    divine = [dict(date=days[i], kind="Actual", chaos=v, point="now" if i == 6 else ("start" if i == 0 else ""))
              for i, v in enumerate(dh) if v is not None]
    divine.append(dict(date=days[-1], kind="Forecast", chaos=dh[-1], point=""))
    divine += [dict(date=fdays[k], kind="Forecast", chaos=v, point="fc7" if k == 6 else "") for k, v in enumerate(dfc)]
    for name, rows in [("items", items), ("history", history), ("divine", divine)]:
        with open(f"{out_dir}/{name}.json", "w") as fh:
            json.dump({"generatedAt": gen, "rows": rows}, fh, separators=(",", ":"))


def update_hourly(market, div, now, prev):
    """Append this run's prices to the rolling hourly series."""
    stamp = now.replace(minute=0, second=0, microsecond=0).isoformat(timespec="minutes")
    times = list(prev.get("t") or [])
    series = dict(prev.get("s") or {})
    if times and times[-1] == stamp:          # same hour already recorded: overwrite it
        times.pop()
        series = {k: v[:-1] for k, v in series.items()}
    times.append(stamp)
    n = len(times)
    current = {it["n"]: it["p"] for it in market["items"]}
    current["Divine Orb"] = r4(div["price"])
    for k in set(series) | set(current):
        col = series.get(k, [])
        col = col + [None] * (n - 1 - len(col))
        col.append(current.get(k))
        series[k] = col
    cutoff = (now - datetime.timedelta(hours=HOURLY_KEEP_HOURS)).isoformat(timespec="minutes")
    keep = [i for i, t in enumerate(times) if t >= cutoff[:16]]
    times = [times[i] for i in keep]
    series = {k: [v[i] for i in keep] for k, v in series.items()}
    series = {k: v for k, v in series.items() if any(x is not None for x in v)}
    return {"t": times, "s": series}


def load_prev_hourly():
    if not PREV_HOURLY_URL:
        return {}
    try:
        return get(PREV_HOURLY_URL + f"?t={int(time.time())}", tries=2)
    except Exception as e:
        print(f"note: no previous hourly file ({e}); starting fresh", file=sys.stderr)
        return {}


if __name__ == "__main__":
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "site/data"
    os.makedirs(out_dir, exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc)
    if len(sys.argv) > 2:
        recs = json.load(open(sys.argv[2], encoding="utf-8"))
    else:
        recs = fetch_all()
    market, div = build(recs, now)
    with open(f"{out_dir}/market.json", "w") as fh:
        json.dump(market, fh, separators=(",", ":"))
    legacy(market, out_dir)
    hourly = update_hourly(market, div, now, load_prev_hourly())
    with open(f"{out_dir}/hourly.json", "w") as fh:
        json.dump(hourly, fh, separators=(",", ":"))
    c = Counter((it["g"], it["sig"]) for it in market["items"])
    print(f"{now:%Y-%m-%d %H:%M}Z {len(market['items'])} items {dict(c)}; "
          f"divine {div['price']} -> {market['divine']['f'][-1]}; hourly points {len(hourly['t'])}")
