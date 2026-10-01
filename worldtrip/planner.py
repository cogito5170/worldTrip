"""생성자 -- 여행 계획 후보를 **만든다.** 판정은 judge.py 가 따로 한다(서로 임포트하지 않는다).

최적화
  · 트리: 나라 노드의 집계 하한(1박+식비 최솟값)으로 예산 밖 조합을 미리 자른다.
          '같은 나라는 한 번에 몰아서'(country_contiguous) 를 순서 제약으로 건다
  · 그래프: 고른 도시들 사이를 **직접 연결된 간선만으로** 잇는 순서를 Held-Karp(부분집합 DP)로 전수
          -- 출발지에서 시작, (있으면) 귀국지에서 끝. 가중치 = 최대가(KRW) + 시간가치 × 시간
  · 조합: must + 후보의 부분집합을 max_cities 까지 **전수**
  · 밤 배분: 최소 박수를 깔고 남은 밤을 '하루 더 머물면 보게 되는 명소 점수' 가 큰 도시에 하나씩(탐욕)

정직한 한계(보고서에 그대로 나간다)
  · 조합과 순서는 전수지만 밤 배분은 탐욕이다 -- 배분까지 최적이라고 말하지 않는다
  · 경유 연결(파리→서울→로마)은 만들지 않는다. 데이터에 직접 연결이 없으면 그 순서는 없다
"""
from __future__ import annotations

import itertools
import math
from datetime import date, timedelta

from .catalog import LODGING_FOR, MODES, TAGS, TIERS, Catalog

ARRIVAL_DAY_HOURS = 4.0
FULL_DAY_HOURS = 8.0
MAX_CANDIDATES = 12


class RequestError(ValueError):
    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = list(errors)


