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



# ----------------------------------------------------------------------------- Faustus (GGG currency exchange)

def fx_hour(h):
    """One hourly digest from GGG's currency exchange, or None if that hour is not published yet."""
    j = get(f"{FX}/{h}")
    if not j or int(j.get("next_change_id") or 0) <= h:
        return None
    return j


def fx_prices(j):
    """Per item (metadata id): chaos VWAP, chaos volume, low and high rate, for LEAGUE in one hour."""
    mk = [m for m in j.get("markets") or [] if m.get("league") == LEAGUE]
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
    # a lone tiny trade can print an absurd rate: drop hours with negligible volume for that item
    dropped = 0
    for n in names:
        vols = [x for x in out["v"][n] if x]
        if not vols:
            continue
        floor = max(MIN_FX_HOUR_VOLUME, 0.05 * sorted(vols)[len(vols) // 2])
        for i, x in enumerate(out["v"][n]):
            if out["s"][n][i] is not None and (x or 0) < floor:
                for k in ("s", "lo", "hi"):
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
            for k in ("s", "lo", "hi"):
                out[k][n][i] = None
        dropped += len(bad)
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
    with open(f"{out_dir}/market.json", "w") as fh:
        json.dump(market, fh, separators=(",", ":"))
    with open(f"{out_dir}/hourly.json", "w") as fh:
        json.dump({"generatedAt": market["generatedAt"], "fxv": FX_VERSION, "nj": nj, "fx": fx}, fh, separators=(",", ":"))
    c = Counter((it["g"], it["sig"]) for it in market["items"])
    print(f"{now:%Y-%m-%d %H:%M}Z {len(market['items'])} items {dict(c)}; divine {div['price']} -> "
          f"{market['divine']['f'][-1]}; hourly {market['hourly']}")
