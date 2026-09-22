"""Normalisation générique de la rotation d'un joueur à partir de ses events bruts WCL.

Fonctionne pour toutes les classes/spés : aucune liste de sorts codée en dur.
Tous les temps internes sont en millisecondes (timestamps du rapport).
"""
from bisect import bisect_right
from collections import Counter, defaultdict

COMBAT_BREAK_MS = 5000     # plus de 5 s sans dégâts = hors combat (entre deux packs)
OPENER_LENGTH = 12
MIN_DEBUFF_APPLICATIONS = 3
AUTO_ATTACK_GUIDS = {1, 75}  # Melee, Auto Shot : pas des sorts de la rotation

RESOURCE_NAMES = {
    0: "Mana", 1: "Rage", 2: "Focalisation", 3: "Énergie", 4: "Points de combo", 5: "Runes",
    6: "Puissance runique", 7: "Fragments d'âme", 8: "Puissance astrale", 9: "Puissance sacrée",
    11: "Maelström", 12: "Chi", 13: "Démence", 16: "Charges arcaniques", 17: "Fureur",
    18: "Douleur", 19: "Essence",
}


def _ability(ev) -> tuple:
    a = ev.get("ability") or {}
    return a.get("guid"), a.get("name") or "?"


def _target_key(ev) -> tuple:
    return ev.get("targetID"), ev.get("targetInstance", 0)


def _target_name(ev, names) -> str | None:
    t = ev.get("target")
    if isinstance(t, dict):
        return t.get("name")
    return (names.get(ev.get("targetID")) or {}).get("name")


def _hostile_target(ev) -> bool:
    return ev.get("targetIsFriendly") is False and ev.get("targetID") not in (None, -1)


def _segments(timestamps, brk):
    segs = []
    for t in sorted(timestamps):
        if segs and t - segs[-1][1] <= brk:
            segs[-1][1] = t
        else:
            segs.append([t, t])
    return segs


def _pct(num, den):
    return round(100.0 * num / den, 1) if den else 0.0


