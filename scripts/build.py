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
  hourly.json  - rolling 48 h of hourly prices: 'fx' = GGG currency exchange (Faustus) VWAP, volume and
                 low/high rate per completed hour; 'nj' = poe.ninja snapshots (used for Uniques and cards)
"""
import datetime
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter

LEAGUE = os.environ.get("LEAGUE", "Covens")
LEAGUE_START = os.environ.get("LEAGUE_START", "2026-11-14")   # first day of poe.ninja data for the league
PRIORS_FILE = os.environ.get("PRIORS_FILE", "data/priors.json")    # built by scripts/history.py
PREV_HOURLY_URL = os.environ.get("PREV_HOURLY_URL", "")
PREV_LEAGUE_URL = os.environ.get("PREV_LEAGUE_URL", "")
HOURLY_KEEP_HOURS = 48
PHI = 0.55
MIN_VOLUME = 50       # exchange items: chaos traded per day
MIN_LISTINGS = 10     # uniques: listings on trade
MIN_UNIQUE_PRICE = 5  # uniques at the chaos floor have no meaningful trend
MAX_DAILY_MOVE = 0.6  # cap on daily log-momentum (about +82% / -45% per day)

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
FX = "https://web.poecdn.com/api/currency-exchange"
CHAOS_ID = "Metadata/Items/Currency/CurrencyRerollRare"
DIVINE_ID = "Metadata/Items/Currency/CurrencyModValues"
ART_NAMES = {}   # poe.ninja art file name -> item names (filled while fetching)
REPOE_URLS = [   # community game-data export: metadata id -> item name
    "https://repoe-fork.github.io/base_items.min.json",
    "https://repoe-fork.github.io/base_items.json",
    "https://raw.githubusercontent.com/lvlvllvlvllvlvl/RePoE/master/RePoE/data/base_items.min.json",
]
META_NAMES = {}  # filled by load_meta_names()
ICONS = {}       # item name -> GGG CDN image path (from poe.ninja)
META_INFO = {}   # item name -> RePoE properties (description, directions, stack size) and item class
POEDB_FILE = os.environ.get("POEDB_FILE", "data/poedb.json")
MIN_FX_HOUR_VOLUME = 20  # chaos; below this an hourly Faustus price is ignored
FX_VERSION = 2   # bump when the Faustus name mapping changes, to rebuild cached hours
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
        for i in j.get("items") or []:
            if i.get("image") and i.get("name"):
                ICONS.setdefault(i["name"], i["image"])
            art = os.path.splitext(os.path.basename(i.get("image") or ""))[0]
            if art:
                ART_NAMES.setdefault(art, set()).add(i["name"])
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
            if (l.get("listingCount") or 0) < MIN_LISTINGS or (l.get("chaosValue") or 0) < MIN_UNIQUE_PRICE:
                continue
            name = l["name"]
            if l.get("links"):
                name += f" ({l['links']}L)"
            if l.get("variant"):
                name += f" [{l['variant']}]"
            if l.get("icon"):
                ICONS.setdefault(name, l["icon"])
                ICONS.setdefault(l["name"], l["icon"])
            spark = (l.get("sparkLine") or {}).get("data") or []
            if not any(x is not None for x in spark):
                spark = (l.get("lowConfidenceSparkLine") or {}).get("data") or []
            recs.append(dict(group="Uniques", cat=label, name=name, base=l.get("baseType") or "", uname=l["name"],
                             sub=l.get("itemType") or l.get("baseType") or "",
                             links=l.get("links") or 0, price=float(l["chaosValue"]), volume=None,
                             listings=int(l.get("listingCount") or 0), spark=spark,
                             mods=dict(imp=[m.get("text") for m in l.get("implicitModifiers") or [] if m.get("text")],
                                       exp=[[m.get("text"), bool(m.get("optional"))] for m in l.get("explicitModifiers") or [] if m.get("text")],
                                       fl=l.get("flavourText") or "", lvl=l.get("levelRequired") or 0, cls=l.get("itemClass"))))
        time.sleep(0.5)
    # names must be unique keys
    seen = Counter()
    for r in recs:
        seen[r["name"]] += 1
        if seen[r["name"]] > 1:
            r["name"] = f"{r['name']} #{seen[r['name']]}"
    return recs



# ----------------------------------------------------------------------------- Faustus (GGG currency exchange)

def fx_hour(h):
    """One hourly digest from GGG's currency exchange, or None if that hour is not published yet."""
    j = get(f"{FX}/{h}")
    if not j or int(j.get("next_change_id") or 0) <= h:
        return None
    return j


