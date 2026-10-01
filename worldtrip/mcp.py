"""MCP(Model Context Protocol) 서버 -- 표준 라이브러리만으로 짓는다. (worldplan 의 것을 옮겼다)

배포가 설치할 것이 없게(se_new '사람에게 설치를 시키지 마라'). 두 전송을 같은 handle() 이 처리한다:
  · stdio            -- `worldplan mcp`  (줄 하나 = JSON-RPC 메시지 하나)
  · Streamable HTTP  -- `worldplan serve` 의 POST /mcp  (http_server.py)

이 서버에 붙은 LLM 은 **생성자**일 수 있어도 **심판**은 아니다. LLM 이 낸 계획은
verify_trip 로 결정적 심판에 올라가고, 심판이 REJECT 면 REJECT 다.
"""
from __future__ import annotations

import json
import sys
import traceback

from . import engine

SUPPORTED = ["2025-06-18", "2025-03-26", "2024-11-05"]
SERVER_INFO = {"name": "worldtrip", "title": "World Trip Planner", "version": engine.VERSION}
INSTRUCTIONS = (
    "Plans multi-country trips from a sourced price ledger (tree: region>country>city; graph: cities and transport links). "
    "A deterministic judge (not an LLM) re-adds every cost day by day and REJECTS by default: unknown core costs are not zero. "
    "ALL prices are search-result snippets researched on 2026-10-01 -- always relay evidence.banner, not_checked and "
    "invalidated_if to the user, and tell them to confirm sources before booking. Never invent prices, restaurants or reviews; "
    "if the user knows a price, pass it in request.overrides (it is marked level 'user'). "
    "If you propose an itinerary yourself, submit it to verify_trip and report the judge's verdict."
)

_REQ = {
    "type": "object",
    "required": ["start_date", "nights"],
    "properties": {
        "origin": {"type": "string", "default": "seoul"},
        "return_to": {"type": ["string", "null"], "description": "default = origin; null = one way"},
        "start_date": {"type": "string", "format": "date"},
        "nights": {"type": "integer", "minimum": 1, "maximum": 60},
        "travelers": {"type": "integer", "minimum": 1, "maximum": 10, "default": 1},
        "budget_krw": {"type": ["number", "null"], "description": "total for all travelers, KRW"},
        "budget_basis": {"enum": ["max", "mid"], "default": "max", "description": "judge budget at range max (default) or midpoint"},
        "style": {"enum": ["budget", "mid", "comfort"], "default": "mid"},
        "interests": {"type": "array", "items": {"enum": ["history", "art", "culture", "architecture", "nature", "food",
                                                          "nightlife", "shopping", "family", "views", "religion"]}},
        "must": {"type": "array", "items": {"type": "string"}, "description": "city ids that must be stayed in"},
        "consider": {"type": ["array", "null"], "items": {"type": "string"}, "description": "candidate city ids (null = auto)"},
        "avoid": {"type": "array", "items": {"type": "string"}},
        "regions": {"type": ["array", "null"], "items": {"enum": ["east_asia", "southeast_asia", "western_europe", "central_south_europe"]}},
        "max_cities": {"type": "integer", "minimum": 1, "maximum": 8, "default": 4},
        "default_min_nights": {"type": "integer", "default": 2},
        "min_nights": {"type": "object", "additionalProperties": {"type": "integer"}},
        "modes": {"type": "array", "items": {"enum": ["flight", "train", "night_train", "bus", "ferry"]}},
        "country_contiguous": {"type": "boolean", "default": True},
        "time_value_krw_per_hour": {"type": "number", "default": 10000},
        "activities": {"type": "array", "items": {"type": "string"}, "description": "activity names to include in budget"},
        "overrides": {"type": "object", "description": "user-known prices: {cities:{id:{lodging:{mid:price}}}, edges:[edge]}; price={min,max,currency,unit}"},
    },
}

