"""카탈로그 -- 조사 원장(data/*.json)을 읽어 **트리**와 **그래프**로 세운다.

    트리   지역(region) -> 나라(country) -> 도시(city) -> 명소 · 숙소 · 식비 · 교통 · 식당 · 활동 · 고민 · 후기
    그래프 노드 = 도시, 간선 = 도시 사이 이동(항공 · 열차 · 야간열차 · 버스), 방향 없음

se_new 원칙:
  · **출처 없는 수는 들이지 않는다.** 가격 객체마다 url · 확인수준(full|snippet|user) · 날짜가 있어야 한다.
    어긋난 것은 버리고 rejected 에 까닭과 함께 남긴다 -- 조용히 고치지 않는다
  · **모르는 것은 모른다고 둔다.** 숙소·식비·교통 칸이 비면 0 이 아니라 None 이고, 그 도시에 묵는
    계획의 예산은 '모름' 이 된다(engine 이 거절한다)
  · 사용자가 아는 값(overrides)은 확인수준 'user' 로 섞는다 -- 조사값과 구별돼 보인다
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
REGION_FILES = ["east_asia", "southeast_asia", "western_europe", "central_south_europe", "long_haul"]
REGION_KO = {"east_asia": "동아시아", "southeast_asia": "동남아시아", "western_europe": "서유럽",
             "central_south_europe": "중·남유럽"}
TIERS = ("budget", "mid", "comfort")
LODGING_FOR = {"budget": "hostel", "mid": "mid", "comfort": "upscale"}
LEVELS = ("full", "snippet", "user")
MODES = ("flight", "train", "night_train", "bus", "ferry")
TAGS = ("history", "art", "culture", "architecture", "nature", "food", "nightlife", "shopping",
        "family", "views", "religion")


class CatalogError(ValueError):
    pass


def _price_ok(p, fx, where, rejected) -> bool:
    """가격 객체 하나가 쓸 만한가. 못 쓰면 까닭을 남기고 False."""
    if p is None:
        return False
    why = None
    if not isinstance(p, dict):
        why = "객체가 아니다"
    else:
        mn, mx = p.get("min"), p.get("max")
        s = p.get("source") or {}
        if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (mn, mx)):
            why = "min/max 가 수가 아니다"
        elif mn < 0 or mn > mx:
            why = f"min {mn} > max {mx} 이거나 음수"
        elif p.get("currency") not in fx:
            why = f"환율을 모르는 통화 {p.get('currency')!r}"
        elif s.get("level") not in LEVELS:
            why = f"확인수준 {s.get('level')!r} 이 full|snippet|user 가 아니다"
        elif s.get("level") != "user" and not str(s.get("url", "")).startswith(("http://", "https://")):
            why = "출처 url 이 없다"
    if why:
        rejected.append({"where": where, "why": why})
        return False
    return True


class Catalog:
    def __init__(self, raw_regions: "dict[str, dict]"):
        self.rejected: "list[dict]" = []
        self.gaps: "dict[str, list[str]]" = {}
        lh = raw_regions.get("long_haul") or {}
        self.fx = {"KRW": {"krw": 1.0, "source": {"level": "definition", "url": "", "date": ""}}}
        for k, v in ((lh.get("fx") or {}).get("rates") or {}).items():
            if isinstance(v, dict) and isinstance(v.get("krw"), (int, float)) and v["krw"] > 0:
                self.fx[k] = v
            else:
                self.rejected.append({"where": f"fx.{k}", "why": "환율 값이 없다"})
        self.cities: "dict[str, dict]" = {}
        self.edges: "list[dict]" = []
        for rid, d in raw_regions.items():
            self.gaps[rid] = list(d.get("gaps") or [])
            for c in d.get("cities") or []:
                self._add_city(rid, c)
        for rid, d in raw_regions.items():
            for e in d.get("edges") or []:
                self._add_edge(e, f"{rid}.edges")
        self._index()

    # ---------------------------------------------------------------- 들이기
    def _add_city(self, rid, c):
        cid = c.get("id")
        if not cid or cid in self.cities:
            self.rejected.append({"where": f"{rid}.cities", "why": f"id 가 없거나 겹친다: {cid!r}"})
            return
        rj = self.rejected
        w = f"{cid}"
        city = {k: c.get(k) for k in ("id", "name", "name_ko", "country", "country_name", "country_ko",
                                      "tz", "currency", "lat", "lon")}
        city["region"] = rid
        city["min_nights"] = c.get("min_nights") if isinstance(c.get("min_nights"), int) and c["min_nights"] >= 1 else None
        city["best_months"] = [m for m in (c.get("best_months") or []) if isinstance(m, int) and 1 <= m <= 12]
        lod = c.get("lodging") or {}
        city["lodging"] = {k: (lod.get(k) if _price_ok(lod.get(k), self.fx, f"{w}.lodging.{k}", rj) else None)
                           for k in ("hostel", "mid", "upscale")}
        food = c.get("food_per_day") or {}
        city["food_per_day"] = {k: (food.get(k) if _price_ok(food.get(k), self.fx, f"{w}.food.{k}", rj) else None)
                                for k in TIERS}
        tr = c.get("transit") or {}
        t = {"day_cost": tr.get("day_cost") if _price_ok(tr.get("day_cost"), self.fx, f"{w}.transit.day", rj) else None,
             "notes_ko": tr.get("notes_ko") or ""}
        ps = tr.get("pass")
        t["pass"] = ps if isinstance(ps, dict) and _price_ok(ps.get("price"), self.fx, f"{w}.transit.pass", rj) else None
        ap = tr.get("airport")
        t["airport"] = ap if isinstance(ap, dict) and _price_ok(ap.get("price"), self.fx, f"{w}.transit.airport", rj) else None
        city["transit"] = t

        def items(key, need_price=True):
            out = []
            for i, it in enumerate(c.get(key) or []):
                if not isinstance(it, dict):
                    continue
                if need_price and not _price_ok(it.get("price"), self.fx, f"{w}.{key}[{i}]", rj):
                    continue
                it = dict(it)
                if key in ("attractions", "activities"):
                    it["tags"] = [x for x in (it.get("tags") or []) if x in TAGS]
                    h = it.get("hours")
                    it["hours_known"] = isinstance(h, (int, float)) and h > 0
                    it["hours"] = float(h) if it["hours_known"] else 2.0     # 모르면 2시간으로 잡고 표시한다
                    it.setdefault("id", f"{cid}-{key[:3]}-{i}")
                out.append(it)
            return out

        city["attractions"] = items("attractions")
        city["restaurants"] = items("restaurants")
        city["activities"] = items("activities")
        city["concerns"] = [x for x in (c.get("concerns") or []) if isinstance(x, dict) and x.get("text_ko")
                            and str((x.get("source") or {}).get("url", "")).startswith("http")]
        city["reviews"] = [x for x in (c.get("reviews") or []) if isinstance(x, dict) and x.get("summary_ko")
                           and str((x.get("source") or {}).get("url", "")).startswith("http")]
        self.cities[cid] = city

    def _add_edge(self, e, where):
        if not isinstance(e, dict):
            return
        a, b = e.get("from"), e.get("to")
        why = None
        if a not in self.cities and a != "seoul" or b not in self.cities and b != "seoul":
            why = f"모르는 도시 {a!r}-{b!r}"
        elif a == b:
            why = "자기 자신"
        elif e.get("mode") not in MODES:
            why = f"모르는 수단 {e.get('mode')!r}"
        if why:
            self.rejected.append({"where": where, "why": why})
            return
        if not _price_ok(e.get("price"), self.fx, f"{where}.{a}-{b}", self.rejected):
            return
        h = e.get("hours")
        ed = {"from": a, "to": b, "mode": e["mode"], "price": e["price"],
              "hours": float(h) if isinstance(h, (int, float)) and h > 0 else None,
              "frequency_ko": e.get("frequency_ko") or "", "notes_ko": e.get("notes_ko") or ""}
        ed["id"] = f"{min(a, b)}~{max(a, b)}~{ed['mode']}"
        if any(x["id"] == ed["id"] for x in self.edges):
            self.edges = [x for x in self.edges if x["id"] != ed["id"]]   # 뒤에 온 것(사용자 값)이 이긴다
        self.edges.append(ed)

    def _index(self):
        self.adj: "dict[str, list[dict]]" = {}
        for e in self.edges:
            self.adj.setdefault(e["from"], []).append(e)
            self.adj.setdefault(e["to"], []).append(e)

    # ---------------------------------------------------------------- 돈
    def krw(self, p) -> "tuple[float, float]":
        r = self.fx[p["currency"]]["krw"]
        return p["min"] * r, p["max"] * r

    # ---------------------------------------------------------------- 트리
    def tree(self) -> dict:
        """지역 -> 나라 -> 도시. 도시마다 '무엇을 아는가' 를 같이 단다."""
        out: "dict[str, dict]" = {}
        for c in self.cities.values():
            r = out.setdefault(c["region"], {"id": c["region"], "name_ko": REGION_KO.get(c["region"], c["region"]),
                                             "countries": {}})
            k = r["countries"].setdefault(c["country"], {"code": c["country"], "name": c["country_name"],
                                                         "name_ko": c["country_ko"], "cities": []})
            k["cities"].append(self.coverage(c["id"]))
        for r in out.values():
            r["countries"] = sorted(r["countries"].values(), key=lambda x: x["code"])
            for k in r["countries"]:
                k["bounds"] = self.country_bounds(k["code"])
        return {"regions": sorted(out.values(), key=lambda x: x["id"])}

    def country_bounds(self, code: str) -> dict:
        """나라 노드의 집계 -- 그 나라 도시들의 1박 숙소·하루 식비 최솟값(KRW). 가지치기 하한에 쓴다."""
        lo = {}
        for c in self.cities.values():
            if c["country"] != code:
                continue
            for tier in TIERS:
                lp = c["lodging"].get(LODGING_FOR[tier])
                fp = c["food_per_day"].get(tier)
                if lp and fp:
                    v = self.krw(lp)[0] + self.krw(fp)[0]
                    lo[tier] = min(lo.get(tier, math.inf), v)
        return {"min_night_plus_food_krw": {k: round(v) for k, v in lo.items()}}

    def coverage(self, cid: str) -> dict:
        c = self.cities[cid]
        known = {
            "lodging": [k for k, v in c["lodging"].items() if v],
            "food": [k for k, v in c["food_per_day"].items() if v],
            "transit_day": bool(c["transit"]["day_cost"]),
            "airport": bool(c["transit"]["airport"]),
            "attractions": len(c["attractions"]),
            "restaurants": len(c["restaurants"]),
            "activities": len(c["activities"]),
            "concerns": len(c["concerns"]),
            "reviews": len(c["reviews"]),
            "links": sorted({(e["to"] if e["from"] == cid else e["from"]) for e in self.adj.get(cid, [])}),
        }
        plannable = [t for t in TIERS if c["lodging"].get(LODGING_FOR[t]) and c["food_per_day"].get(t)
                     and c["transit"]["day_cost"]]
        return {"id": cid, "name": c["name"], "name_ko": c["name_ko"], "country": c["country"],
                "known": known, "plannable_tiers": plannable}

    # ---------------------------------------------------------------- 그래프
    def links(self, a: str, b: str) -> "list[dict]":
        return [e for e in self.adj.get(a, []) if {e["from"], e["to"]} == {a, b}]

    def components(self) -> "list[list[str]]":
        nodes = set(self.cities) | {"seoul"}
        seen, out = set(), []
        for n in sorted(nodes):
            if n in seen:
                continue
            st, comp = [n], []
            seen.add(n)
            while st:
                x = st.pop()
                comp.append(x)
                for e in self.adj.get(x, []):
                    y = e["to"] if e["from"] == x else e["from"]
                    if y not in seen:
                        seen.add(y)
                        st.append(y)
            out.append(sorted(comp))
        return sorted(out, key=len, reverse=True)

    def shortest(self, a: str, b: str, weight: str = "cost", time_value_krw: float = 0.0) -> "dict | None":
        """다익스트라. weight=cost(최대가 KRW + 시간가치×시간) 또는 hours. 모르는 시간은 시간 가중 경로에서 뺀다."""
        import heapq
        if a not in self.adj or b not in self.adj:
            return None
        dist = {a: 0.0}
        prev: "dict[str, tuple[str, dict]]" = {}
        pq = [(0.0, a)]
        while pq:
            d, x = heapq.heappop(pq)
            if x == b:
                break
            if d > dist.get(x, math.inf):
                continue
            for e in self.adj.get(x, []):
                y = e["to"] if e["from"] == x else e["from"]
                if weight == "hours":
                    if e["hours"] is None:
                        continue
                    w = e["hours"]
                else:
                    w = self.krw(e["price"])[1] + time_value_krw * (e["hours"] or 0)
                nd = d + w
                if nd < dist.get(y, math.inf):
                    dist[y] = nd
                    prev[y] = (x, e)
                    heapq.heappush(pq, (nd, y))
        if b not in dist:
            return None
        path, legs, x = [b], [], b
        while x != a:
            p, e = prev[x]
            legs.append(e)
            path.append(p)
            x = p
        return {"path": path[::-1], "legs": legs[::-1], "weight": dist[b]}

    def stats(self) -> dict:
        lv: "dict[str, int]" = {}

        def walk(o):
            if isinstance(o, dict):
                if "min" in o and "max" in o and "source" in o:
                    k = (o.get("source") or {}).get("level")
                    lv[k] = lv.get(k, 0) + 1
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        walk(self.cities)
        walk(self.edges)
        return {"cities": len(self.cities), "edges": len(self.edges), "prices_by_level": lv,
                "fx": {k: {"krw": v["krw"], "date": (v.get("source") or {}).get("date")} for k, v in self.fx.items()},
                "rejected": len(self.rejected), "components": self.components()}


_BASE: "dict | None" = None


def raw_data() -> "dict[str, dict]":
    global _BASE
    if _BASE is None:
        _BASE = {r: json.loads((DATA / f"{r}.json").read_text(encoding="utf-8")) for r in REGION_FILES}
    return copy.deepcopy(_BASE)


def load(overrides: "dict | None" = None) -> Catalog:
    """조사 원장 + (있으면) 사용자 값. 사용자 값은 확인수준이 'user' 로 강제된다."""
    raw = raw_data()
    if overrides:
        raw["user"] = _user_layer(raw, overrides)
    return Catalog(raw)


def _user_layer(raw, ov) -> dict:
    if not isinstance(ov, dict):
        raise CatalogError("overrides: 객체여야 한다")

    def mark(o):
        if isinstance(o, dict):
            if "min" in o and "max" in o:
                o["source"] = {"level": "user", "url": "", "date": "", "note": str((o.get("source") or {}).get("note", ""))[:200]}
            for v in o.values():
                mark(v)
        elif isinstance(o, list):
            for v in o:
                mark(v)
        return o
    layer = {"gaps": [], "cities": [], "edges": []}
    cities = ov.get("cities") or {}
    if not isinstance(cities, dict):
        raise CatalogError("overrides.cities: {도시id: {칸: 값}} 객체여야 한다")
    for cid, patch in cities.items():
        found = None
        for rid, d in raw.items():
            for c in d.get("cities") or []:
                if c.get("id") == cid:
                    found = (rid, c)
        if not found or not isinstance(patch, dict):
            raise CatalogError(f"overrides.cities: 모르는 도시 {cid!r}")
        rid, c = found
        patch = mark(copy.deepcopy(patch))
        for k in ("lodging", "food_per_day", "transit"):
            if isinstance(patch.get(k), dict):
                c.setdefault(k, {})
                if c[k] is None:
                    c[k] = {}
                c[k].update(patch[k])
        for k in ("min_nights",):
            if k in patch:
                c[k] = patch[k]
    edges = ov.get("edges") or []
    if not isinstance(edges, list):
        raise CatalogError("overrides.edges: 목록이어야 한다")
    layer["edges"] = [mark(copy.deepcopy(e)) for e in edges]
    return layer
