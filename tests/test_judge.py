"""옳은 계획 하나를 짓고 망가뜨려 -- 심판이 매번 그 검사로 빨개지는지."""
import copy
import unittest

from tests._util import BKK, ROME
from worldtrip import catalog as C
from worldtrip import engine
from worldtrip.judge import judge
from worldtrip.planner import parse


class 망가뜨리면_빨개진다(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = engine.plan_trip(ROME, record=False)
        assert cls.r["verdict"] == "ACCEPT", cls.r.get("reason")
        cls.cat = C.load()
        cls.q = parse(ROME, cls.cat)

    def red(self, mutate, code, q=None, claimed=None):
        p = copy.deepcopy(self.r["plan"])
        mutate(p)
        j = judge(self.cat, q or self.q, p, claimed)
        self.assertFalse(j["ok"], f"{code} 를 기대했는데 통과")
        self.assertIn(code, {v["check"] for v in j["violations"]}, j["violations"])

    def test_원본_초록(self):
        self.assertTrue(judge(self.cat, self.q, self.r["plan"])["ok"])

    def test_T0_출발지(self):
        self.red(lambda p: p["route"].__setitem__(0, "tokyo"), "T0_structure")

    def test_T0_빈_계획(self):
        def m(p):
            p["route"] = ["seoul", "seoul"]; p["stays"] = []; p["days"] = []; p["legs"] = p["legs"][:1]
        self.red(m, "T0_structure")

    def test_T1_없는_간선(self):
        self.red(lambda p: p["legs"][0].__setitem__("edge", "seoul~rome~teleport"), "T1_edges")

    def test_T1_다른_간선(self):
        self.red(lambda p: p["legs"][0].__setitem__("edge", "paris~seoul~flight"), "T1_edges")

    def test_T2_박수(self):
        def m(p):
            p["stays"][0]["nights"] -= 1
        self.red(m, "T2_dates")

    def test_T2_날짜_틈(self):
        self.red(lambda p: p["days"].pop(), "T2_dates")

    def test_T3_must(self):
        q = parse({**ROME, "must": ["rome", "florence"]}, self.cat)
        p = copy.deepcopy(self.r["plan"])
        if "florence" in [s["city"] for s in p["stays"]]:
            self.skipTest("이미 피렌체를 들른다")
        j = judge(self.cat, q, p)
        self.assertIn("T3_requirements", {v["check"] for v in j["violations"]})

    def test_T4_하루_용량(self):
        def m(p):
            c = p["days"][0]["city"]
            p["days"][0]["attractions"] = [a["id"] for a in self.cat.cities[c]["attractions"]]
        self.red(m, "T4_day_capacity")

    def test_T4_남의_도시_명소(self):
        self.red(lambda p: p["days"][1]["attractions"].append("senso-ji"), "T4_day_capacity")

    def test_T5_예산(self):
        q = parse({**ROME, "budget_krw": 1_000_000}, self.cat)
        self.red(lambda p: None, "T5_budget", q=q)

    def test_T5_모르는_비용(self):
        # 오사카에 묵는 계획 -- 숙소·식비를 모른다. 0 으로 치면 안 된다
        raw = {**BKK, "must": ["osaka"], "style": "mid"}
        q = parse(raw, self.cat)
        p = {"route": ["seoul", "osaka", "seoul"],
             "legs": [{"from": "seoul", "to": "osaka", "edge": "osaka~seoul~flight"},
                      {"from": "osaka", "to": "seoul", "edge": "osaka~seoul~flight"}],
             "stays": [{"city": "osaka", "nights": 5, "arrive": "2026-11-02", "depart": "2026-11-07", "attractions": []}],
             "days": [{"date": f"2026-11-0{i}", "city": "osaka", "attractions": []} for i in range(2, 7)]}
        j = judge(self.cat, q, p)
        self.assertIn("T5_budget", {v["check"] for v in j["violations"]})
        self.assertIn("osaka lodging", " ".join(j["money"]["unknown"]))

    def test_T6_돈_대조(self):
        m = self.r["budget"]
        self.red(lambda p: None, "T6_crosscheck", claimed={"min": m["min"], "max": m["max"] + 1000})

    def test_verify_도구도_같은_심판(self):
        self.assertEqual(engine.verify_trip(ROME, self.r["plan"], record=False)["verdict"], "ACCEPT")
        bad = copy.deepcopy(self.r["plan"]); bad["stays"][0]["nights"] += 3
        self.assertEqual(engine.verify_trip(ROME, bad, record=False)["verdict"], "REJECT")
        self.assertEqual(engine.verify_trip(ROME, {"route": "x"}, record=False)["verdict"], "REJECT")


if __name__ == "__main__":
    unittest.main()