def analyze_player(events, fight, player_id, table_entry, names, bosses, gap_threshold_ms=1500) -> dict:
    fight_start, fight_end = fight["start"], fight["end"]
    duration = max(1, fight_end - fight_start)
    minutes = duration / 60000

    own = [e for e in events if e.get("sourceID") == player_id]
    deaths = sorted(e["timestamp"] for e in events if e.get("type") == "death" and e.get("targetID") == player_id)

    # --- passe 1 : durée de vie observée des cibles hostiles
    target_first, target_last = {}, {}
    for ev in own:
        if _hostile_target(ev):
            k = _target_key(ev)
            target_first.setdefault(k, ev["timestamp"])
            target_last[k] = ev["timestamp"]

    # --- passe 2 : collecte
    casts = []
    begin_by_guid = {}
    activity_ts = []
    damage_player = 0
    damage_by_ability = defaultdict(int)
    damage_boss = defaultdict(int)
    resources = defaultdict(lambda: {"gained": 0, "wasted": 0, "waste_events": 0})
    waste_by_ability = defaultdict(lambda: {"events": 0, "wasted": 0})
    cap_casts = defaultdict(lambda: [0, 0])   # type -> [casts ressource pleine, casts]
    debuff_open, debuff_total = {}, defaultdict(int)
    debuff_apps, debuff_refresh = Counter(), Counter()
    debuff_targets, debuff_names = defaultdict(set), {}
    buff_open, buff_total, buff_apps, buff_names = {}, defaultdict(int), Counter(), {}

    for ev in own:
        t, ts = ev.get("type"), ev["timestamp"]
        guid, name = _ability(ev)

        if t == "begincast":
            begin_by_guid[guid] = ts
        elif t == "cast":
            if guid in AUTO_ATTACK_GUIDS:
                continue
            begin = begin_by_guid.pop(guid, None)
            casts.append({"t": ts, "guid": guid, "name": name, "target": _target_name(ev, names), "begin": begin})
            if _hostile_target(ev):
                activity_ts.append(ts)
            for r in ev.get("classResources") or []:
                if r.get("max"):
                    c = cap_casts[r.get("type")]
                    c[1] += 1
                    c[0] += r.get("amount", 0) >= r["max"]
        elif t == "damage" and _hostile_target(ev):
            amount = ev.get("amount", 0) + ev.get("absorbed", 0)
            damage_player += amount
            damage_by_ability[name] += amount
            activity_ts.append(ts)
            if ev.get("targetID") in bosses:
                damage_boss[ev["targetID"]] += amount
        elif t == "resourcechange" and ev.get("targetID") == player_id:
            rtype = ev.get("resourceChangeType")
            waste = ev.get("waste", 0) or 0
            res = resources[rtype]
            res["gained"] += ev.get("resourceChange", 0) or 0
            res["wasted"] += waste
            if waste > 0:
                res["waste_events"] += 1
                w = waste_by_ability[(rtype, name)]
                w["events"] += 1
                w["wasted"] += waste
        elif t in ("applydebuff", "refreshdebuff", "removedebuff") and _hostile_target(ev):
            tk = _target_key(ev)
            key = (guid, tk)
            debuff_names[guid] = name
            debuff_targets[guid].add(tk)
            if t == "applydebuff":
                debuff_apps[guid] += 1
                debuff_open.setdefault(key, ts)
            elif t == "refreshdebuff":
                debuff_refresh[guid] += 1
                debuff_open.setdefault(key, ts)
            else:
                start = debuff_open.pop(key, target_first.get(tk, fight_start))
                debuff_total[guid] += max(0, ts - start)
        elif t in ("applybuff", "refreshbuff", "removebuff") and ev.get("targetID") == player_id:
            buff_names[guid] = name
            if t == "applybuff":
                buff_apps[guid] += 1
                buff_open.setdefault(guid, ts)
            elif t == "refreshbuff":
                buff_open.setdefault(guid, ts)
            else:
                start = buff_open.pop(guid, fight_start)
                buff_total[guid] += max(0, ts - start)

    for (guid, tk), start in debuff_open.items():
        debuff_total[guid] += max(0, max(target_last.get(tk, start), start) - start)
    for guid, start in buff_open.items():
        buff_total[guid] += max(0, fight_end - start)

    casts.sort(key=lambda c: c["t"])

    # --- temps en combat (segments d'activité hostile)
    segments = _segments(activity_ts, COMBAT_BREAK_MS)
    seg_starts = [s[0] for s in segments]
    engaged_ms = sum(e - s for s, e in segments) or 1
    engaged_min = engaged_ms / 60000

    def segment_of(ts):
        i = bisect_right(seg_starts, ts) - 1
        return i if i >= 0 and ts <= segments[i][1] else None

    def death_between(a, b):
        i = bisect_right(deaths, a)
        return i < len(deaths) and deaths[i] <= b

    # --- trous entre casts (temps mort)
    gaps = []
    prev = None
    for c in casts:
        start = c["begin"] if c["begin"] is not None and (prev is None or c["begin"] >= prev["t"]) else c["t"]
        if prev is not None:
            gap = start - prev["t"]
            if gap > gap_threshold_ms:
                seg = segment_of(prev["t"])
                if seg is not None and seg == segment_of(start) and not death_between(prev["t"], start):
                    gaps.append({"t": prev["t"] - fight_start, "ms": gap, "after": prev["name"], "before": c["name"]})
        prev = c

    gap_total = sum(g["ms"] for g in gaps)
    after_counter = defaultdict(lambda: {"count": 0, "ms": 0})
    for g in gaps:
        a = after_counter[g["after"]]
        a["count"] += 1
        a["ms"] += g["ms"]
    gaps_after = sorted(
        ({"spell": k, "count": v["count"], "total_s": round(v["ms"] / 1000, 1)} for k, v in after_counter.items()),
        key=lambda x: -x["total_s"],
    )[:8]
    longest_gaps = [
        {"at": _clock(g["t"]), "s": round(g["ms"] / 1000, 1), "after": g["after"], "before": g["before"]}
        for g in sorted(gaps, key=lambda g: -g["ms"])[:8]
    ]

    # --- temps passé mort
    dead_ms = 0
    cast_times = [c["t"] for c in casts]
    for d in deaths:
        i = bisect_right(cast_times, d)
        dead_ms += (cast_times[i] if i < len(cast_times) else fight_end) - d

    # --- fréquence d'utilisation
    counts, guid_of = Counter(), {}
    for c in casts:
        counts[c["name"]] += 1
        guid_of[c["name"]] = c["guid"]
    abilities = {
        n: {"guid": guid_of[n], "count": k, "per_min": round(k / engaged_min, 2)}
        for n, k in counts.most_common()
    }

    # --- séquences : ouvertures et enchaînements fréquents
    bigrams = Counter()
    for a, b in zip(casts, casts[1:]):
        if segment_of(a["t"]) is not None and segment_of(a["t"]) == segment_of(b["t"]):
            bigrams[(a["name"], b["name"])] += 1
    total_pairs = sum(bigrams.values()) or 1
    top_bigrams = [
        {"sequence": f"{a} → {b}", "per_100": round(100 * k / total_pairs, 1)}
        for (a, b), k in bigrams.most_common(15)
    ]

    first_pull = [c["name"] for c in casts if segments and c["t"] >= segments[0][0] - 3000][:OPENER_LENGTH]
    boss_fights = []
    for bid in bosses:
        times = [e["timestamp"] for e in own if e.get("targetID") == bid]
        if not times:
            continue
        b_start, b_end = min(times), max(times)
        b_dur = max(1, b_end - b_start)
        b_casts = [c for c in casts if b_start - 3000 <= c["t"] <= b_end]
        boss_fights.append({
            "boss": (names.get(bid) or {}).get("name", str(bid)),
            "duration": _clock(b_dur),
            "boss_dps_player_only": round(damage_boss[bid] / (b_dur / 1000)),
            "casts_per_min": round(len(b_casts) / (b_dur / 60000), 1),
            "opener": [c["name"] for c in b_casts[:OPENER_LENGTH]],
        })
    boss_fights.sort(key=lambda b: b["boss"])

    # --- uptime des debuffs posés par le joueur (regroupés par nom : un même sort peut avoir plusieurs ids)
    all_lifetime = sum(max(0, target_last[k] - target_first[k]) for k in target_first) or 1
    by_name = defaultdict(lambda: {"total": 0, "apps": 0, "refresh": 0, "targets": set()})
    for guid, total in debuff_total.items():
        agg = by_name[debuff_names[guid]]
        agg["total"] += total
        agg["apps"] += debuff_apps[guid]
        agg["refresh"] += debuff_refresh[guid]
        agg["targets"] |= debuff_targets[guid]
    debuffs = []
    for name, agg in by_name.items():
        if agg["apps"] < MIN_DEBUFF_APPLICATIONS:
            continue
        life = sum(max(0, target_last.get(k, 0) - target_first.get(k, 0)) for k in agg["targets"]) or 1
        debuffs.append({
            "name": name,
            "applications": agg["apps"], "refreshes": agg["refresh"],
            "targets": len(agg["targets"]),
            "uptime_on_affected_targets_pct": min(100.0, _pct(agg["total"], life)),
            "coverage_all_targets_pct": min(100.0, _pct(agg["total"], all_lifetime)),
        })
    debuffs.sort(key=lambda d: -d["applications"])

    buffs = sorted(
        (
            {"name": buff_names[g], "applications": buff_apps[g], "uptime_pct": min(100.0, _pct(buff_total[g], duration))}
            for g in buff_total if buff_apps[g] >= 2
        ),
        key=lambda b: -b["applications"],
    )[:25]

    # --- ressources
    resource_rows = []
    for rtype, r in resources.items():
        if not r["gained"] and not r["wasted"]:
            continue
        cap = cap_casts.get(rtype)
        resource_rows.append({
            "resource": RESOURCE_NAMES.get(rtype, f"Ressource {rtype}"),
            "gained": r["gained"], "wasted": r["wasted"],
            "waste_pct": _pct(r["wasted"], r["gained"] + r["wasted"]),
            "waste_events": r["waste_events"],
            "wasted_per_min": round(r["wasted"] / engaged_min, 1),
            "casts_at_cap_pct": _pct(cap[0], cap[1]) if cap else None,
        })
    resource_rows.sort(key=lambda r: -r["wasted"])
    waste_sources = sorted(
        (
            {"resource": RESOURCE_NAMES.get(rt, f"Ressource {rt}"), "ability": ab, **v}
            for (rt, ab), v in waste_by_ability.items()
        ),
        key=lambda x: -x["wasted"],
    )[:10]

    # --- dégâts : le joueur depuis ses events, les familiers depuis la table WCL
    entry = table_entry or {}
    pet_rows = [(p.get("name"), p.get("total", 0)) for p in entry.get("pets") or [] if p.get("total")]
    pets_total = sum(t for _, t in pet_rows)
    total_damage = max(entry.get("total") or 0, damage_player + pets_total)
    grand = total_damage or 1
    rows = [(n, None, v) for n, v in damage_by_ability.items()] + [(n, "familier", v) for n, v in pet_rows]
    damage_breakdown = [
        {"name": n, "pet": p, "share_pct": _pct(v, grand)}
        for n, p, v in sorted(rows, key=lambda r: -r[2])[:15]
    ]

    return {
        "duration_s": round(duration / 1000),
        "engaged_s": round(engaged_ms / 1000),
        "item_level": entry.get("itemLevel"),
        "dps": round(total_damage / (duration / 1000)),
        "dps_player_only": round((total_damage - pets_total) / (duration / 1000)),
        "pet_damage_pct": _pct(pets_total, grand),
        "active_time_pct": _pct(entry["activeTime"], duration) if entry.get("activeTime") else None,
        "cast_count": len(casts),
        "casts_per_min_engaged": round(len(casts) / engaged_min, 1),
        "deaths": len(deaths),
        "dead_time_s": round(dead_ms / 1000),
        "gaps": {
            "threshold_s": gap_threshold_ms / 1000,
            "count": len(gaps),
            "total_s": round(gap_total / 1000, 1),
            "pct_of_engaged": _pct(gap_total, engaged_ms),
            "per_10min_engaged": round(len(gaps) / engaged_min * 10, 1),
            "after_spell": gaps_after,
            "longest": longest_gaps,
        },
        "abilities": abilities,
        "debuffs": debuffs[:15],
        "buffs": buffs,
        "resources": resource_rows,
        "waste_sources": waste_sources,
        "damage_breakdown": damage_breakdown,
        "first_pull_opener": first_pull,
        "boss_fights": boss_fights,
        "top_sequences": top_bigrams,
    }


def _clock(ms: int) -> str:
    s = max(0, int(ms)) // 1000
    return f"{s // 60}:{s % 60:02d}"