def fx_prices(j, league=None):
    """Per item (metadata id): chaos VWAP, chaos volume, low and high rate, for one league (default LEAGUE) in one hour."""
    league = league or LEAGUE
    mk = [m for m in j.get("markets") or [] if m.get("league") == league]
    out = {}

    def add(x, other, rate_other):
        for m in mk:
            if other in m["market_pair"] and x in m["market_pair"]:
                vo, vx = m["volume_traded"].get(other, 0), m["volume_traded"].get(x, 0)
                if vo <= 0 or vx <= 0:
                    return None
                p = vo / vx * rate_other
                rates = []
                for key in ("lowest_ratio", "highest_ratio"):
                    r = m.get(key) or {}
                    if r.get(other) and r.get(x):
                        rates.append(r[other] / r[x] * rate_other)
                return {"p": p, "v": vo * rate_other, "lo": min(rates) if rates else None, "hi": max(rates) if rates else None}
        return None

    div = add(DIVINE_ID, CHAOS_ID, 1.0)
    if div:            # pair is stored as chaos per divine
        out[DIVINE_ID] = div
    divrate = div["p"] if div else None
    ids = {i for m in mk for i in m["market_pair"]} - {CHAOS_ID, DIVINE_ID}
    for x in ids:
        r = add(x, CHAOS_ID, 1.0)
        if not r and divrate:
            r = add(x, DIVINE_ID, divrate)
        if r:
            out[x] = r
    return out


def load_meta_names():
    """Metadata id -> name (and art file -> name) from RePoE; empty if unreachable."""
    for url in REPOE_URLS:
        try:
            data = get(url, tries=2)
        except Exception as e:
            print(f"note: RePoE {url}: {e}", file=sys.stderr)
            continue
        for mid, b in data.items():
            if b.get("name"):
                META_NAMES[mid] = b["name"]
                pr = b.get("properties") or {}
                old = META_INFO.get(b["name"])
                if not old or (not (old.get("description") or old.get("directions")) and (pr.get("description") or pr.get("directions"))):
                    META_INFO[b["name"]] = dict(pr, item_class=b.get("item_class") or "")
                art = ((b.get("visual_identity") or {}).get("dds_file") or "").rsplit("/", 1)[-1].rsplit(".", 1)[0]
                if art:
                    META_NAMES.setdefault("art:" + art, b["name"])
        print(f"RePoE: {len(META_NAMES)} names from {url}")
        return


def fx_name(meta_id):
    if meta_id == DIVINE_ID:
        return "Divine Orb"
    if meta_id in META_NAMES:
        return META_NAMES[meta_id]
    names = ART_NAMES.get(meta_id.rsplit("/", 1)[-1])
    return next(iter(names)) if names and len(names) == 1 else None


