"""판정 원장 -- 덧붙이기만 한다. 지울 수 없다(se_new G020 의 정신).

줄마다 앞 줄의 해시를 품는다(해시 사슬). 한 줄이라도 고치거나 지우면 verify() 가 그 자리를 짚는다.
막는 것이 아니라 **들킨다** -- 파일 시스템 권한이 있는 사람은 통째로 다시 쓸 수 있다.
그 한계는 README 에 적혀 있다.

자리: 환경 변수 WORLDTRIP_LEDGER_ROOT (기본 ./data-ledger). se_new 의 SE_LEDGER_ROOT 와 같은 까닭이다 --
검사는 재는 것이지 남기는 것이 아니다. 검사는 이 변수를 임시 자리로 세우고 돈다.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - 윈도우
    fcntl = None

GENESIS = "0" * 64
_lock = threading.Lock()


def root() -> Path:
    return Path(os.environ.get("WORLDTRIP_LEDGER_ROOT", "data-ledger"))


def path() -> Path:
    return root() / "ledger.jsonl"


def canon(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(obj) -> str:
    return hashlib.sha256(canon(obj).encode("utf-8")).hexdigest()


def _last(p: Path) -> "tuple[int, str]":
    if not p.is_file():
        return 0, GENESIS
    last = None
    with p.open("rb") as f:
        for line in f:
            if line.strip():
                last = line
    if last is None:
        return 0, GENESIS
    rec = json.loads(last)
    return rec["seq"], rec["hash"]


def append(kind: str, body: dict) -> dict:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with _lock, p.open("a+", encoding="utf-8") as f:
        if fcntl:
            fcntl.flock(f, fcntl.LOCK_EX)
        try:
            seq, prev = _last(p)
            rec = {"seq": seq + 1, "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "kind": kind, "body": body, "prev": prev}
            rec["hash"] = sha(rec)
            f.write(canon(rec) + "\n")
            f.flush()
            os.fsync(f.fileno())
        finally:
            if fcntl:
                fcntl.flock(f, fcntl.LOCK_UN)
    return rec


def read(n: int = 20) -> "list[dict]":
    p = path()
    if not p.is_file():
        return []
    lines = [x for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    return [json.loads(x) for x in lines[-n:]]


def verify() -> dict:
    """사슬을 처음부터 다시 잰다. 끊긴 자리가 있으면 그 seq 를 말한다."""
    p = path()
    if not p.is_file():
        return {"ok": True, "entries": 0, "note": "원장이 아직 없다"}
    prev = GENESIS
    n = 0
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            return {"ok": False, "entries": n, "broken_at_line": i, "why": "JSON 이 아니다"}
        h = rec.pop("hash", None)
        if rec.get("prev") != prev:
            return {"ok": False, "entries": n, "broken_at_line": i, "why": "prev 가 앞 줄 해시와 다르다(줄이 지워졌거나 끼워졌다)"}
        if sha(rec) != h:
            return {"ok": False, "entries": n, "broken_at_line": i, "why": "내용이 해시와 다르다(줄이 고쳐졌다)"}
        if rec.get("seq") != n + 1:
            return {"ok": False, "entries": n, "broken_at_line": i, "why": "seq 가 이어지지 않는다"}
        prev = h
        n += 1
    return {"ok": True, "entries": n, "head": prev}
