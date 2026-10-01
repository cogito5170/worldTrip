import unittest

import tests._util  # noqa: F401
from worldtrip import catalog as C


class 카탈로그(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat = C.load()

    def test_출처_없는_수가_없다(self):
        st = self.cat.stats()
        self.assertEqual(self.cat.rejected, [])
        self.assertGreater(sum(st["prices_by_level"].values()), 150)
        # 정직: 지금 원장은 전부 검색 조각이다. 원문 확인이 생기면 이 줄을 바꿔야 한다
        self.assertEqual(set(st["prices_by_level"]), {"snippet"})

    def test_트리(self):
        t = self.cat.tree()
        self.assertEqual(len(t["regions"]), 4)
        n = sum(len(k["cities"]) for r in t["regions"] for k in r["countries"])
        self.assertEqual(n, 20)

    def test_끊긴_도시를_말한다(self):
        comps = self.cat.components()
        lone = [c for c in comps if len(c) == 1]
        self.assertIn(["chiangmai"], lone)
        self.assertIn(["madrid"], lone)

    def test_모르는_칸은_0이_아니라_None(self):
        self.assertIsNone(self.cat.cities["osaka"]["lodging"]["mid"])
        self.assertEqual(self.cat.coverage("osaka")["plannable_tiers"], [])

    def test_사용자_값은_user_로_표시(self):
        cat = C.load({"cities": {"osaka": {"lodging": {"mid": {"min": 9000, "max": 15000, "currency": "JPY", "unit": "per night",
                                                                "source": {"level": "full", "url": "https://fake"}}}}}})
        self.assertEqual(cat.cities["osaka"]["lodging"]["mid"]["source"]["level"], "user")   # 'full' 이라고 우겨도 user

    def test_어긋난_가격은_까닭과_함께_버린다(self):
        raw = C.raw_data()
        raw["east_asia"]["cities"][1]["lodging"]["mid"]["min"] = 10 ** 9      # min > max
        raw["east_asia"]["cities"][1]["lodging"]["hostel"]["source"]["url"] = ""
        cat = C.Catalog(raw)
        whys = {r["where"]: r["why"] for r in cat.rejected}
        self.assertIn("tokyo.lodging.mid", whys)
        self.assertIn("tokyo.lodging.hostel", whys)
        self.assertIsNone(cat.cities["tokyo"]["lodging"]["mid"])

    def test_다익스트라(self):
        r = self.cat.shortest("berlin", "rome", "hours")
        self.assertEqual(r["path"][0], "berlin")
        self.assertEqual(r["path"][-1], "rome")
        self.assertIsNone(self.cat.shortest("madrid", "paris"))


if __name__ == "__main__":
    unittest.main()
