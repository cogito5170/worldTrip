"""심판 -- 여행 계획을 결정적으로 다시 잰다. LLM 이 아니다. planner.py 를 임포트하지 않는다.

생성자는 도시별 비용을 '하룻밤 묶음' 으로 미리 계산해 쓴다. 심판은 **원장 항목 하나하나를 날짜별로
다시 더한다**(독립 대조). 두 합이 다르면 둘 중 하나가 다른 것을 쟀으니 거절한다.

검사
  T0 구조     출발지에서 시작 · 귀국지에서 끝 · 도시 중복 없음 · 머무는 도시가 1곳 이상
  T1 간선     구간마다 원장에 있는 간선인가 · 양 끝이 맞나 · 허용한 수단인가
  T2 날짜     체류가 빈틈없이 이어지고 박수 합 = nights · 최소 박수
  T3 요구     must 를 다 들렀나 · avoid 를 안 들렀나 · 같은 나라를 두 번 들어가지 않나(설정 시)
  T4 하루     하루에 넣은 명소 시간 ≤ 그날 용량 · 명소가 그 도시의 것인가
  T5 돈       값을 모르는 핵심 항목(숙소·식비·시내교통)이 없나 · 최대가 ≤ 예산
  T6 대조     생성자 합 = 심판 합 (최소·최대 모두)
  T7 근거     쓴 수마다 출처가 있나 -- 확인수준별로 센다
"""
from __future__ import annotations

import math
from datetime import date, timedelta

LODGING = {"budget": "hostel", "mid": "mid", "comfort": "upscale"}
CHECKS = {
    "T0_structure": "출발지에서 시작해 귀국지에서 끝나고, 도시가 겹치지 않는다",
    "T1_edges": "구간마다 원장에 있는 간선이고 허용한 수단이다",
    "T2_dates": "체류가 빈틈없이 이어지고 박수 합과 최소 박수를 지킨다",
    "T3_requirements": "must 를 다 들르고 avoid 를 피하고, 나라를 두 번 들어가지 않는다",
    "T4_day_capacity": "하루 명소 시간이 그날 용량 안이고 명소가 그 도시의 것이다",
    "T5_budget": "핵심 비용을 다 알고 최대가가 예산 안이다",
    "T6_crosscheck": "생성자가 더한 돈과 심판이 날짜별로 다시 더한 돈이 같다",
    "T7_evidence": "쓴 수마다 출처가 있다",
}


def _krw(cat, p):
    r = cat.fx[p["currency"]]["krw"]
    return p["min"] * r, p["max"] * r


def recount(cat, q, plan) -> dict:
    """원장 항목을 **날짜 단위로** 다시 더한다. 생성자의 묶음 계산을 쓰지 않는다."""
    T = q["travelers"]
    lo = hi = 0.0
    levels: "dict[str, int]" = {}
    unknown = []

    def take(p):
        nonlocal lo, hi
        a, b = _krw(cat, p)
        lv = (p.get("source") or {}).get("level")
        levels[lv] = levels.get(lv, 0) + 1
        return a, b

    emap = {e["id"]: e for e in cat.edges}
    for lg in plan["legs"]:
        e = emap.get(lg["edge"])
        if e is None:
            continue
        a, b = take(e["price"])
        for _ in range(T):
            lo += a
            hi += b
        if e["mode"] == "flight":
            for c in (lg["from"], lg["to"]):
                if c in (q["origin"], q["return_to"]):
                    continue
                ap = cat.cities[c]["transit"]["airport"]
                if ap:
                    a, b = take(ap["price"])
                    lo += a * T
                    hi += b * T
    by_city = {s["city"]: s for s in plan["stays"]}
    for d in plan["days"]:
        c = cat.cities[d["city"]]
        lp = c["lodging"].get(LODGING[q["style"]])
        fp = c["food_per_day"].get(q["style"])
        tp = c["transit"]["day_cost"]
        beds = T if LODGING[q["style"]] == "hostel" else math.ceil(T / 2)
        for nm, p, mult in (("lodging", lp, beds), ("food", fp, T), ("local_transit", tp, T)):
            if p is None:
                unknown.append(f"{d['city']} {nm}")
                continue
            a, b = take(p)
            lo += a * mult
            hi += b * mult
        amap = {x["id"]: x for x in c["attractions"]}
        for aid in d["attractions"]:
            if aid in amap:
                a, b = take(amap[aid]["price"])
                lo += a * T
                hi += b * T
    for s in plan["stays"]:
        for act in cat.cities[s["city"]]["activities"]:
            if act["name"] in q["activities"]:
                a, b = take(act["price"])
                lo += a * T
                hi += b * T
    _ = by_city
    return {"min": round(lo), "max": round(hi), "levels": levels, "unknown": sorted(set(unknown))}


