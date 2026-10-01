"""엔진 -- 카탈로그 -> 생성(planner) -> 판정(judge) -> 원장 -> 보고.

보고 규칙(se_new)
  · verdict 는 심판이 정한다. ACCEPT 는 '핵심 비용을 다 알고, 최대가가 예산 안이고, 위반 0' 일 때만
  · 모든 수 옆에 확인수준이 있다. 지금 원장은 전부 '검색 조각(snippet)' 이다 -- 매 응답 맨 위에 그렇게 적는다
  · 최적이라는 말은 전수한 부분에만 쓴다(조합·순서). 밤 배분은 탐욕이라고 적는다
  · 무효화 조건과 못 본 것을 매번 같이 낸다
"""
from __future__ import annotations

from . import catalog as C
from . import ledger
from .judge import CHECKS, judge
from .planner import RequestError, money, parse, plan as make_plan

VERSION = "0.1.0"

NOT_CHECKED = [
    "입국 요건(비자·전자여행허가 ETIAS·K-ETA 등)과 여권 유효기간은 재지 않는다",
    "실시간 항공·열차 좌석과 그날 가격은 보지 않는다 -- 원장의 범위값이다",
    "명소의 휴관일·예약 마감은 보지 않는다(예약 필요 표시는 원장에 있을 때만)",
    "날씨·여행경보·파업은 보지 않는다",
    "장거리 비행의 시차·야간 도착으로 줄어드는 첫날 시간은 '도착일 4시간' 으로만 반영한다",
    "중형·고급 숙소는 2인 1실로 센다(호스텔은 1인 1침대)",
]


def _evidence_banner(levels: dict) -> str:
    full = levels.get("full", 0)
    tot = sum(levels.values())
    if tot and full == 0:
        user = levels.get("user", 0)
        return (f"이 계획에 쓴 수 {tot}개 중 원문을 읽고 확인한 것은 0개다 -- "
                f"{tot - user}개는 검색 결과 조각, {user}개는 사용자 입력이다. 예약 전에 출처를 직접 확인하라")
    return f"이 계획에 쓴 수 {tot}개 중 원문 확인 {full}개"


def _invalidated(cat, q) -> list:
    fx = {k: (v.get("source") or {}).get("date") for k, v in cat.fx.items() if k != "KRW"}
    return [
        "환율이 원장 날짜와 달라지면 금액이 바뀐다: " + ", ".join(f"{k} {d}" for k, d in sorted(fx.items())),
        "조사일 2026-10-01 의 검색 조각 값이다 -- 가격 개정(입장료 인상 등)이 나오면 그 항목은 무효다",
        f"인원({q['travelers']})·등급({q['style']})·박수({q['nights']})가 바뀌면 다시 계획해야 한다",
    ]


def _expand(cat, q, p) -> dict:
    """사람이 읽는 꼴로 펼친다 -- 이름 · 출처 · 팁."""
    emap = {e["id"]: e for e in cat.edges}
    legs = []
    for lg in p["legs"]:
        e = emap[lg["edge"]]
        lo, hi = cat.krw(e["price"])
        legs.append({**lg, "hours": e["hours"], "price_krw": [round(lo), round(hi)], "price": e["price"],
                     "frequency_ko": e["frequency_ko"], "notes_ko": e["notes_ko"],
                     "from_ko": cat.cities[lg["from"]]["name_ko"], "to_ko": cat.cities[lg["to"]]["name_ko"]})
    cities = []
    for s in p["stays"]:
        c = cat.cities[s["city"]]
        amap = {a["id"]: a for a in c["attractions"]}
        rest = [r for r in c["restaurants"] if r.get("tier") == q["style"]] or c["restaurants"]
        cities.append({
            **s, "name": c["name"], "name_ko": c["name_ko"], "country_ko": c["country_ko"], "tz": c["tz"],
            "lat": c["lat"], "lon": c["lon"],
            "attraction_detail": [{**amap[a], "price_krw": [round(x) for x in cat.krw(amap[a]["price"])]}
                                  for a in s["attractions"]],
            "other_attractions": [a for a in c["attractions"] if a["id"] not in s["attractions"]],
            "restaurants": rest, "activities": c["activities"], "concerns": c["concerns"], "reviews": c["reviews"],
            "transit": c["transit"], "best_months": c["best_months"],
            "season_note": (None if not c["best_months"] or q["start"].month in c["best_months"]
                            else f"{q['start'].month}월은 원장의 추천 시기({', '.join(map(str, c['best_months']))}월)가 아니다"),
        })
    names = {}
    for c in p["stays"]:
        for a in cat.cities[c["city"]]["attractions"]:
            names[a["id"]] = a.get("name_ko") or a["name"]
    days = [{**d, "attraction_names": [names.get(a, a) for a in d["attractions"]],
             "city_ko": cat.cities[d["city"]]["name_ko"]} for d in p["days"]]
    return {"route": p["route"], "route_ko": [cat.cities[c]["name_ko"] for c in p["route"]], "legs": legs,
            "cities": cities, "days": days, "value": p["value"], "transit_hours": p["transit_hours"]}


