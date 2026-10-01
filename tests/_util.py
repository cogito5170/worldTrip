import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 검사는 재는 것이지 남기는 것이 아니다 -- 원장은 늘 임시 자리에
os.environ["WORLDTRIP_LEDGER_ROOT"] = tempfile.mkdtemp(prefix="worldtrip-test-")

ROME = {"origin": "seoul", "start_date": "2026-11-02", "nights": 6, "travelers": 2, "budget_krw": 12000000,
        "style": "mid", "interests": ["art", "history"], "must": ["rome"], "consider": ["florence"], "max_cities": 2}
BKK = {"origin": "seoul", "start_date": "2026-11-02", "nights": 5, "travelers": 1, "budget_krw": 3000000,
       "style": "budget", "interests": ["food", "culture"], "must": ["bangkok"], "consider": []}
