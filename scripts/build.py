"""Fetch PoE 1 exchange prices from poe.ninja and build the site's data files.

Usage: python scripts/build.py [out_dir] [optional dump file for offline testing]

Dump format (what fetch_lines() produces):
Input lines (between BEGIN/END, after a 'DATE yyyy-mm-dd' line):
  category|name|chaosPriceNow|dailyVolumeChaos|sparkline
sparkline = poe.ninja exchange sparkline: cumulative % change in chaos vs the
first day, one value per day ending on DATE (7 values; a 4-value line is
left-padded). Missing days are empty.

Model: convert each item's chaos price to Divines (chaos price / Divine price
that day). Daily momentum = 0.75*last log-change + 0.25*previous log-change,
damped by PHI each day for 7 days. Gain/Lose = beyond +/-10% in Divines.
"""
import sys, json, math, datetime

PHI = 0.55
CAT_LABEL = {"Currency": "Currency", "Fragment": "Fragments", "Essence": "Essences", "Fossil": "Fossils",
             "Resonator": "Resonators", "Oil": "Oils", "DeliriumOrb": "Delirium Orbs", "Omen": "Omens",
             "Tattoo": "Tattoos", "Artifact": "Artifacts", "DivinationCard": "Divination Cards",
             "Runegraft": "Runegrafts", "AllflameEmber": "Allflame Embers", "Astrolabe": "Astrolabes",
             "Scarab": "Scarabs"}


import os, time, urllib.request, urllib.parse

LEAGUE = os.environ.get("LEAGUE", "Covens")
TYPES = ["Currency", "Fragment", "Essence", "Fossil", "Resonator", "Oil", "DeliriumOrb", "Omen", "Tattoo",
         "Artifact", "DivinationCard", "Runegraft", "AllflameEmber", "Astrolabe", "Scarab"]
MIN_VOLUME = 50  # chaos per day; thinner items are mostly noise
API = "https://poe.ninja/poe1/api/economy/exchange/current/overview"
UA = "poe-market-trends (github.com/Strix-YT/poe-market-trends; hourly)"


def get(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as e:
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))


def fetch_lines():
    """Pull every category from poe.ninja and return dump lines in the format build() reads."""
    date = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    out = ["DATE " + date, "BEGIN"]
    for t in TYPES:
        try:
            j = get(API + "?" + urllib.parse.urlencode({"league": LEAGUE, "type": t}))
        except Exception as e:
            print(f"warn: {t}: {e}", file=sys.stderr)
            if t == "Currency":
                raise
            continue
        names = {i["id"]: i["name"] for i in (j.get("items") or [])}
        for l in j.get("lines") or []:
            if l["id"] == "chaos":
                continue
            if l["id"] != "divine" and not (l.get("volumePrimaryValue") or 0) >= MIN_VOLUME:
                continue
            s = (l.get("sparkline") or {}).get("data") or []
            if not any(x is not None for x in s):
                continue
            spark = ",".join("" if x is None else str(round(float(x), 1)) for x in s)
            out.append("|".join([t, names.get(l["id"], l["id"]), f"{float(l['primaryValue']):.5g}",
                                 str(round(l.get("volumePrimaryValue") or 0)), spark]))
        time.sleep(0.5)
    out.append("END")
    return out