def clean_fx(out, names):
    """Blank out item-hours that are thin or far off-market. out = {"s": {name: [...]}, "v": {...}, optional "lo"/"hi"}."""
    keys = [k for k in ("s", "lo", "hi") if k in out]
    # a lone tiny trade can print an absurd rate: drop hours with negligible volume for that item
    dropped = 0
    for n in names:
        vols = [x for x in out["v"][n] if x]
        if not vols:
            continue
        floor = max(MIN_FX_HOUR_VOLUME, 0.05 * sorted(vols)[len(vols) // 2])
        for i, x in enumerate(out["v"][n]):
            if out["s"][n][i] is not None and (x or 0) < floor:
                for k in keys:
                    out[k][n][i] = None
                dropped += 1
    # an hour far from its neighbours on below-normal volume is a misprint, not a move
    for n in names:
        S, V = out["s"][n], out["v"][n]
        valid = [i for i, x in enumerate(S) if x is not None]
        vols = sorted(V[i] or 0 for i in valid)
        if len(valid) < 4 or not vols:
            continue
        med_v = vols[len(vols) // 2]
        bad = []
        for pos, i in enumerate(valid):
            nb = [S[j] for j in valid[max(0, pos - 3):pos] + valid[pos + 1:pos + 4]]
            nb.sort()
            ref = nb[len(nb) // 2]
            if ref and abs(S[i] / ref - 1) > 0.35 and (V[i] or 0) < med_v:
                bad.append(i)
        for i in bad:
            for k in keys:
                out[k][n][i] = None
        dropped += len(bad)
    return dropped


def update_fx(prev, now, known=None, keep=HOURLY_KEEP_HOURS):
    """Rolling hourly Faustus series {t, s, v, lo, hi}; fills any missing hour in the window."""
    last = int(now.timestamp()) // 3600 * 3600 - 3600
    want = [last - 3600 * k for k in range(keep - 1, -1, -1)]
    stamp = lambda h: datetime.datetime.fromtimestamp(h, datetime.timezone.utc).isoformat(timespec="minutes")
    have = {t: i for i, t in enumerate(prev.get("t") or [])}
    cols = {k: prev.get(k) or {} for k in ("s", "v", "lo", "hi")}
    rows = {}
    fetched = missing = 0
    for h in want:
        t = stamp(h)
        if t in have:
            i = have[t]
            rows[t] = {k: {n: (v[i] if i < len(v) else None) for n, v in cols[k].items()} for k in cols}
            continue
        try:
            j = fx_hour(h)
        except Exception as e:
            print(f"warn: faustus {t}: {e}", file=sys.stderr)
            j = None
        if not j:
            missing += 1
            continue
        fetched += 1
        rec = {"s": {}, "v": {}, "lo": {}, "hi": {}}
        for mid, r in fx_prices(j).items():
            n = fx_name(mid)
            if n and (known is None or n in known):
                rec["s"][n], rec["v"][n] = r4(r["p"]), round(r["v"])
                rec["lo"][n], rec["hi"][n] = r4(r["lo"]), r4(r["hi"])
        rows[t] = rec
        time.sleep(0.2)
    times = sorted(rows)
    names = sorted({n for t in times for n in rows[t]["s"] if known is None or n in known})
    out = {"t": times}
    for k in ("s", "v", "lo", "hi"):
        out[k] = {n: [rows[t][k].get(n) for t in times] for n in names}
    dropped = clean_fx(out, names)
    print(f"faustus: dropped {dropped} thin or off-market item-hours")
    print(f"faustus: {fetched} hours fetched, {missing} unavailable, {len(times)} in window, {len(names)} items mapped")
    return out


# ----------------------------------------------------------------------------- model

def momentum(vals):
    lv = [math.log(v) for v in vals]
    d = [lv[i + 1] - lv[i] for i in range(len(lv) - 1)]
    if not d:
        return None, d
    m = 0.75 * d[-1] + 0.25 * d[-2] if len(d) >= 2 else d[-1]
    return max(-MAX_DAILY_MOVE, min(MAX_DAILY_MOVE, m)), d


def path(m):
    return [m * PHI * (1 - PHI ** k) / (1 - PHI) for k in range(1, 8)]


def history_from_spark(price, spark):
    s = [None if x is None else float(x) for x in spark][-7:]
    s = [None] * (7 - len(s)) + s
    known = [i for i, v in enumerate(s) if v is not None]
    if not known:
        return [None] * 6 + [price]
    # poe.ninja pads days before an item first traded with a flat 0% value; drop that repeated lead-in
    while len(known) >= 2 and s[known[0]] == s[known[1]]:
        s[known[0]] = None
        known = known[1:]
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


def load_priors():
    try:
        with open(PRIORS_FILE) as fh:
            return json.load(fh)
    except Exception as e:
        print(f"note: no past-league priors ({e}); momentum only", file=sys.stderr)
        return None


def prior_at(g, day):
    """Median 7-day log move at this league day from past leagues (None if unknown)."""
    if not g or day < 0:
        return None
    d = min(day, len(g) - 1)
    vals = [g[k] for k in (d - 1, d, d + 1) if 0 <= k < len(g) and g[k] is not None]
    return sorted(vals)[len(vals) // 2] if vals else None


# live category label -> category name used in poe.ninja's dumps
DUMP_TYPE = {"Currency": "Currency", "Fragments": "Currency", "Essences": "Essence", "Fossils": "Fossil",
             "Resonators": "Resonator", "Oils": "Oil", "Delirium Orbs": "DeliriumOrb", "Omens": "Omen",
             "Tattoos": "Tattoo", "Artifacts": "Artifact", "Divination Cards": "DivinationCard",
             "Runegrafts": "Runegraft", "Allflame Embers": "AllflameEmber", "Astrolabes": "Astrolabe",
             "Scarabs": "Scarab", "Unique Weapons": "UniqueWeapon", "Unique Armours": "UniqueArmour",
             "Unique Accessories": "UniqueAccessory", "Unique Flasks": "UniqueFlask", "Unique Jewels": "UniqueJewel",
             "Unique Relics": "UniqueRelic", "Unique Tinctures": "UniqueTincture", "Unique Maps": "UniqueMap"}


def blend_path(mom_path, parts):
    """Cumulative 7-day log path = w0*momentum + sum(w*prior) spread evenly over the week.
    parts: [momentum weight, (weight, prior log move or None), ...]; a missing prior counts as 0, as in the backtest."""
    w0 = parts[0]
    extra = sum(w * (h or 0.0) for w, h in parts[1:])
    return [w0 * mom_path[k] + extra * (k + 1) / 7 for k in range(7)]


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
    priors = load_priors()
    if priors and "categories" not in priors:      # older file format: ignore
        priors = None
    league_day = (date - datetime.date.fromisoformat(LEAGUE_START)).days
    w = priors["weights"] if priors else {"momentum": 1.0, "item": 0.0, "category": 0.0}
    dw = priors["divineWeights"] if priors else {"momentum": 1.0, "prior": 0.0}
    pitems = priors.get("items", {}) if priors else {}
    pcats = priors.get("categories", {}) if priors else {}
    dm, _ = momentum([v for v in dh if v is not None])
    div_prior = prior_at(priors["divine"]["g"], league_day) if priors else None
    div_path = blend_path(path(dm), [dw["momentum"], (dw["prior"], div_prior)])
    div_fc = [dh[-1] * math.exp(x) for x in div_path]
    div_trend = [dh[-1] * math.exp(x) for x in path(dm)]
    div_hist = [dh[-1] * math.exp((div_prior or 0.0) * (k + 1) / 7) for k in range(7)] if div_prior is not None else div_trend

    items = []
    for r in recs:
        if r is div:
            continue
        pts = [(i, r["hist"][i] / dh[i]) for i in range(7) if r["hist"][i] is not None and dh[i]]
        if len(pts) >= 2:
            m, d = momentum([v for _, v in pts])
            pr = pitems.get(r["name"])
            h = prior_at(pr["g"], league_day) if pr else None
            ct = (pr or {}).get("t") or DUMP_TYPE.get(r["cat"])
            c = prior_at(pcats[ct]["g"], league_day) if ct in pcats else None
            fc = blend_path(path(m), [w["momentum"], (w["item"], h), (w["category"], c)])
            f7 = math.exp(fc[-1]) - 1
            trend_path = path(m)                                   # current trend only
            hist = h if (pr and pr.get("n", 0) >= 2 and h is not None) else c
            hist_src = "item" if hist is h and h is not None else ("category" if c is not None else None)
            hist_path = None if hist is None else [hist * (k + 1) / 7 for k in range(7)]
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
            tpath = [r4(pts[-1][1] * math.exp(trend_path[k]) * div_trend[k]) for k in range(7)]
            hpath = None if hist_path is None else [r4(pts[-1][1] * math.exp(hist_path[k]) * div_hist[k]) for k in range(7)]
            ft = round((math.exp(trend_path[-1]) - 1) * 100, 1)
            fh = None if hist is None else round((math.exp(hist) - 1) * 100, 1)
        else:
            f7 = total = d1 = None
            trend, sig, fpath = "New", "New", [None] * 7
            tpath = hpath = None
            ft = None
            pr = pitems.get(r["name"])
            h = prior_at(pr["g"], league_day) if pr else None
            ct = (pr or {}).get("t") or DUMP_TYPE.get(r["cat"])
            c = prior_at(pcats[ct]["g"], league_day) if ct in pcats else None
            hist = h if (pr and pr.get("n", 0) >= 2 and h is not None) else c
            hist_src = "item" if hist is h and h is not None else ("category" if c is not None else None)
            fh = None if hist is None else round((math.exp(hist) - 1) * 100, 1)
        items.append({
            "n": r["name"], "g": r["group"], "c": r["cat"], "s": r["sub"], "l": r["links"],
            **({"un": r.get("uname"), "b": r.get("base")} if r["group"] == "Uniques" else {}),
            "p": r4(r["price"]), "dv": r4(r["price"] / dh[-1]),
            "v": None if r["volume"] is None else round(r["volume"]),
            "ls": r["listings"],
            "h": [r4(x) for x in r["hist"]], "f": fpath,
            "chg": None if total is None else round(total * 100, 1),
            "d1": None if d1 is None else round(d1 * 100, 1),
            "f7": None if f7 is None else round(f7 * 100, 1),
            "sig": sig, "conf": confidence(r, len(pts)), "tr": trend,
            "hp": None if h is None else round((math.exp(h) - 1) * 100, 1),
            "hn": pitems[r["name"]]["n"] if h is not None else 0,
            "cp": None if c is None else round((math.exp(c) - 1) * 100, 1),
            "ft": ft, "fh": fh, "fhs": hist_src, "tp": tpath, "hpth": hpath,
        })
    market = {
        "generatedAt": now.isoformat(timespec="seconds"), "league": LEAGUE,
        "days": days, "fdays": fdays,
        "divine": {"h": [r4(x) for x in dh], "f": [r4(x) for x in div_fc], "ft": [r4(x) for x in div_trend],
                   "fh": None if div_prior is None else [r4(x) for x in div_hist]},
        "items": items,
        "model": "Prices converted to Divines; 7-day damped momentum (0.75*last + 0.25*previous daily log-change, "
                 f"damped by {PHI}/day), blended with the median move of the same item at the same league day in "
                 "past leagues where that history exists; Gain/Lose beyond +/-10%.",
        "leagueDay": league_day,
        "priors": None if not priors else {
            "weights": w, "divineWeights": dw, "leagues": [l["name"] for l in priors.get("leagues", [])],
            "backtest": priors["backtest"],
            "divinePct": None if div_prior is None else round((math.exp(div_prior) - 1) * 100, 1),
            "itemsWithHistory": sum(1 for it in items if it.get("hn")),
        },
    }
    return market, div


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


def attach_hourly(market, fx, nj):
    """Add 1h/6h/24h changes, last-hour volume and a 24-point sparkline to each item."""
    def pct(arr, k):
        idx = [i for i, x in enumerate(arr) if x is not None]
        if not idx:
            return None
        i = idx[-1]
        if i - k < 0 or arr[i - k] in (None, 0):
            return None
        return round((arr[i] / arr[i - k] - 1) * 100, 1)
    used = Counter()
    for it in market["items"] + [{"n": "Divine Orb", "_div": True}]:
        arr, src = (fx["s"].get(it["n"]), "fx")
        if not arr or sum(x is not None for x in arr) < 2:
            arr, src = nj["s"].get(it["n"]) or [], "nj"
        elif it.get("p"):
            # thin Faustus markets can sit far from the real price: trust them only if they agree with poe.ninja
            last = [x for x in arr if x is not None][-1]
            if abs(last / it["p"] - 1) > 0.30:
                arr, src = nj["s"].get(it["n"]) or [], "nj"
        used[src] += 1
        out = {"src": src, "c1": pct(arr, 1), "c6": pct(arr, 6), "c24": pct(arr, 24), "sp": arr[-24:]}
        if src == "fx":
            vol = fx["v"].get(it["n"]) or []
            out["vh"] = vol[-1] if vol else None
        if it.get("_div"):
            market["divine"].update(out)
        else:
            it.update(out)
    market["hourly"] = {"fxHours": len(fx["t"]), "njHours": len(nj["t"]),
                        "fxLast": fx["t"][-1] if fx["t"] else None, "sources": dict(used)}


def update_league(market, prev):
    """Daily chaos price of every item since league start, carried forward run to run.
    The last 7 days are always rewritten from poe.ninja's latest history."""
    start = datetime.date.fromisoformat(LEAGUE_START)
    if prev.get("start") != LEAGUE_START:
        prev = {}
    series = {k: list(v) for k, v in (prev.get("s") or {}).items()}
    div = list(prev.get("div") or [])

    def put(arr, idx, v):
        if idx < 0 or v is None:
            return arr
        arr = arr + [None] * (idx + 1 - len(arr))
        arr[idx] = v
        return arr

    idxs = [(datetime.date.fromisoformat(d) - start).days for d in market["days"]]
    for it in market["items"]:
        arr = series.get(it["n"], [])
        for k, v in zip(idxs, it["h"]):
            arr = put(arr, k, v)
        series[it["n"]] = arr
    for k, v in zip(idxs, market["divine"]["h"]):
        div = put(div, k, v)
    return {"start": LEAGUE_START, "league": LEAGUE, "s": series, "div": div}


def load_prev_hourly():
    if not PREV_HOURLY_URL:
        return {}
    try:
        return get(PREV_HOURLY_URL + f"?t={int(time.time())}", tries=2)
    except Exception as e:
        print(f"note: no previous hourly file ({e}); starting fresh", file=sys.stderr)
        return {}


# ----------------------------------------------------------------------------- item descriptions

def repoe_text(s):
    """RePoE text with markup like <currencyitem>{Mirror of Kalandra} reduced to plain lines."""
    s = s or ""
    for _ in range(4):
        s = re.sub(r"<[^<>{}]*>\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"<[^<>]*>", "", s).replace("{", "").replace("}", "")
    return [x.strip() for x in re.split(r"\r?\n", s) if x.strip()]


CLASS_LABEL = {"StackableCurrency": "Stackable Currency", "MapFragment": "Map Fragment", "DivinationCard": "Divination Card",
               "DelveSocketableCurrency": "Resonator", "DelveStackableSocketableCurrency": "Resonator", "MiscMapItem": "Map Item"}


def build_info(recs, market):
    """info.json: what each item is and does, for the item details panel."""
    try:
        pdb = json.load(open(POEDB_FILE, encoding="utf-8"))
    except Exception as e:
        print(f"note: no poedb cache ({e})", file=sys.stderr)
        pdb = {}
    mods = {r["name"]: r.get("mods") for r in recs if r.get("mods")}
    out = {}
    for it in market["items"] + [{"n": "Divine Orb", "g": "Exchange"}, {"n": "Chaos Orb", "g": "Exchange"}]:
        n = it["n"]
        e = {}
        if it["g"] == "Uniques":
            m = mods.get(n)
            if m:
                e = {"k": "u", "cls": m.get("cls") or it.get("s") or "", "base": it.get("b") or "", "lvl": m.get("lvl") or 0,
                     "imp": m["imp"], "exp": m["exp"], "fl": m.get("fl") or ""}
            e["pdb"] = it.get("un") or n
        else:
            rp, pd = META_INFO.get(n) or {}, pdb.get(n) or {}
            cls = rp.get("item_class") or ""
            e["cls"] = CLASS_LABEL.get(cls, cls)
            if cls == "DivinationCard":
                e["k"] = "card"
                e["rew"] = repoe_text(rp.get("description"))
                if rp.get("stack_size"):
                    e["set"] = rp["stack_size"]
                e["fl"] = pd.get("f") or ""
            else:
                e["k"] = "x"
                eff = pd.get("m") if pd.get("ok") and pd.get("m") else repoe_text(rp.get("description"))
                if eff:
                    e["eff"] = eff
                props = [p for p in (pd.get("p") or []) if not re.match(r"(Stack Size|Limit|Quality|Stackable Currency|Map Fragments?|Divination Cards?)\b", p)]
                if props:
                    e["props"] = props
                use = repoe_text(rp.get("directions")) or [re.sub(r"\s*Shift click to unstack\.?", "", u).strip() for u in (pd.get("u") or [])]
                use = [u for u in use if u]
                if use:
                    e["use"] = use
                if rp.get("stack_size"):
                    e["stack"] = rp["stack_size"]
            if pd.get("ok"):
                e["pdb"] = n
        out[n] = e
    print(f"info: {len(out)} items, {sum(1 for v in out.values() if v.get('eff') or v.get('exp') or v.get('rew'))} with text, "
          f"{sum(1 for v in out.values() if 'pdb' in v)} with a poedb page")
    return out


if __name__ == "__main__":
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "site/data"
    os.makedirs(out_dir, exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc)
    if len(sys.argv) > 2:
        recs = json.load(open(sys.argv[2], encoding="utf-8"))
    else:
        recs = fetch_all()
    market, div = build(recs, now)
    prev = load_prev_hourly()
    if "nj" not in prev and "t" in prev:          # first-generation file: poe.ninja snapshots only
        prev = {"nj": prev}
    nj = update_hourly(market, div, now, prev.get("nj") or {})
    if os.environ.get("SKIP_FAUSTUS"):
        fx = prev.get("fx") or {"t": [], "s": {}, "v": {}, "lo": {}, "hi": {}}
    else:
        load_meta_names()
        known = {it["n"] for it in market["items"]} | {"Divine Orb"}
        # cached hours are reused only if they were built with the current name mapping
        prev_fx = prev.get("fx") if prev.get("fxv") == FX_VERSION else {}
        fx = update_fx(prev_fx or {}, now, known)
    attach_hourly(market, fx, nj)
    prev_league = {}
    if PREV_LEAGUE_URL:
        try:
            prev_league = get(PREV_LEAGUE_URL + f"?t={int(time.time())}", tries=2)
        except Exception as e:
            print(f"note: no previous league file ({e}); starting from the last 7 days", file=sys.stderr)
    league = update_league(market, prev_league)
    with open(f"{out_dir}/league.json", "w") as fh:
        json.dump(league, fh, separators=(",", ":"))
    with open(f"{out_dir}/market.json", "w") as fh:
        json.dump(market, fh, separators=(",", ":"))
    if not META_INFO:
        load_meta_names()
    with open(f"{out_dir}/info.json", "w") as fh:
        json.dump(build_info(recs, market), fh, ensure_ascii=False, separators=(",", ":"))
    if ICONS:   # skipped for offline fixture builds, so an old icons.json is not overwritten with nothing
        cdn = "https://web.poecdn.com"
        names = {it["n"] for it in market["items"]} | {"Divine Orb", "Chaos Orb"}
        icons = {n: (u[len(cdn):] if u.startswith(cdn) else u) for n, u in ICONS.items() if n in names}
        card = "/image/Art/2DItems/Divination/InventoryIcon.png"   # every divination card has the same inventory icon
        for it in market["items"]:
            if it["c"] == "Divination Cards" and it["n"] not in icons:
                icons[it["n"]] = card
        try:
            with urllib.request.urlopen(urllib.request.Request(cdn + card, method="HEAD", headers={"User-Agent": UA}), timeout=20) as r:
                print(f"card icon: HTTP {r.status}")
        except Exception as e:
            print(f"warn: card icon unreachable: {e}", file=sys.stderr)
        with open(f"{out_dir}/icons.json", "w") as fh:
            json.dump(icons, fh, ensure_ascii=False, separators=(",", ":"))
        miss = [it for it in market["items"] if re.sub(r" #\d+$", "", it["n"]) not in icons]
        print(f"icons: {len(icons)}; missing {len(miss)}: {dict(Counter(it['c'] for it in miss))} e.g. {[it['n'] for it in miss[:8]]}")
    with open(f"{out_dir}/hourly.json", "w") as fh:
        json.dump({"generatedAt": market["generatedAt"], "fxv": FX_VERSION, "nj": nj, "fx": fx}, fh, separators=(",", ":"))
    c = Counter((it["g"], it["sig"]) for it in market["items"])
    print(f"{now:%Y-%m-%d %H:%M}Z {len(market['items'])} items {dict(c)}; divine {div['price']} -> "
          f"{market['divine']['f'][-1]}; hourly {market['hourly']}")
