"""HTTP 서버 -- 엔진 API + MCP(Streamable HTTP), 그리고 (있으면) 프론트엔드를 한 포트에.

프론트엔드는 이 저장소에 없다. gentleMonster 의 `gentle_monster/apps/worldtrip/` 이 화면이다(디자인은 그쪽이
책임진다). 이 서버는 그 폴더를 가리키면 그대로 내준다 -- `worldtrip app` 이 받아 와서 붙인다.

    GET  /...               프론트엔드 폴더의 파일 (WORLDTRIP_FRONTEND 또는 --frontend). 없으면 / 가 안내 페이지
    GET  /healthz           살아 있나 + 원장 사슬 + 프론트엔드가 붙었나
    POST /api/plan          {request}
    POST /api/verify        {request, plan}
    POST /api/city          {city, style}
    POST /api/route         {from, to, weight}
    GET  /api/explore       트리 + 그래프
    GET  /api/status        데이터가 아는 것 / 모르는 것
    GET  /api/ledger        판정 원장
    POST /mcp               MCP JSON-RPC

환경 변수
    WORLDTRIP_HOST              기본 127.0.0.1 (0.0.0.0 으로 열면 토큰을 세워라)
    WORLDTRIP_PORT              기본 8766
    WORLDTRIP_FRONTEND          프론트엔드 폴더
    WORLDTRIP_TOKEN             세우면 /api · /mcp 에 Authorization: Bearer 가 필요
    WORLDTRIP_ALLOWED_ORIGINS   쉼표로. 다른 곳(예: GitHub Pages)에 올린 화면이 이 엔진을 부를 수 있게 한다(CORS)
    WORLDTRIP_LEDGER_ROOT       판정 원장 자리(기본 ./data-ledger)
"""
from __future__ import annotations

import hmac
import json
import mimetypes
import os
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import engine, ledger, mcp

MAX_BODY = 1_000_000
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".json": "application/json; charset=utf-8", ".webmanifest": "application/manifest+json", ".svg": "image/svg+xml",
        ".png": "image/png", ".ico": "image/x-icon", ".txt": "text/plain; charset=utf-8"}
API_GET = ("/api/ledger", "/api/describe", "/api/explore", "/api/status")
API_POST = ("/mcp", "/api/plan", "/api/verify", "/api/city", "/api/route")