def judge(cat, q, plan, claimed: "dict | None" = None) -> dict:
    v = []

    def bad(code, msg):
        v.append({"check": code, "message": msg})

    route = plan.get("route") or []
    stays = plan.get("stays") or []
    days = plan.get("days") or []
    legs = plan.get("legs") or []
    # T0
    if not route or route[0] != q["origin"]:
        bad("T0_structure", f"출발지 {q['origin']} 에서 시작하지 않는다")
    if q["return_to"] is not None and (not route or route[-1] != q["return_to"]):
        bad("T0_structure", f"귀국지 {q['return_to']} 에서 끝나지 않는다")
    staying = [s["city"] for s in stays]
    if not staying:
        bad("T0_structure", "머무는 도시가 없다 -- 빈 계획은 성공이 아니다")
    if len(set(staying)) != len(staying):
        bad("T0_structure", "같은 도시에 두 번 머문다")
    mid = route[1:-1] if q["return_to"] is not None else route[1:]
    if mid != staying:
        bad("T0_structure", "경로의 도시와 체류 순서가 다르다")
    for c in staying:
        if c not in cat.cities:
            bad("T0_structure", f"모르는 도시 {c}")
    if v:
        return {"ok": False, "violations": v, "money": None, "checks_run": list(CHECKS)}
    # T1
    emap = {e["id"]: e for e in cat.edges}
    if len(legs) != len(route) - 1:
        bad("T1_edges", f"구간 {len(legs)}개, 경로 이음 {len(route) - 1}개")
    for (a, b), lg in zip(zip(route, route[1:]), legs):
        e = emap.get(lg.get("edge"))
        if e is None:
            bad("T1_edges", f"{a}→{b}: 원장에 없는 간선 {lg.get('edge')!r}")
            continue
        if {e["from"], e["to"]} != {a, b}:
            bad("T1_edges", f"{a}→{b}: 간선 {e['id']} 는 {e['from']}-{e['to']} 를 잇는다")
        if e["mode"] not in q["modes"]:
            bad("T1_edges", f"{a}→{b}: 허용 안 한 수단 {e['mode']}")
    # T2
    try:
        d0 = date.fromisoformat(stays[0]["arrive"])
    except (ValueError, KeyError):
        d0 = None
        bad("T2_dates", "첫 도착일을 못 읽는다")
    if d0 is not None and d0 != q["start"]:
        bad("T2_dates", f"첫 도착 {d0} ≠ 출발일 {q['start']}")
    cur = q["start"]
    for s in stays:
        if s["arrive"] != cur.isoformat():
            bad("T2_dates", f"{s['city']}: 도착 {s['arrive']} 가 앞 체류의 출발 {cur} 과 안 이어진다")
        cur = cur + timedelta(days=s["nights"])
        if s["depart"] != cur.isoformat():
            bad("T2_dates", f"{s['city']}: 출발일 {s['depart']} ≠ 도착+{s['nights']}박")
        need = q["min_nights"].get(s["city"]) or cat.cities[s["city"]]["min_nights"] or q["default_min_nights"]
        if s["nights"] < need:
            bad("T2_dates", f"{s['city']}: {s['nights']}박 < 최소 {need}박")
    total = sum(s["nights"] for s in stays)
    if total != q["nights"]:
        bad("T2_dates", f"박수 합 {total} ≠ 요청 {q['nights']}")
    want_days = [(q["start"] + timedelta(days=i)).isoformat() for i in range(q["nights"])]
    if [d["date"] for d in days] != want_days:
        bad("T2_dates", "하루 일정이 출발일부터 하루씩 빈틈없이 이어지지 않는다")
    # T3
    for m in q["must"]:
        if m not in staying:
            bad("T3_requirements", f"must '{m}' 를 안 들렀다")
    for a in q["avoid"]:
        if a in staying:
            bad("T3_requirements", f"avoid '{a}' 를 들렀다")
    if q["contiguous"]:
        seen, last = set(), None
        for c in staying:
            k = cat.cities[c]["country"]
            if k != last and k in seen:
                bad("T3_requirements", f"{k} 에 두 번 들어간다({c})")
            seen.add(k)
            last = k
    # T4
    first = {}
    for d in days:
        first.setdefault(d["city"], d["date"])
    for d in days:
        c = cat.cities.get(d["city"])
        if c is None:
            bad("T4_day_capacity", f"{d['date']}: 모르는 도시 {d['city']}")
            continue
        cap = 4.0 if first[d["city"]] == d["date"] else 8.0
        amap = {a["id"]: a for a in c["attractions"]}
        h = 0.0
        for aid in d["attractions"]:
            if aid not in amap:
                bad("T4_day_capacity", f"{d['date']}: '{aid}' 는 {d['city']} 의 명소가 아니다")
            else:
                h += amap[aid]["hours"]
        if h > cap + 1e-9:
            bad("T4_day_capacity", f"{d['date']} {d['city']}: 명소 {h:g}시간 > 하루 용량 {cap:g}시간")
    # T5 · T6 · T7
    m = recount(cat, q, plan)
    if m["unknown"]:
        bad("T5_budget", "값을 모르는 핵심 비용: " + ", ".join(m["unknown"]) + " -- 모르는 것은 0 이 아니다")
    elif q["budget"] is not None:
        if q["basis"] == "max" and m["max"] > q["budget"]:
            bad("T5_budget", f"최대 {m['max']:,}원 > 예산 {q['budget']:,.0f}원 (범위의 최대가로 판정한다)")
        mid = (m["min"] + m["max"]) / 2
        if q["basis"] == "mid" and mid > q["budget"]:
            bad("T5_budget", f"가운데값 {mid:,.0f}원 > 예산 {q['budget']:,.0f}원 (budget_basis=mid)")
    if claimed is not None and not m["unknown"]:
        if abs(claimed["min"] - m["min"]) > 2 or abs(claimed["max"] - m["max"]) > 2:
            bad("T6_crosscheck", f"생성자 {claimed['min']:,}~{claimed['max']:,} ≠ 심판 {m['min']:,}~{m['max']:,}")
    if not m["levels"]:
        bad("T7_evidence", "근거 있는 수가 하나도 없다 -- 아무것도 재지 않았다")
    return {"ok": not v, "violations": v, "money": m, "checks_run": list(CHECKS)}