def _budget_view(m, q):
    cats = {}
    for ln in m["lines"]:
        k = cats.setdefault(ln["category"], {"min": 0, "max": 0})
        k["min"] += ln["min"]
        k["max"] += ln["max"]
    out = {"min": m["min"], "max": m["max"], "per_person": [round(m["min"] / q["travelers"]), round(m["max"] / q["travelers"])],
           "by_category": cats, "lines": m["lines"], "unknown": m["unknown"], "excluded": m["excluded"],
           "budget_krw": q["budget"], "basis": q["basis"]}
    if q["budget"] is not None:
        out["headroom_at_max"] = round(q["budget"] - m["max"])
    return out


def _diagnose(cat, q, res) -> list:
    out = []
    st = res["stats"]
    for c in q["must"]:
        cov = cat.coverage(c)
        if q["style"] not in cov["plannable_tiers"]:
            miss = city_missing(cat, c, q["style"])
            out.append(f"'{cat.cities[c]['name_ko']}' 는 {q['style']} 등급의 비용을 모른다({', '.join(miss)}) -- "
                       f"overrides 로 아는 값을 넣거나 등급을 바꿔라")
        if not cat.adj.get(c):
            out.append(f"'{cat.cities[c]['name_ko']}' 로 가는 연결이 원장에 하나도 없다 -- overrides.edges 로 넣어라")
    if st["no_direct_route"]:
        out.append(f"{st['no_direct_route']}개 조합은 도시 사이 직접 연결이 원장에 없어 순서를 못 만들었다")
    if st["not_enough_nights"]:
        out.append(f"{st['not_enough_nights']}개 조합은 최소 박수 합이 {q['nights']}박을 넘었다")
    if st["pruned_by_tree_bound"]:
        out.append(f"{st['pruned_by_tree_bound']}개 조합은 숙소+식비 하한만으로 이미 예산을 넘었다(트리 하한)")
    if not st["candidate_pool"] and not q["must"]:
        out.append("후보 도시가 없다 -- regions·style 에 맞는, 비용을 아는 도시가 원장에 없다")
    return out


def city_missing(cat, cid, style):
    c = cat.cities[cid]
    miss = []
    if not c["lodging"].get(C.LODGING_FOR[style]):
        miss.append(f"숙소 {C.LODGING_FOR[style]}")
    if not c["food_per_day"].get(style):
        miss.append(f"식비 {style}")
    if not c["transit"]["day_cost"]:
        miss.append("시내교통")
    return miss


def _record(kind, sha, verdict, extra):
    rec = ledger.append(kind, {"input_sha256": sha, "verdict": verdict, "engine": VERSION, **extra})
    return {"seq": rec["seq"], "hash": rec["hash"]}


