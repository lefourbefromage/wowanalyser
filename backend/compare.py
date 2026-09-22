"""Diff structuré entre deux joueurs normalisés (A = premier lien, B = second)."""


def _diff(a, b):
    if a is None or b is None:
        return None
    return round(b - a, 2)


def _rel(a, b):
    if not a:
        return None
    return round(100.0 * (b - a) / a, 1)


# (clé, libellé, "higher"/"lower" = sens du mieux, unité)
HEADLINE = [
    ("dps", "DPS (familiers inclus)", "higher", ""),
    ("dps_player_only", "DPS hors familiers", "higher", ""),
    ("active_time_pct", "Temps actif (WCL)", "higher", "%"),
    ("casts_per_min_engaged", "Casts / min en combat", "higher", ""),
    ("gaps.total_s", "Temps mort total", "lower", "s"),
    ("gaps.pct_of_engaged", "Temps mort (% du combat)", "lower", "%"),
    ("gaps.per_10min_engaged", "Trous / 10 min", "lower", ""),
    ("deaths", "Morts", "lower", ""),
    ("dead_time_s", "Temps passé mort", "lower", "s"),
    ("item_level", "Niveau d'objet", "higher", ""),
    ("engaged_s", "Temps en combat", None, "s"),
    ("duration_s", "Durée de la clé", None, "s"),
]


def _get(d, path):
    for part in path.split("."):
        if d is None:
            return None
        d = d.get(part)
    return d


def _by_name(rows, key="name"):
    return {r[key]: r for r in rows}


def build_diff(a: dict, b: dict, meta_a: dict, meta_b: dict) -> dict:
    headline = []
    for key, label, better, unit in HEADLINE:
        va, vb = _get(a, key), _get(b, key)
        winner = None
        if better and va is not None and vb is not None and va != vb:
            winner = "A" if (va > vb) == (better == "higher") else "B"
        headline.append({
            "key": key, "label": label, "unit": unit, "a": va, "b": vb,
            "diff": _diff(va, vb), "rel_pct": _rel(va, vb), "better": winner,
        })

    abilities = []
    for name in set(a["abilities"]) | set(b["abilities"]):
        ra, rb = a["abilities"].get(name), b["abilities"].get(name)
        ca, cb = (ra or {}).get("count", 0), (rb or {}).get("count", 0)
        if ca + cb < 3:
            continue
        pa, pb = (ra or {}).get("per_min", 0), (rb or {}).get("per_min", 0)
        abilities.append({
            "name": name, "a_count": ca, "b_count": cb, "a_per_min": pa, "b_per_min": pb,
            "diff_per_min": round(pb - pa, 2), "rel_pct": _rel(pa, pb),
        })
    abilities.sort(key=lambda r: -abs(r["diff_per_min"]))

    da, db = _by_name(a["debuffs"]), _by_name(b["debuffs"])
    debuffs = []
    for name in sorted(set(da) | set(db)):
        xa, xb = da.get(name) or {}, db.get(name) or {}
        ua, ub = xa.get("uptime_on_affected_targets_pct"), xb.get("uptime_on_affected_targets_pct")
        debuffs.append({
            "name": name,
            "a_uptime": ua, "b_uptime": ub, "diff": _diff(ua or 0, ub or 0),
            "a_coverage": xa.get("coverage_all_targets_pct"), "b_coverage": xb.get("coverage_all_targets_pct"),
            "a_applications": xa.get("applications", 0), "b_applications": xb.get("applications", 0),
        })
    debuffs.sort(key=lambda r: -abs(r["diff"] or 0))

    ba, bb = _by_name(a["buffs"]), _by_name(b["buffs"])
    buffs = []
    for name in set(ba) | set(bb):
        ua, ub = (ba.get(name) or {}).get("uptime_pct", 0), (bb.get(name) or {}).get("uptime_pct", 0)
        buffs.append({"name": name, "a_uptime": ua, "b_uptime": ub, "diff": round(ub - ua, 1)})
    buffs.sort(key=lambda r: -abs(r["diff"]))

    wa, wb = _by_name(a["resources"], "resource"), _by_name(b["resources"], "resource")
    resources = []
    for res in sorted(set(wa) | set(wb)):
        xa, xb = wa.get(res) or {}, wb.get(res) or {}
        resources.append({
            "resource": res,
            "a_wasted": xa.get("wasted", 0), "b_wasted": xb.get("wasted", 0),
            "a_waste_pct": xa.get("waste_pct", 0), "b_waste_pct": xb.get("waste_pct", 0),
            "a_wasted_per_min": xa.get("wasted_per_min", 0), "b_wasted_per_min": xb.get("wasted_per_min", 0),
            "a_waste_events": xa.get("waste_events", 0), "b_waste_events": xb.get("waste_events", 0),
            "a_casts_at_cap_pct": xa.get("casts_at_cap_pct"), "b_casts_at_cap_pct": xb.get("casts_at_cap_pct"),
        })

    dmg_a = {(r["name"], r["pet"]): r["share_pct"] for r in a["damage_breakdown"]}
    dmg_b = {(r["name"], r["pet"]): r["share_pct"] for r in b["damage_breakdown"]}
    damage = [
        {"name": n + (f" ({p})" if p else ""), "a_share": dmg_a.get((n, p), 0), "b_share": dmg_b.get((n, p), 0),
         "diff": round(dmg_b.get((n, p), 0) - dmg_a.get((n, p), 0), 1)}
        for n, p in set(dmg_a) | set(dmg_b)
    ]
    damage.sort(key=lambda r: -max(r["a_share"], r["b_share"]))

    bosses_a, bosses_b = _by_name(a["boss_fights"], "boss"), _by_name(b["boss_fights"], "boss")
    bosses = [
        {"boss": n, "a": bosses_a.get(n), "b": bosses_b.get(n)}
        for n in sorted(set(bosses_a) | set(bosses_b))
    ]

    dps_a, dps_b = a["dps"], b["dps"]
    weaker = "A" if dps_a < dps_b else "B"
    same_dungeon = meta_a["fight_name"] == meta_b["fight_name"]

    return {
        "context": {
            "spec": meta_a["spec_label"],
            "A": meta_a, "B": meta_b,
            "same_dungeon": same_dungeon,
            "weaker_player": weaker,
            "dps_gap_pct": round(100 * abs(dps_a - dps_b) / max(dps_a, dps_b, 1), 1),
            "gap_threshold_s": a["gaps"]["threshold_s"],
        },
        "headline": headline,
        "abilities": abilities,
        "debuffs": debuffs,
        "buffs": buffs[:20],
        "resources": resources,
        "waste_sources": {"A": a["waste_sources"], "B": b["waste_sources"]},
        "damage_breakdown": damage[:18],
        "gaps": {"A": a["gaps"], "B": b["gaps"]},
        "bosses": bosses,
        "first_pull_opener": {"A": a["first_pull_opener"], "B": b["first_pull_opener"]},
        "top_sequences": {"A": a["top_sequences"], "B": b["top_sequences"]},
    }