def parse(raw: dict, cat: Catalog) -> dict:
    """요청 검사. 애매하면 추측하지 않고 거절한다."""
    e = []
    if not isinstance(raw, dict):
        raise RequestError(["요청은 JSON 객체여야 한다"])
    g = raw.get
    origin = g("origin", "seoul")
    if origin not in cat.cities:
        e.append(f"origin: 모르는 도시 {origin!r}")
    ret = g("return_to", origin)
    if ret is not None and ret not in cat.cities:
        e.append(f"return_to: 모르는 도시 {ret!r} (편도면 null)")
    try:
        start = date.fromisoformat(str(g("start_date")))
    except ValueError:
        start = None
        e.append("start_date: YYYY-MM-DD 가 필요하다")
    nights = g("nights")
    if isinstance(nights, bool) or not isinstance(nights, int) or not 1 <= nights <= 60:
        e.append("nights: 1..60 정수")
        nights = 1
    trav = g("travelers", 1)
    if isinstance(trav, bool) or not isinstance(trav, int) or not 1 <= trav <= 10:
        e.append("travelers: 1..10 정수")
        trav = 1
    budget = g("budget_krw")
    if budget is not None and (isinstance(budget, bool) or not isinstance(budget, (int, float)) or budget <= 0):
        e.append("budget_krw: 양수 또는 null")
    style = g("style", "mid")
    if style not in TIERS:
        e.append(f"style: {TIERS} 중 하나")
    interests = g("interests") or []
    if not isinstance(interests, list) or any(t not in TAGS for t in interests):
        e.append(f"interests: {list(TAGS)} 의 부분집합")
        interests = []
    must = g("must") or []
    consider = g("consider")
    avoid = set(g("avoid") or [])
    for nm, lst in (("must", must), ("consider", consider or []), ("avoid", list(avoid))):
        if not isinstance(lst, list):
            e.append(f"{nm}: 목록이어야 한다")
            continue
        for c in lst:
            if c not in cat.cities:
                e.append(f"{nm}: 모르는 도시 {c!r}")
            elif c == origin and nm != "avoid":
                e.append(f"{nm}: 출발지 {c!r} 는 머무는 도시로 넣지 않는다")
    if set(must) & avoid:
        e.append("must 와 avoid 가 겹친다")
    regions = g("regions")
    if regions is not None and (not isinstance(regions, list) or not regions):
        e.append("regions: 비지 않은 목록 또는 null")
    maxc = g("max_cities", 4)
    if isinstance(maxc, bool) or not isinstance(maxc, int) or not 1 <= maxc <= 8:
        e.append("max_cities: 1..8")
        maxc = 4
    if len(must) > maxc:
        e.append(f"must 가 {len(must)}곳인데 max_cities 가 {maxc} 다")
    dmin = g("default_min_nights", 2)
    if isinstance(dmin, bool) or not isinstance(dmin, int) or not 1 <= dmin <= 14:
        e.append("default_min_nights: 1..14")
        dmin = 2
    mn = g("min_nights") or {}
    if not isinstance(mn, dict) or any(k not in cat.cities or not isinstance(v, int) or v < 1 for k, v in mn.items()):
        e.append("min_nights: {도시: 1 이상 정수}")
        mn = {}
    modes = g("modes") or list(MODES)
    if not isinstance(modes, list) or any(m not in MODES for m in modes) or not modes:
        e.append(f"modes: {list(MODES)} 의 비지 않은 부분집합")
        modes = list(MODES)
    tv = g("time_value_krw_per_hour", 10000)
    if isinstance(tv, bool) or not isinstance(tv, (int, float)) or not 0 <= tv <= 1_000_000:
        e.append("time_value_krw_per_hour: 0..1,000,000")
        tv = 10000
    basis = g("budget_basis", "max")
    if basis not in ("max", "mid"):
        e.append("budget_basis: max(기본 · 범위의 최대가로 판정) | mid(범위의 가운데로 판정)")
        basis = "max"
    acts = g("activities") or []
    if not isinstance(acts, list) or not all(isinstance(a, str) for a in acts):
        e.append("activities: 활동 이름 목록")
        acts = []
    if e:
        raise RequestError(e)
    return {"origin": origin, "return_to": ret, "start": start, "nights": nights, "travelers": trav,
            "budget": float(budget) if budget is not None else None, "style": style, "interests": interests,
            "must": list(dict.fromkeys(must)), "consider": consider, "avoid": avoid, "regions": regions,
            "max_cities": maxc, "default_min_nights": dmin, "min_nights": mn, "modes": modes,
            "contiguous": bool(g("country_contiguous", True)), "basis": basis, "time_value": float(tv), "activities": acts}


# ------------------------------------------------------------------ 도시 비용·가치
def _rooms(style, trav):
    return trav if LODGING_FOR[style] == "hostel" else math.ceil(trav / 2)


def city_night_cost(cat: Catalog, cid: str, style: str, trav: int):
    """(min, max) KRW 하룻밤 + 하루(숙소·식비·시내교통). 모르면 None 과 빠진 칸."""
    c = cat.cities[cid]
    lp = c["lodging"].get(LODGING_FOR[style])
    fp = c["food_per_day"].get(style)
    tp = c["transit"]["day_cost"]
    missing = [n for n, v in (("lodging." + LODGING_FOR[style], lp), ("food." + style, fp), ("transit.day", tp)) if not v]
    if missing:
        return None, missing
    a, b = cat.krw(lp)
    f0, f1 = cat.krw(fp)
    t0, t1 = cat.krw(tp)
    r = _rooms(style, trav)
    return (a * r + (f0 + t0) * trav, b * r + (f1 + t1) * trav), []


def _score(item, interests):
    return 1 + 2 * len(set(item["tags"]) & set(interests))


def pick_attractions(cat, cid, hours, interests):
    """주어진 시간 안에 볼 명소 -- 점수 큰 것부터, 같으면 싼 것부터."""
    c = cat.cities[cid]
    ranked = sorted(c["attractions"], key=lambda a: (-_score(a, interests), cat.krw(a["price"])[1], a["id"]))
    out, used = [], 0.0
    for a in ranked:
        if used + a["hours"] <= hours + 1e-9:
            out.append(a)
            used += a["hours"]
    return out


def capacity(nights: int) -> float:
    return ARRIVAL_DAY_HOURS + FULL_DAY_HOURS * (nights - 1)