if __name__ == "__main__":
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "site/data"
    os.makedirs(out_dir, exist_ok=True)
    lines = open(sys.argv[2], encoding="utf-8").read().splitlines() if len(sys.argv) > 2 else fetch_lines()
    date = next(l.split()[1] for l in lines if l.startswith("DATE "))
    body = lines[lines.index("BEGIN") + 1: lines.index("END")]
    end = datetime.date.fromisoformat(date)
    days = [(end - datetime.timedelta(days=6 - i)).isoformat() for i in range(7)]
    fdays = [(end + datetime.timedelta(days=k)).isoformat() for k in range(1, 8)]

    rows = []
    for l in body:
        if not l.strip():
            continue
        cat, name, pv, vol, spark = l.split("|")
        s = [float(x) if x.strip() else None for x in spark.split(",")]
        s = [None] * (7 - len(s)) + s
        pv = float(pv)
        last = max(i for i, v in enumerate(s) if v is not None)
        hist = [pv * (1 + v / 100) / (1 + s[last] / 100) if v is not None else None for v in s]
        rows.append(dict(cat=cat, name=name, pv=pv, vol=float(vol), hist=hist))

    div = next(r for r in rows if r["name"] == "Divine Orb")
    dh = div["hist"]

    def momentum(vals):
        lv = [math.log(v) for v in vals]
        d = [lv[i + 1] - lv[i] for i in range(len(lv) - 1)]
        if not d:
            return None, d
        m = 0.75 * d[-1] + 0.25 * d[-2] if len(d) >= 2 else d[-1]
        return m, d

    def path(m):
        return [m * PHI * (1 - PHI ** k) / (1 - PHI) for k in range(1, 8)]

    dm, dd = momentum([v for v in dh if v is not None])
    div_fc = [dh[-1] * math.exp(x) for x in path(dm)]

    items, history = [], []
    for r in rows:
        if r is div:
            continue
        pts = [(i, r["hist"][i] / dh[i]) for i in range(7) if r["hist"][i] is not None and dh[i]]
        if len(pts) < 2:
            continue
        m, d = momentum([v for _, v in pts])
        fc = path(m)
        f7 = math.exp(fc[-1]) - 1
        total = pts[-1][1] / pts[0][1] - 1
        lastchg = math.exp(d[-1]) - 1
        vol = r["vol"]
        conf = "High" if vol >= 20000 else "Medium" if vol >= 3000 else "Low"
        if len(pts) < 3 and conf == "High":
            conf = "Medium"
        if len(d) >= 2:
            prior = sum(d[:-1]) / len(d[:-1])
            trend = "Accelerating" if d[-1] * prior > 0 and abs(d[-1]) > abs(prior) else ("Reversing" if d[-1] * prior < 0 else "Slowing")
        else:
            trend = "New"
        sig = "Gain" if f7 > 0.10 else "Lose" if f7 < -0.10 else "Hold"
        first_day = days[pts[0][0]]
        items.append(dict(currency=r["name"], category=CAT_LABEL.get(r["cat"], r["cat"]), priceChaos=r["pv"],
                          valueDiv=round(pts[-1][1], 8), volumeChaos=round(vol), firstDay=first_day,
                          changeSinceStartPct=round(total * 100, 1), change1dPct=round(lastchg * 100, 1),
                          forecast7dPct=round(f7 * 100, 1), signal=sig, confidence=conf, trend=trend, days=len(pts)))
        for i, v in pts:
            history.append(dict(currency=r["name"], date=days[i], kind="Actual", chaos=round(r["hist"][i], 6)))
        history.append(dict(currency=r["name"], date=days[pts[-1][0]], kind="Forecast", chaos=round(r["hist"][pts[-1][0]], 6)))
        for k, day in enumerate(fdays):
            history.append(dict(currency=r["name"], date=day, kind="Forecast", chaos=round(pts[-1][1] * math.exp(fc[k]) * div_fc[k], 6)))

    divine = []
    for i, v in enumerate(dh):
        if v is not None:
            divine.append(dict(date=days[i], kind="Actual", chaos=round(v, 2), point="now" if i == 6 else ("start" if not divine else "")))
    divine.append(dict(date=date, kind="Forecast", chaos=round(dh[-1], 2), point=""))
    for k, day in enumerate(fdays):
        divine.append(dict(date=day, kind="Forecast", chaos=round(div_fc[k], 2), point="fc7" if k == 6 else ""))

    code = open(__file__, encoding="utf-8").read()
    meta = [{"label": "Source", "value": f"poe.ninja PoE 1 Currency Exchange overview, league {LEAGUE}, as of {date}"},
            {"label": "refresh.py", "value": code, "type": "code", "lang": "python"}]
    for name, data in [("items", items), ("history", history), ("divine", divine)]:
        json.dump({"generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "rows": data, "meta": meta}, open(f"{out_dir}/{name}.json", "w"), separators=(",", ":"))
    from collections import Counter
    print(date, len(items), "items;", Counter(i["signal"] for i in items), "divine fc7", round(div_fc[-1], 1))
