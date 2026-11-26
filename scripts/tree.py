"""Compact passive tree for the Builds tab, from GGG's official export (github.com/grindinggear/skilltree-export).

    python scripts/tree.py [data.json] [site/tree/tree.json]

Writes {v, orbitRadii, groups:{id:[x,y]}, nodes:{id:[x,y,group,orbit,angle,kind,asc,name,stats]}, edges:[[a,b],...],
classes:[{name, ascendancies:[...]}]}. kind: 0 small, 1 notable, 2 keystone, 3 mastery, 4 jewel socket, 5 class start,
6 ascendancy start. asc is the ascendancy name ('' for the main tree). Re-run when a new patch changes the tree.
"""
import json, math, os, sys, urllib.request

SRC = sys.argv[1] if len(sys.argv) > 1 else None
OUT = sys.argv[2] if len(sys.argv) > 2 else "site/tree/tree.json"
URL = "https://raw.githubusercontent.com/grindinggear/skilltree-export/master/data.json"

# GGG's orbits with 16 and 40 slots are not evenly spaced (same table Path of Building uses)
ANGLES = {16: [0, 30, 45, 60, 90, 120, 135, 150, 180, 210, 225, 240, 270, 300, 315, 330],
          40: [0, 10, 20, 30, 40, 45, 50, 60, 70, 80, 90, 100, 110, 120, 130, 135, 140, 150, 160, 170, 180, 190, 200,
               210, 220, 225, 230, 240, 250, 260, 270, 280, 290, 300, 310, 315, 320, 330, 340, 350]}


def main():
    d = json.load(open(SRC, encoding="utf-8")) if SRC else json.load(urllib.request.urlopen(URL, timeout=120))
    C = d["constants"]
    per, radii = C["skillsPerOrbit"], C["orbitRadii"]
    groups = d["groups"]
    nodes, keep = {}, set()
    for k, n in d["nodes"].items():
        if k == "root" or n.get("isProxy") or "group" not in n:
            continue
        g = groups[str(n["group"])]
        o, i = n.get("orbit", 0), n.get("orbitIndex", 0)
        cnt = per[o]
        a = ANGLES[cnt][i] if cnt in ANGLES else 360 * i / cnt
        r = radii[o]
        x = g["x"] + r * math.sin(math.radians(a))
        y = g["y"] - r * math.cos(math.radians(a))
        kind = (5 if "classStartIndex" in n else 6 if n.get("isAscendancyStart") else 3 if n.get("isMastery") else
                4 if n.get("isJewelSocket") else 2 if n.get("isKeystone") else 1 if n.get("isNotable") else 0)
        nodes[k] = [round(x), round(y), n["group"], o, round(a, 1), kind, n.get("ascendancyName", ""), n.get("name", ""), n.get("stats", [])]
        if kind == 5:
            nodes[k].append(n["classStartIndex"])
        if kind == 3 and n.get("masteryEffects"):
            nodes[k].append({str(e["effect"]): e.get("stats", []) for e in n["masteryEffects"]})
        keep.add(k)
    edges, seen = [], set()
    for k, n in d["nodes"].items():
        if k not in keep:
            continue
        for o in n.get("out", []):
            if o not in keep or nodes[k][5] == 3 or nodes[o][5] == 3:
                continue
            if nodes[k][6] != nodes[o][6]:          # main tree <-> ascendancy
                continue
            if nodes[k][5] == 5 and nodes[o][6]:     # class start -> ascendancy
                continue
            key = tuple(sorted((k, o)))
            if key not in seen:
                seen.add(key)
                edges.append([int(key[0]), int(key[1])])
    used = {nodes[k][2] for k in nodes}
    out = {"v": d.get("tree", ""), "orbitRadii": radii,
           "groups": {g: [round(groups[g]["x"]), round(groups[g]["y"])] for g in groups if int(g) in used},
           "nodes": nodes, "edges": edges,
           "classes": [{"name": c["name"], "ascendancies": [a["name"] for a in c.get("ascendancies", [])]} for c in d["classes"]],
           "alternate": [a.get("name") or a.get("id") for a in (d.get("alternate_ascendancies") or [])],
           "jewelSlots": d.get("jewelSlots", [])}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"{len(nodes)} nodes, {len(edges)} edges, {os.path.getsize(OUT)//1024} KB -> {OUT}")


if __name__ == "__main__":
    main()