def city_value(cat, cid, nights, interests):
    return sum(_score(a, interests) for a in pick_attractions(cat, cid, capacity(nights), interests))


# ------------------------------------------------------------------ 그래프: 간선 고르기 · 순서
def best_edge(cat, a, b, q):
    ok = [e for e in cat.links(a, b) if e["mode"] in q["modes"]]
    if not ok:
        return None
    return min(ok, key=lambda e: (cat.krw(e["price"])[1] + q["time_value"] * (e["hours"] or 0), e["id"]))


def order(cat, cities, q):
    """출발지 -> cities 전부 -> (귀국지). 직접 간선만. 같은 나라는 연속. Held-Karp."""
    n = len(cities)
    o, ret = q["origin"], q["return_to"]
    W = {}

    def w(a, b):
        k = (a, b)
        if k not in W:
            e = best_edge(cat, a, b, q)
            W[k] = (None if e is None else cat.krw(e["price"])[1] + q["time_value"] * (e["hours"] or 0), e)
        return W[k]
    country = [cat.cities[c]["country"] for c in cities]
    INF = math.inf
    dp = {}
    for j in range(n):
        c, e = w(o, cities[j])
        if c is not None:
            dp[(1 << j, j)] = (c, None)
    for mask in range(1, 1 << n):
        for j in range(n):
            if (mask, j) not in dp:
                continue
            cost, _ = dp[(mask, j)]
            for k in range(n):
                if mask & (1 << k):
                    continue
                if q["contiguous"] and country[k] != country[j] and any(
                        mask & (1 << i) and country[i] == country[k] for i in range(n)):
                    continue      # 그 나라를 이미 떠났다 -- 다시 들어가지 않는다
                c, e = w(cities[j], cities[k])
                if c is None:
                    continue
                nm = mask | (1 << k)
                if cost + c < dp.get((nm, k), (INF,))[0]:
                    dp[(nm, k)] = (cost + c, j)
    full = (1 << n) - 1
    best, bj = INF, None
    for j in range(n):
        if (full, j) not in dp:
            continue
        cost = dp[(full, j)][0]
        if ret is not None:
            c, e = w(cities[j], ret)
            if c is None:
                continue
            cost += c
        if cost < best:
            best, bj = cost, j
    if bj is None:
        return None
    seq, mask, j = [], full, bj
    while j is not None:
        seq.append(cities[j])
        pj = dp[(mask, j)][1]
        mask &= ~(1 << j)
        j = pj
    seq.reverse()
    route = [o] + seq + ([ret] if ret is not None else [])
    legs = []
    for a, b in zip(route, route[1:]):
        e = w(a, b)[1]
        legs.append({"from": a, "to": b, "edge": e["id"], "mode": e["mode"]})
    return {"route": route, "legs": legs, "weight": best}


# ------------------------------------------------------------------ 밤 배분 · 일정
def allocate(cat, seq, q, cost_of):
    mins = {c: q["min_nights"].get(c) or cat.cities[c]["min_nights"] or q["default_min_nights"] for c in seq}
    left = q["nights"] - sum(mins.values())
    if left < 0:
        return None, mins
    n = dict(mins)
    while left > 0:
        best = None
        for c in seq:
            gain = city_value(cat, c, n[c] + 1, q["interests"]) - city_value(cat, c, n[c], q["interests"])
            nc = cost_of[c]
            key = (gain, -(nc[1] if nc else math.inf), -seq.index(c))
            if best is None or key > best[0]:
                best = (key, c)
        n[best[1]] += 1
        left -= 1
    return n, mins