def plan_trip(raw: dict, record: bool = True) -> dict:
    sha = ledger.sha(raw)
    try:
        cat = C.load((raw or {}).get("overrides") if isinstance(raw, dict) else None)
        q = parse(raw, cat)
    except (RequestError, C.CatalogError) as e:
        errs = getattr(e, "errors", [str(e)])
        out = {"verdict": "REJECT", "reason": "요청을 판정할 수 없다 -- 애매하거나 틀린 입력은 추측하지 않는다",
               "input_errors": errs, "engine": VERSION, "input_sha256": sha}
        if record:
            out["ledger"] = _record("plan", sha, "REJECT", {"stage": "input"})
        return out
    res = make_plan(cat, q)
    chosen = res["best"] or res["fallback"]
    out = {"engine": VERSION, "input_sha256": sha, "request": _echo(q),
           "search": {**res["stats"], "optimality": (
               "머무는 도시 조합과 방문 순서는 후보 안에서 전수했다(직접 연결만). 밤 배분은 탐욕이라 "
               "배분까지 최적이라고는 말하지 않는다. 목표: 예산 안 → 관심사 점수 최대 → 이동 시간 최소 → 최대가 최소")},
           "not_checked": NOT_CHECKED, "invalidated_if": _invalidated(cat, q)}
    if chosen is None:
        out["verdict"] = "REJECT"
        out["reason"] = "만들 수 있는 계획이 없다"
        out["diagnostics"] = _diagnose(cat, q, res)
    else:
        j = judge(cat, q, chosen, claimed={"min": chosen["money"]["min"], "max": chosen["money"]["max"]})
        ok = j["ok"] and res["best"] is not None
        out["verdict"] = "ACCEPT" if ok else "REJECT"
        if ok:
            out["reason"] = f"심판이 {len(j['checks_run'])}가지를 다시 재서 위반 0건 · 돈을 날짜별로 다시 더해 생성자와 일치"
            if q["budget"] is not None and q["basis"] == "mid":
                out["reason"] += (f" · 단, 예산은 범위의 가운데값으로 판정했다 -- 최대가 {chosen['money']['max']:,}원이면 "
                                  f"{chosen['money']['max'] - q['budget']:,.0f}원 넘을 수 있다" if chosen["money"]["max"] > q["budget"] else "")
        else:
            out["reason"] = "예산·비용을 모두 지키는 계획이 없다 -- 아래는 가장 가까운 안(성공이 아니다)"
            out["diagnostics"] = _diagnose(cat, q, res)
        out["itinerary"] = _expand(cat, q, chosen)
        out["budget"] = _budget_view(chosen["money"], q)
        out["judge"] = {"checks_run": j["checks_run"], "violations": j["violations"],
                        "recount": j["money"]}
        out["evidence"] = {"levels": (j["money"] or {}).get("levels", {}),
                           "banner": _evidence_banner((j["money"] or {}).get("levels", {}))}
        out["plan"] = {k: chosen[k] for k in ("route", "legs", "stays", "days")}
        out["alternatives"] = [{**a, "route_ko": [cat.cities[c]["name_ko"] for c in a["route"]]}
                               for a in res["alternatives"]]
    if record:
        out["ledger"] = _record("plan", sha, out["verdict"], {
            "route": out.get("plan", {}).get("route"), "max_krw": out.get("budget", {}).get("max"),
            "violations": len(out.get("judge", {}).get("violations", []))})
    return out


def verify_trip(raw: dict, plan: dict, record: bool = True) -> dict:
    """누가 만든 계획이든(LLM·사람) 같은 심판에. plan = {route, legs[{from,to,edge}], stays, days}."""
    sha = ledger.sha({"request": raw, "plan": plan})
    try:
        cat = C.load((raw or {}).get("overrides") if isinstance(raw, dict) else None)
        q = parse(raw, cat)
        if not isinstance(plan, dict):
            raise RequestError(["plan: {route, legs, stays, days} 객체가 필요하다"])
        for k in ("route", "legs", "stays", "days"):
            if not isinstance(plan.get(k), list):
                raise RequestError([f"plan.{k}: 목록이 필요하다"])
        j = judge(cat, q, plan)
    except (RequestError, C.CatalogError, KeyError, TypeError, ValueError) as e:
        errs = getattr(e, "errors", [f"{type(e).__name__}: {e}"])
        out = {"verdict": "REJECT", "reason": "계획을 읽을 수 없다", "input_errors": errs, "engine": VERSION}
        if record:
            out["ledger"] = _record("verify", sha, "REJECT", {"stage": "input"})
        return out
    out = {"engine": VERSION, "verdict": "ACCEPT" if j["ok"] else "REJECT",
           "reason": "위반 0건" if j["ok"] else f"위반 {len(j['violations'])}건 -- 기본은 거절이다",
           "judge": {"checks_run": j["checks_run"], "violations": j["violations"], "recount": j["money"]},
           "evidence": {"levels": (j["money"] or {}).get("levels", {}),
                        "banner": _evidence_banner((j["money"] or {}).get("levels", {}))},
           "note": "맞는가만 판정한다. 더 나은 계획이 있는지는 plan_trip 이 말한다",
           "not_checked": NOT_CHECKED, "invalidated_if": _invalidated(cat, q), "input_sha256": sha}
    if record:
        out["ledger"] = _record("verify", sha, out["verdict"], {"violations": len(j["violations"])})
    return out


def _echo(q):
    return {**{k: v for k, v in q.items() if k not in ("start", "avoid")}, "start_date": q["start"].isoformat(),
            "avoid": sorted(q["avoid"])}


