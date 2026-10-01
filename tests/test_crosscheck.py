"""독립 대조 -- 생성자의 순서·돈을 다른 길로 다시 구한다.

1) 순서: Held-Karp 결과 = 순열 전수(원장 간선을 직접 찾아 더한다)의 최솟값
2) 돈: 무작위 요청 다수에서 생성자 합 = 심판의 날짜별 재합 (plan_trip 의 T6 가 지나야 ACCEPT)
3) 그 대조가 빨개질 수 있나: 생성자의 방 수 계산을 일부러 틀리게 하면 T6 가 잡는다
"""
import itertools
import math
import random
import unittest

import tests._util  # noqa: F401
from worldtrip import catalog as C
from worldtrip import engine, planner
from worldtrip.planner import order, parse


def brute_order(cat, cities, q):
    best = math.inf
    for perm in itertools.permutations(cities):
        route = [q["origin"], *perm, q["return_to"]]
        if q["contiguous"]:
            seen, last, ok = set(), None, True
            for c in perm:
                k = cat.cities[c]["country"]
                if k != last and k in seen:
                    ok = False
                seen.add(k); last = k
            if not ok:
                continue
        tot = 0.0
        for a, b in zip(route, route[1:]):
            es = [e for e in cat.edges if {e["from"], e["to"]} == {a, b} and e["mode"] in q["modes"]]
            if not es:
                tot = math.inf
                break
            tot += min(cat.fx[e["price"]["currency"]]["krw"] * e["price"]["max"] + q["time_value"] * (e["hours"] or 0) for e in es)
        best = min(best, tot)
    return best


class 순서(unittest.TestCase):
    def test_순열_전수와_같다(self):
        cat = C.load()
        reach = [c for c in cat.cities if c != "seoul" and cat.adj.get(c)]
        rng = random.Random(7)
        seen = {"found": 0, "none": 0}
        for _ in range(80):
            cities = rng.sample(reach, rng.randint(1, 4))
            q = parse({"start_date": "2026-11-02", "nights": 8, "consider": [], "must": cities, "max_cities": 4,
                       "country_contiguous": rng.random() < .5}, cat)
            want = brute_order(cat, cities, q)
            got = order(cat, cities, q)
            if want == math.inf:
                self.assertIsNone(got, cities); seen["none"] += 1
            else:
                self.assertIsNotNone(got, cities)
                self.assertAlmostEqual(got["weight"], want, places=3); seen["found"] += 1
        # 사소한 설명 죽이기: 전부 길이 없거나 전부 있으면 이 대조는 아무것도 안 잰 것이다
        self.assertGreaterEqual(seen["found"], 10, seen)
        self.assertGreaterEqual(seen["none"], 10, seen)


def random_requests(n, seed=11):
    rng = random.Random(seed)
    regs = ["east_asia", "southeast_asia", "western_europe", "central_south_europe"]
    for _ in range(n):
        yield {"start_date": "2026-11-02", "nights": rng.randint(3, 12), "travelers": rng.randint(1, 4),
               "budget_krw": rng.choice([None, 2_000_000, 5_000_000, 12_000_000]),
               "budget_basis": rng.choice(["max", "mid"]), "style": rng.choice(["budget", "mid", "comfort"]),
               "interests": rng.sample(["art", "history", "food", "culture", "views"], 2),
               "regions": rng.sample(regs, rng.randint(1, 2)), "max_cities": rng.randint(1, 3)}


class 돈(unittest.TestCase):
    def test_무작위_요청에서_생성자_합_eq_심판_합(self):
        n_acc = n_rej = 0
        for raw in random_requests(40):
            r = engine.plan_trip(raw, record=False)
            if "judge" in r:
                self.assertNotIn("T6_crosscheck", {v["check"] for v in r["judge"]["violations"]}, raw)
                rc = r["judge"]["recount"]
                if not rc["unknown"]:
                    self.assertEqual((rc["min"], rc["max"]), (r["budget"]["min"], r["budget"]["max"]))
            n_acc += r["verdict"] == "ACCEPT"
            n_rej += r["verdict"] == "REJECT"
        self.assertGreaterEqual(n_acc, 5)
        self.assertGreaterEqual(n_rej, 5)

    def test_돌연변이_방_수를_틀리면_T6_가_잡는다(self):
        orig = planner._rooms
        planner._rooms = lambda style, trav: trav   # 2인 1실을 1인 1실로 -- 생성자만 틀린다
        try:
            r = engine.plan_trip({**tests._util.ROME}, record=False)
        finally:
            planner._rooms = orig
        self.assertEqual(r["verdict"], "REJECT")
        self.assertIn("T6_crosscheck", {v["check"] for v in r["judge"]["violations"]})


if __name__ == "__main__":
    unittest.main()
