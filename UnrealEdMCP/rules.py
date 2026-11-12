"""
iCTF design rules: community rules from experienced players plus numeric targets from the
community-liked pool maps (research/reference_ranges.json, built from FragMath preference data
and geometry measured on the competitive map versions).
"""
from __future__ import annotations

import json
from pathlib import Path

REF = json.loads((Path(__file__).resolve().parent / "research" / "reference_ranges.json").read_text())["metrics"]


def _rng(key):
    r = REF[key]
    return r["liked_min"], r["liked_max"], r["liked_median"]


def evaluate(m: dict) -> list[dict]:
    """Return checks: {check, status PASS/WARN/FAIL, value, target, why}."""
    out = []

    def add(check, ok, value, target, why, fail=False):
        out.append({"check": check, "status": "PASS" if ok else ("FAIL" if fail else "WARN"),
                    "value": value, "target": target, "why": why})

    if m.get("error"):
        return [{"check": "metrics", "status": "FAIL", "value": m["error"], "target": "", "why": ""}]
    add("flags connected", bool(m.get("flag_reachable")), m.get("flag_reachable"), True,
        "a carrier must be able to walk/kick/lift from one flag to the other WITHOUT crossing lava, slime or deep water "
        "(hazards are punishers, never part of a route)", fail=True)
    unreach = [d["id"] for d in m.get("spawn_details", []) if d.get("unreachable")]
    add("every spawn reaches both flags", not unreach, f"{len(unreach)} unreachable {unreach[:8]}", 0,
        "spawns cut off by hazards / missing links are useless", fail=bool(unreach))
    # --- spawns (community rule)
    det = m.get("spawn_details", [])
    near = [d["id"] for d in det if d.get("to_own_flag", 1e9) < 1024]
    add("no spawns in the flag room", not near, f"{len(near)} spawn(s) within 1024uu walk of own flag {near}",
        "0 (liked maps: closest spawn 1170-1970uu from own flag)",
        "flag-room spawns let defenders respawn on the flag -> 0-0 stalemates and overtime", fail=True)
    los_close = [d["id"] for d in det if d.get("sees_own_flag") and d.get("to_own_flag", 1e9) < 1500]
    add("no spawns with line of sight to own flag", not los_close, f"{len(los_close)} {los_close}", 0,
        "same stalemate problem: instant defence of the flag from spawn")
    # --- spawn orientation (community rule; pool: research/spawn_orientation.json)
    face = [d for d in det if "view_ahead" in d]
    if face:
        wall = [d["id"] for d in face if d["view_ahead"] < 256]
        add("spawns don't face a wall", not wall, f"{len(wall)} spawn(s) look at a wall < 256uu away {wall}",
            "0 (liked maps: none; pool p10 446uu, median 752uu straight ahead)",
            "spawning nose-to-wall wastes the first second of life; being NEXT to a wall is fine - only the view "
            "straight ahead counts (fix: orient_spawns)", fail=True)
        back = [d["id"] for d in face if d.get("attack_progress") is not None and d["attack_progress"] <= -0.3]
        add("spawns don't look back toward their own base", not back, f"{len(back)} {back}",
            "0 (pool: 3% of spawns)", "spawns should look toward the enemy base or, if not possible, perpendicular - "
            "never back toward their own flag (fix: orient_spawns)")
        att = sum(1 for d in face if (d.get("attack_progress") or -9) >= 0.3)
        share = att / len(face)
        add("spawns look along an attacking path", share >= 0.5, f"{att}/{len(face)} ({share:.0%})",
            ">= 50% (pool 73%; liked maps 29-100%)",
            "looking down a path that leads toward the enemy (any attacking route, also odd angles) gets players "
            "moving; measured as walk-distance gained toward the enemy flag along the view (fix: orient_spawns)")
        seen = [d["id"] for d in face if d.get("sees_enemy_spawns")]
        add("no line of sight to enemy spawns", not seen, f"{len(seen)} spawn(s) see enemy spawns {seen}",
            "0 (pool: 6.5% of spawns; Grudge, Overflow, Nuance99 4 each)",
            "spawning in sight of enemy spawns means instant spawn kills both ways - move or screen them", fail=True)
    # --- CTF-Tenement review
    gaps = m.get("flag_floor_gap") or []
    bad_g = [g for g in gaps if g is not None and not (48 <= g <= 96)]
    if gaps:
        add("flags stand on the floor", not bad_g, f"FlagBase {gaps} uu above the floor",
            "48-96 (pool median 65: a FlagBase stands on the floor at floor + 64)",
            "a FlagBase placed lower sinks into the ground, higher floats (fix: add_flag uses floor_z + 64)",
            fail=any(g is not None and g < 48 for g in bad_g))
    ke = m.get("knife_edges")
    if ke is not None:
        add("no knife edges between carves", ke == 0,
            f"{ke} {[(s['at'], s['angle_deg'], s['brushes']) for s in m.get('knife_edge_spots', [])[:4]]}",
            "0 (15 of 18 pool maps have none)",
            "a door or corridor cut into a chamfered / rounded corner leaves a wall wedge that tapers to an edge - "
            "looks broken and snags players. Keep doors on straight wall spans, cut them square to the wall, or extend "
            "the carve over the whole corner")
    oe = m.get("one_end_platforms")
    if oe is not None:
        add("raised walkways climbable from both ends", oe == 0,
            f"{oe} {[(p['id'], p['group'], p['worst_at'], p['worst_walk_up']) for p in m.get('one_end_platform_list', [])[:4]]}",
            "0 (community rule; pool: Rune, Duku, PryXon 0, Nuance99 9)",
            "a raised path with stairs / a ramp at one end only forces everyone coming from the other side into a "
            "detour - add access at both ends (or every ~768uu along a long side), or a kicker")
    p90 = m.get("sightline_p90")
    if p90 is not None:
        add("prefer shorter sightlines", p90 <= 1216, f"p90 {p90}uu, median {m.get('sightline_median')}uu",
            "p90 <= ~1200uu (liked maps' median; range 1056-1712)",
            "community preference: shorter sightlines - break long corridors and halls with turns, offsets, pillars "
            "or height changes every ~1000-1500uu")
    tot = m.get("spawns", 0)
    per = m.get("spawns_per_team", [0, 0])
    add("enough spawn points", min(per) >= 8, f"{tot} total, per team {per}", ">= 8 per team (liked maps 12-16 total; more is better)",
        "more spawns spread out = carriers don't get 'spawned on'; strongest geometric predictor of preference")
    fw = m.get("spawn_forwardness")
    if fw is not None:
        add("spawns placed forward", 0.30 <= fw <= 0.42, fw, "0.30-0.40 (0 = at own flag, 0.5 = midfield)",
            "forward spawns push players to attack/grab instead of falling back")
    # --- geometry (community rules + liked ranges)
    lo, hi, med = _rng("corridor_width_median")
    v = m.get("corridor_width_median")
    add("open, not corridor-like", v is not None and v >= 450, v, f">= 450uu typical local width (liked median {med:.0f})",
        "players prefer open spaces to narrow zig-zag corridors")
    v = m.get("narrow_spots_pct")
    add("few narrow spots", v is not None and v <= 35, f"{v}%", "<= 35% of spots narrower than 384uu (liked median 31%, disliked maps 43-53%)",
        "narrow, zig-zag paths are disliked")
    lo, hi, med = _rng("walk_area_kuu2")
    v = m.get("walk_area_kuu2")
    add("map size", v is not None and 13000 <= v <= 24000, f"{v} k-uu2", "13,000-24,000 k-uu2 walkable floor",
        "open but not too large for 5v5")
    v = m.get("flag_dist_straight")
    add("flag distance", v is not None and 4900 <= v <= 6600, v, "4,900-6,600uu straight (liked median 5,900)", "liked-map range")
    v = m.get("long_sightlines_pct")
    add("no cross-map sniping lines", v is not None and v <= 1.0 and m.get("sightline_p99", 0) <= 3100,
        f"{v}% of sightlines >= 3000uu, p99 {m.get('sightline_p99')}uu", "<= 1% and p99 <= 3100uu",
        "being sniped across the map is disliked; break long lines with cover/angles")
    v, med = m.get("headroom_over_640_pct"), m.get("headroom_median")
    add("rooms, not one big hall", v is not None and v <= 60 and (med or 0) <= 768,
        f"{v}% of floor has > 640uu headroom (median headroom {med}uu)",
        "<= 60% and median <= 768uu (pool max 57% / 768; a single open hall scores ~80% / 1024)",
        "a big open area with objects placed inside it is a no-go: carve a network of distinct rooms and passages",
        fail=v is not None and v > 70)
    v = m.get("dead_end_pct_near_flags")
    add("no hiding pockets near the flags", v is not None and v <= 12,
        f"{v}% of spots within 2560uu of a flag are dead ends {m.get('dead_end_examples', [])[:3]}",
        "<= 12% (liked maps 1-12%; houses/small rooms with one door push it up)",
        "enterable houses / one-door nooks near a base let the enemy flag carrier hide -> returns take forever",
        fail=v is not None and v > 18)
    v, dark = m.get("lights_per_Muu2"), m.get("dark_floor_pct")
    if v is not None:
        add("enough light", v >= 5.5,
            f"{v} lights per M uu2 of floor (estimated dark floor {dark}%, median light {m.get('light_median')})",
            ">= 5.5 lights per M uu2 of walkable floor, aim ~12-15 (pool 6-58, median ~15); spread them so no area is dark",
            "players must be visible in InstaGib; generated maps tend to be far too dark",
            fail=v < 4)
    if "ramp_issues" in m:
        iss = m["ramp_issues"]
        add("ramps: gentle on main routes, landings before walls", not iss, iss[:8] if iss else "ok",
            "main ramps <= 30 deg & <= 512 wide; steep only on narrow bonus routes; >= 256uu landing before walls",
            "steep main ramps and ramps that end in a wall break movement flow", fail=len(iss) >= 4)
    v = m.get("base_to_base_los_pairs")
    if v is not None:
        add("no shooting from base to base", v == 0,
            f"{v} red/blue spot pairs within 1536uu of the flags can see each other {m.get('base_to_base_los_examples', [])[:2]}",
            "0 - bases must never see each other",
            "being able to shoot across from base to base is a no-go; offset doorways / bend the central axis / "
            "add elevation changes so no straight line connects the bases", fail=v > 0)
    v = m.get("floor_levels")
    add("not too many stories", v is not None and v <= 3, v, "<= 3 floor levels (liked median 3)",
        "too many levels/stories are not preferred")
    v = m.get("level_change_walk_median")
    add("elevation quickly reachable", v is not None and v <= 600, f"{v}uu", "<= 600uu median walk to change level (liked median ~430)",
        "if elevation exists it must be accessible quickly from anywhere")
    ent = (m.get("flag_entrances") or {}).get("1024")
    if ent:
        add("flag room has many entrances", min(ent) >= 3, ent, ">= 3 distinct entrances per flag area (4+ better)",
            "few entrances make mass defence too easy for the leading team")
    return out


def summary(checks: list[dict]) -> str:
    f = sum(c["status"] == "FAIL" for c in checks)
    w = sum(c["status"] == "WARN" for c in checks)
    p = sum(c["status"] == "PASS" for c in checks)
    return f"{p} pass, {w} warn, {f} fail"