NO_FRONTEND = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>worldtrip engine</title></head><body>
<h1>worldtrip 엔진은 돌고 있다</h1>
<p>화면(프론트엔드)이 붙지 않았다. 화면은 gentleMonster 저장소의 <code>gentle_monster/apps/worldtrip/</code> 이다.</p>
<p><code>worldtrip app</code> 으로 띄우면 받아 와서 붙인다. 이미 있으면 <code>WORLDTRIP_FRONTEND=&lt;그 폴더&gt;</code>.</p>
<p>API 와 MCP 는 지금도 쓸 수 있다: <code>/api/explore</code> · <code>POST /mcp</code>.</p></body></html>"""


def frontend_dir() -> "Path | None":
    d = os.environ.get("WORLDTRIP_FRONTEND")
    if not d:
        return None
    p = Path(d).resolve()
    return p if (p / "index.html").is_file() else None


def _allowed_origins() -> "set[str]":
    return {x.strip() for x in os.environ.get("WORLDTRIP_ALLOWED_ORIGINS", "").split(",") if x.strip()}


class Handler(BaseHTTPRequestHandler):
    server_version = "worldtrip/" + engine.VERSION
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 요청 본문은 남기지 않는다 -- 사람의 일정이 들어 있다
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    # ---- 공통 ----
    def _cors(self) -> dict:
        o = self.headers.get("Origin")
        if o and o in _allowed_origins():
            return {"Access-Control-Allow-Origin": o, "Vary": "Origin",
                    "Access-Control-Allow-Headers": "Content-Type, Authorization, MCP-Protocol-Version, Mcp-Session-Id",
                    "Access-Control-Allow-Methods": "GET, POST, OPTIONS", "Access-Control-Max-Age": "600"}
        return {}

    def _send(self, code, body: bytes, ctype="application/json; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                         "connect-src 'self'; manifest-src 'self'; frame-ancestors 'none'")
        for k, v in {**self._cors(), **(extra or {})}.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code, obj, extra=None):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), extra=extra)

    def _origin_ok(self) -> bool:
        o = self.headers.get("Origin")
        if not o:
            return True
        host = self.headers.get("Host", "")
        return o in ({f"http://{host}", f"https://{host}"} | _allowed_origins())

    def _auth_ok(self) -> bool:
        tok = os.environ.get("WORLDTRIP_TOKEN")
        if not tok:
            return True
        return hmac.compare_digest(self.headers.get("Authorization", "").encode(), f"Bearer {tok}".encode())

    def _guard(self) -> bool:
        if not self._origin_ok():
            self._json(403, {"error": "Origin 이 허용 목록에 없다 (WORLDTRIP_ALLOWED_ORIGINS)"})
            return False
        if not self._auth_ok():
            self._json(401, {"error": "토큰이 필요하다 (Authorization: Bearer ...)"}, extra={"WWW-Authenticate": "Bearer"})
            return False
        return True

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = -1
        if n < 0 or n > MAX_BODY:
            self.close_connection = True
            self._json(413, {"error": f"본문은 {MAX_BODY} 바이트 이하"})
            return False, None
        raw = self.rfile.read(n)
        try:
            return True, json.loads(raw.decode("utf-8") or "null")
        except (ValueError, UnicodeDecodeError):
            if self.path.startswith("/mcp"):
                self._json(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            else:
                self._json(400, {"error": "JSON 이 아니다"})
            return False, None

    def _static(self, path: str):
        root = frontend_dir()
        if root is None:
            if path in ("/", "/index.html"):
                self._send(200, NO_FRONTEND.encode("utf-8"), "text/html; charset=utf-8")
            else:
                self._json(404, {"error": "없다 (프론트엔드가 붙지 않았다)"})
            return
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        f = (root / rel).resolve()
        # 폴더 밖(../)과 숨김 파일은 내주지 않는다
        if root not in f.parents or not f.is_file() or any(p.startswith(".") for p in Path(rel).parts):
            self._json(404, {"error": "없다"})
            return
        ctype = MIME.get(f.suffix) or mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        extra = {"Cache-Control": "no-cache"} if f.name in ("index.html", "sw.js") else {}
        self._send(200, f.read_bytes(), ctype, extra)

    # ---- 메서드 ----
    def do_OPTIONS(self):
        c = self._cors()
        if not c:
            self._json(403, {"error": "CORS 허용 목록에 없다 (WORLDTRIP_ALLOWED_ORIGINS)"})
            return
        self.send_response(204)
        for k, v in c.items():
            self.send_header(k, v)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            fd = frontend_dir()
            self._json(200, {"ok": True, "engine": engine.VERSION, "ledger": ledger.verify(),
                             "frontend": str(fd) if fd else None})
        elif path in API_GET:
            if self._guard():
                fn = {"/api/ledger": lambda: engine.ledger_status(20), "/api/describe": engine.describe,
                      "/api/explore": engine.explore, "/api/status": engine.data_status}[path]
                self._json(200, fn())
        elif path == "/mcp":
            self._json(405, {"error": "서버발 SSE 스트림은 열지 않는다 -- POST 를 써라"}, extra={"Allow": "POST"})
        elif path.startswith("/api/"):
            self._json(404, {"error": "없다"})
        else:
            self._static(path)

    def do_DELETE(self):
        self._json(405, {"error": "세션 상태를 두지 않는다"}, extra={"Allow": "POST"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path not in API_POST:
            self._json(404, {"error": "없다"})
            return
        if not self._guard():
            return
        ok, body = self._body()
        if not ok:
            return
        if path == "/mcp":
            return self._mcp(body)
        if not isinstance(body, dict):
            self._json(400, {"error": "JSON 객체가 필요하다"})
            return
        if path == "/api/plan":
            self._json(200, engine.plan_trip(body.get("request")))
        elif path == "/api/verify":
            self._json(200, engine.verify_trip(body.get("request"), body.get("plan")))
        elif path == "/api/city":
            self._json(200, engine.city_guide(body.get("city"), body.get("style", "mid")))
        elif path == "/api/route":
            self._json(200, engine.route(body.get("from"), body.get("to"), body.get("weight", "cost")))

    def _mcp(self, msg):
        pv = self.headers.get("MCP-Protocol-Version")
        if pv and pv not in mcp.SUPPORTED:
            self._json(400, {"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32600, "message": f"Unsupported MCP-Protocol-Version {pv}"}})
            return
        resp = mcp.handle(msg)
        if resp is None:
            self.send_response(202)
            for k, v in self._cors().items():
                self.send_header(k, v)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        extra = {}
        if isinstance(msg, dict) and msg.get("method") == "initialize":
            extra["Mcp-Session-Id"] = uuid.uuid4().hex
        self._json(200, resp, extra=extra)


def make_server(host=None, port=None, frontend=None) -> ThreadingHTTPServer:
    if frontend:
        os.environ["WORLDTRIP_FRONTEND"] = str(frontend)
    host = host or os.environ.get("WORLDTRIP_HOST", "127.0.0.1")
    port = int(port if port is not None else os.environ.get("WORLDTRIP_PORT", "8766"))
    if host not in ("127.0.0.1", "localhost", "::1") and not os.environ.get("WORLDTRIP_TOKEN"):
        sys.stderr.write("경고: 밖으로 열린 주소인데 WORLDTRIP_TOKEN 이 없다 -- 누구나 원장에 쓸 수 있다\n")
    if os.environ.get("WORLDTRIP_FRONTEND") and frontend_dir() is None:
        sys.stderr.write(f"경고: WORLDTRIP_FRONTEND={os.environ['WORLDTRIP_FRONTEND']} 에 index.html 이 없다 -- 안내 페이지를 낸다\n")
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    return httpd


def serve(host=None, port=None, frontend=None):
    httpd = make_server(host, port, frontend)
    h, p = httpd.server_address[:2]
    sys.stderr.write(f"worldtrip {engine.VERSION} -- http://{h}:{p}/  (MCP: POST /mcp · 화면: {frontend_dir() or '없음'})\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