TOOLS = [
    {"name": "plan_trip", "title": "Plan a multi-country trip",
     "description": "Choose cities (exhaustive over candidate subsets), order them over direct transport links (Held-Karp), "
                    "allocate nights, schedule attractions per day, and total the budget with min/max ranges. The judge "
                    "re-adds every cost; verdict ACCEPT only if all core costs are known and within budget. Returns "
                    "itinerary, budget lines with evidence level, restaurants, activities, traveler concerns, reviews, alternatives.",
     "inputSchema": {"type": "object", "required": ["request"], "properties": {"request": _REQ}},
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}},
    {"name": "verify_trip", "title": "Judge a proposed itinerary",
     "description": "Judge ANY itinerary (yours, a human's) against the ledger: edges exist, dates chain, min nights, "
                    "must/avoid, day capacity, known costs within budget. Use the 'plan' object shape returned by plan_trip.",
     "inputSchema": {"type": "object", "required": ["request", "plan"], "properties": {
         "request": _REQ, "plan": {"type": "object", "description": "{route, legs:[{from,to,edge}], stays, days}"}}},
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}},
    {"name": "explore_world", "title": "Browse the region > country > city tree",
     "description": "The tree with per-city data coverage (which costs are known), country lower bounds, and the transport graph.",
     "inputSchema": {"type": "object", "properties": {}}, "annotations": {"readOnlyHint": True}},
    {"name": "city_guide", "title": "Everything known about one city",
     "description": "Attractions, lodging/food/transit costs, restaurants, activities, traveler concerns and review summaries -- each with its source.",
     "inputSchema": {"type": "object", "required": ["city"], "properties": {
         "city": {"type": "string"}, "style": {"enum": ["budget", "mid", "comfort"], "default": "mid"}}},
     "annotations": {"readOnlyHint": True}},
    {"name": "find_route", "title": "Cheapest or fastest path between two cities",
     "description": "Dijkstra over the transport graph (weight: cost or hours).",
     "inputSchema": {"type": "object", "required": ["from", "to"], "properties": {
         "from": {"type": "string"}, "to": {"type": "string"}, "weight": {"enum": ["cost", "hours"], "default": "cost"},
         "time_value_krw_per_hour": {"type": "number", "default": 10000}}},
     "annotations": {"readOnlyHint": True}},
    {"name": "data_status", "title": "What the ledger knows and does not",
     "description": "Coverage per city, research gaps, evidence levels, FX dates. Read this before promising anything.",
     "inputSchema": {"type": "object", "properties": {}}, "annotations": {"readOnlyHint": True}},
    {"name": "ledger_status", "title": "Decision ledger",
     "description": "Re-verify the hash-chained decision ledger and return recent entries.",
     "inputSchema": {"type": "object", "properties": {"n": {"type": "integer"}}}, "annotations": {"readOnlyHint": True}},
]


def _call(name: str, a: dict):
    if name == "plan_trip":
        return engine.plan_trip(a.get("request"))
    if name == "verify_trip":
        return engine.verify_trip(a.get("request"), a.get("plan"))
    if name == "explore_world":
        return engine.explore()
    if name == "city_guide":
        return engine.city_guide(a.get("city"), a.get("style", "mid"))
    if name == "find_route":
        return engine.route(a.get("from"), a.get("to"), a.get("weight", "cost"), a.get("time_value_krw_per_hour", 10000))
    if name == "data_status":
        return engine.data_status()
    if name == "ledger_status":
        return engine.ledger_status(a.get("n", 10))
    raise KeyError(name)


def _err(mid, code, msg):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": msg}}


def handle(msg) -> "dict | list | None":
    """JSON-RPC 메시지 하나(또는 배치)를 처리한다. 알림이면 None."""
    if isinstance(msg, list):
        out = [r for r in (handle(m) for m in msg) if r is not None]
        return out or None
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method", ""), str):
        return _err(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid Request")
    method = msg.get("method")
    mid = msg.get("id")
    is_note = "id" not in msg
    if method is None:      # 클라이언트가 보낸 응답 -- 우리는 요청을 보내지 않으니 버린다
        return None
    params = msg.get("params") or {}
    if not isinstance(params, dict):
        return None if is_note else _err(mid, -32602, "params must be an object")
    try:
        if method == "initialize":
            want = params.get("protocolVersion")
            ver = want if want in SUPPORTED else SUPPORTED[0]
            res = {"protocolVersion": ver, "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": SERVER_INFO, "instructions": INSTRUCTIONS}
        elif method == "ping":
            res = {}
        elif method == "tools/list":
            res = {"tools": TOOLS}
        elif method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            if name not in {t["name"] for t in TOOLS}:
                return _err(mid, -32602, f"Unknown tool: {name}")
            if not isinstance(args, dict):
                return _err(mid, -32602, "arguments must be an object")
            try:
                data = _call(name, args)
                res = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=1)}],
                       "structuredContent": data, "isError": False}
            except Exception as e:  # 도구 실행 오류는 프로토콜 오류가 아니라 도구 결과다
                traceback.print_exc(file=sys.stderr)
                res = {"content": [{"type": "text", "text": f"내부 오류: {type(e).__name__}: {e}"}],
                       "isError": True}
        elif method.startswith("notifications/"):
            return None
        else:
            return None if is_note else _err(mid, -32601, f"Method not found: {method}")
    except Exception as e:  # pragma: no cover
        traceback.print_exc(file=sys.stderr)
        return None if is_note else _err(mid, -32603, f"Internal error: {e}")
    return None if is_note else {"jsonrpc": "2.0", "id": mid, "result": res}


def run_stdio(inp=None, out=None):
    """줄 단위 JSON-RPC. stdout 에는 프로토콜 메시지만 쓴다 -- 로그는 stderr."""
    inp = inp or sys.stdin
    out = out or sys.stdout
    for line in inp:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            resp = _err(None, -32700, "Parse error")
        else:
            resp = handle(msg)
        if resp is not None:
            out.write(json.dumps(resp, ensure_ascii=False) + "\n")
            out.flush()