def build_days(cat, seq, nights, q):
    days, stays = [], []
    d = q["start"]
    for c in seq:
        k = nights[c]
        picks = pick_attractions(cat, c, capacity(k), q["interests"])
        stays.append({"city": c, "nights": k, "arrive": d.isoformat(), "depart": (d + timedelta(days=k)).isoformat(),
                      "attractions": [a["id"] for a in picks]})
        queue = list(picks)
        for i in range(k):
            cap = ARRIVAL_DAY_HOURS if i == 0 else FULL_DAY_HOURS
            today, used = [], 0.0
            while queue and used + queue[0]["hours"] <= cap + 1e-9:
                a = queue.pop(0)
                today.append(a["id"])
                used += a["hours"]
            day = {"date": (d + timedelta(days=i)).isoformat(), "city": c, "capacity_h": cap,
                   "attractions": today, "hours": used}
            if not today and i > 0:
                # 빈 날 -- 원장에 더 넣을 명소가 없다. 활동을 '제안' 으로만 단다(예산에 안 들어간다)
                acts = [a["name"] for a in cat.cities[c]["activities"] if a["name"] not in q["activities"]]
                day["suggest"] = acts[(i - 1) % len(acts):(i - 1) % len(acts) + 1] if acts else []
                day["free"] = True
            days.append(day)
        d += timedelta(days=k)
    return stays, days


def money(cat, q, route_legs, stays):
    """(min, max) KRW 와 항목별 표, 모르는 것·뺀 것."""
    T = q["travelers"]
    lines, unknown, excluded = [], [], []
    raw = [0.0, 0.0]

    def add(cat_name, label, lo, hi, src):
        raw[0] += lo
        raw[1] += hi
        lines.append({"category": cat_name, "label": label, "min": round(lo), "max": round(hi), "level": src})

    edges = {e["id"]: e for e in cat.edges}
    for lg in route_legs:
        e = edges[lg["edge"]]
        lo, hi = cat.krw(e["price"])
        add("transport", f"{lg['from']}→{lg['to']} ({e['mode']}) ×{T}", lo * T, hi * T, e["price"]["source"]["level"])
        if e["mode"] == "flight":
            for c in (lg["from"], lg["to"]):
                if c in (q["origin"], q["return_to"]):
                    continue
                ap = cat.cities[c]["transit"]["airport"]
                if ap:
                    lo, hi = cat.krw(ap["price"])
                    add("airport", f"{c} 공항 이동 ×{T}", lo * T, hi * T, ap["price"]["source"]["level"])
                else:
                    if f"{c} 공항 이동(값을 모른다)" not in excluded:
                        excluded.append(f"{c} 공항 이동(값을 모른다)")
    for s in stays:
        c = cat.cities[s["city"]]
        k = s["nights"]
        lp = c["lodging"].get(LODGING_FOR[q["style"]])
        fp = c["food_per_day"].get(q["style"])
        tp = c["transit"]["day_cost"]
        r = _rooms(q["style"], T)
        for nm, p, mult, lab in (("lodging", lp, k * r, f"{s['city']} 숙소 {LODGING_FOR[q['style']]} {k}박×{r}"),
                                 ("food", fp, k * T, f"{s['city']} 식비 {q['style']} {k}일×{T}"),
                                 ("local_transit", tp, k * T, f"{s['city']} 시내교통 {k}일×{T}")):
            if p:
                lo, hi = cat.krw(p)
                add(nm, lab, lo * mult, hi * mult, p["source"]["level"])
            else:
                unknown.append(f"{s['city']} {nm}" + (f" ({q['style']})" if nm != "local_transit" else ""))
        amap = {a["id"]: a for a in c["attractions"]}
        for aid in s["attractions"]:
            a = amap[aid]
            lo, hi = cat.krw(a["price"])
            add("attractions", f"{a.get('name_ko') or a['name']} ×{T}", lo * T, hi * T, a["price"]["source"]["level"])
        for act in c["activities"]:
            if act["name"] in q["activities"]:
                lo, hi = cat.krw(act["price"])
                add("activities", f"{act.get('name_ko') or act['name']} ×{T}", lo * T, hi * T, act["price"]["source"]["level"])
    lo, hi = round(raw[0]), round(raw[1])
    return {"min": lo, "max": hi, "lines": lines, "unknown": unknown, "excluded": excluded}