def explore(overrides=None) -> dict:
    cat = C.load(overrides)
    t = cat.tree()
    t["stats"] = cat.stats()
    t["edges"] = [{"id": e["id"], "from": e["from"], "to": e["to"], "mode": e["mode"], "hours": e["hours"],
                   "price_krw": [round(x) for x in cat.krw(e["price"])], "level": e["price"]["source"]["level"]}
                  for e in cat.edges]
    t["coords"] = {cid: [c["lat"], c["lon"]] for cid, c in cat.cities.items()}
    t["names_ko"] = {cid: c["name_ko"] for cid, c in cat.cities.items()}
    return t


def city_guide(city: str, style: str = "mid") -> dict:
    cat = C.load()
    if city not in cat.cities:
        return {"verdict": "REJECT", "input_errors": [f"모르는 도시 {city!r}. 있는 도시: {sorted(cat.cities)}"]}
    if style not in C.TIERS:
        return {"verdict": "REJECT", "input_errors": [f"style: {C.TIERS}"]}
    c = cat.cities[city]
    k = lambda p: [round(x) for x in cat.krw(p)] if p else None
    return {"city": {kk: c[kk] for kk in ("id", "name", "name_ko", "country_ko", "tz", "currency", "best_months")},
            "coverage": cat.coverage(city),
            "costs_krw": {"lodging": {t: k(v) for t, v in c["lodging"].items()},
                          "food_per_day": {t: k(v) for t, v in c["food_per_day"].items()},
                          "transit_day": k(c["transit"]["day_cost"])},
            "attractions": [{**a, "price_krw": k(a["price"])} for a in c["attractions"]],
            "restaurants": [{**r, "price_krw": k(r["price"])} for r in c["restaurants"]],
            "activities": [{**a, "price_krw": k(a["price"])} for a in c["activities"]],
            "transit": c["transit"], "concerns": c["concerns"], "reviews": c["reviews"],
            "gaps": [g for g in cat.gaps.get(c["region"], []) if c["name"].lower() in g.lower() or c["id"] in g.lower()],
            "missing_for_style": city_missing(cat, city, style),
            "note": "모든 수는 2026-10-01 검색 조각 수준이다. 예약 전에 출처를 확인하라"}


def route(a: str, b: str, weight: str = "cost", time_value_krw_per_hour: float = 10000) -> dict:
    cat = C.load()
    for x in (a, b):
        if x not in cat.cities:
            return {"verdict": "REJECT", "input_errors": [f"모르는 도시 {x!r}"]}
    if weight not in ("cost", "hours"):
        return {"verdict": "REJECT", "input_errors": ["weight: cost|hours"]}
    r = cat.shortest(a, b, weight, float(time_value_krw_per_hour))
    if r is None:
        comp = next((c for c in cat.components() if a in c), [])
        return {"found": False, "why": f"원장에 {a} 와 {b} 를 잇는 길이 없다", "reachable_from_a": comp}
    return {"found": True, "path": r["path"], "legs": [{"edge": e["id"], "mode": e["mode"], "hours": e["hours"],
                                                       "price_krw": [round(x) for x in cat.krw(e["price"])],
                                                       "source": e["price"]["source"]} for e in r["legs"]],
            "weight": round(r["weight"], 2), "weight_kind": weight,
            "note": "경유가 끼면 실제로는 연결편 시간이 더 든다 -- 원장은 구간별 값뿐이다"}


def data_status() -> dict:
    cat = C.load()
    return {"stats": cat.stats(), "coverage": [cat.coverage(c) for c in sorted(cat.cities)],
            "gaps": cat.gaps, "rejected": cat.rejected,
            "honest_note": ("조사는 2026-10-01 한 세션에서 했다. 웹 페이지 원문 읽기(WebFetch)가 막혀 모든 값이 "
                            "검색 조각이고, 검색 한도(200회)가 차서 오사카·서울·타이베이·호찌민·싱가포르·바르셀로나·"
                            "마드리드는 거의 비어 있다. 빈 칸은 0 이 아니라 '모름' 이다.")}


def ledger_status(n: int = 10) -> dict:
    n = n if isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= 200 else 10
    return {"chain": ledger.verify(), "recent": ledger.read(n)}


def describe() -> dict:
    return {"engine": VERSION, "checks": [{"id": k, "what": v} for k, v in CHECKS.items()], "not_checked": NOT_CHECKED}


_ = money  # 생성자 쪽 계산은 planner 안에서만 쓴다(심판은 따로 센다)