# ------------------------------------------------------------------ 전체
def candidates(cat, q):
    if q["consider"] is not None:
        pool = [c for c in q["consider"] if c not in q["must"] and c not in q["avoid"]]
    else:
        pool = []
        for cid, c in cat.cities.items():
            if cid == q["origin"] or cid in q["must"] or cid in q["avoid"]:
                continue
            if q["regions"] and c["region"] not in q["regions"]:
                continue
            if not cat.adj.get(cid):
                continue
            cov = cat.coverage(cid)
            if q["style"] not in cov["plannable_tiers"]:
                continue
            pool.append(cid)
        pool.sort(key=lambda x: -sum(_score(a, q["interests"]) for a in cat.cities[x]["attractions"]))
    return pool[:MAX_CANDIDATES]


def plan(cat: Catalog, q: dict) -> dict:
    pool = candidates(cat, q)
    cost_of = {}
    for c in set(pool) | set(q["must"]):
        cost_of[c] = city_night_cost(cat, c, q["style"], q["travelers"])[0]
    evaluated, pruned_tree, no_route, too_few_nights = 0, 0, 0, 0
    best, best_any = None, None
    ranked = []
    room = q["max_cities"] - len(q["must"])
    for k in range(0, min(room, len(pool)) + 1):
        for extra in itertools.combinations(pool, k):
            cities = q["must"] + list(extra)
            if not cities:
                continue
            evaluated += 1
            # 트리 하한: 나라 노드의 최솟값 x 최소 박수로 예산을 이미 넘으면 그래프 탐색을 안 한다
            if q["budget"] is not None:
                lb = 0.0
                for c in cities:
                    b = cat.country_bounds(cat.cities[c]["country"])["min_night_plus_food_krw"].get(q["style"])
                    mn = q["min_nights"].get(c) or cat.cities[c]["min_nights"] or q["default_min_nights"]
                    lb += (b or 0) * mn
                if lb > q["budget"]:
                    pruned_tree += 1
                    continue
            o = order(cat, cities, q)
            if o is None:
                no_route += 1
                continue
            seq = o["route"][1:-1] if q["return_to"] is not None else o["route"][1:]
            nights, mins = allocate(cat, seq, q, cost_of)
            if nights is None:
                too_few_nights += 1
                continue
            stays, days = build_days(cat, seq, nights, q)
            m = money(cat, q, o["legs"], stays)
            value = sum(city_value(cat, c, nights[c], q["interests"]) for c in seq)
            hours = sum((e["hours"] or 0) for lg in o["legs"] for e in [next(x for x in cat.edges if x["id"] == lg["edge"])])
            cand = {"route": o["route"], "legs": o["legs"], "stays": stays, "days": days, "money": m,
                    "value": value, "transit_hours": round(hours, 2)}
            known = not m["unknown"]
            judged = m["max"] if q["basis"] == "max" else (m["min"] + m["max"]) / 2
            fits = known and (q["budget"] is None or judged <= q["budget"])
            key = (fits, known, value, -hours, -m["max"])
            ranked.append((key, cand))
            if best_any is None or key > best_any[0]:
                best_any = (key, cand)
            if fits and (best is None or key > best[0]):
                best = (key, cand)
    stats = {"combinations": evaluated, "pruned_by_tree_bound": pruned_tree, "no_direct_route": no_route,
             "not_enough_nights": too_few_nights, "candidate_pool": pool,
             "method": "도시 조합 전수 · 순서 Held-Karp 전수(직접 간선만) · 밤 배분 탐욕"}
    ranked.sort(key=lambda kc: kc[0], reverse=True)
    chosen = best[1] if best else (best_any[1] if best_any else None)
    alts = [{"route": c["route"], "nights": {s["city"]: s["nights"] for s in c["stays"]},
             "min": c["money"]["min"], "max": c["money"]["max"], "unknown": c["money"]["unknown"],
             "value": c["value"], "transit_hours": c["transit_hours"], "fits_budget": k[0]}
            for k, c in ranked if c is not chosen][:4]
    return {"alternatives": alts, "best": best[1] if best else None, "fallback": None if best else (best_any[1] if best_any else None),
            "stats": stats}
