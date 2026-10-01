"""MCP(stdio 진짜 프로세스) · HTTP(진짜 포트) · 앱 파일 · 보안 경계."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from tests._util import ROME, ROOT
from worldtrip.http_server import Handler

TOOLS = {"plan_trip", "verify_trip", "explore_world", "city_guide", "find_route", "data_status", "ledger_status"}


class Stdio(unittest.TestCase):
    def test_왕복(self):
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                                                            "clientInfo": {"name": "t", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "plan_trip", "arguments": {"request": ROME}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "city_guide", "arguments": {"city": "tokyo"}}},
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "find_route", "arguments": {"from": "berlin", "to": "rome"}}},
        ]
        p = subprocess.run([sys.executable, "-m", "worldtrip", "mcp"], cwd=ROOT, env=os.environ.copy(),
                           input="\n".join(map(json.dumps, msgs)) + "\n", capture_output=True, text=True, timeout=120)
        by = {m["id"]: m for m in map(json.loads, p.stdout.splitlines())}
        self.assertEqual(len(by), 5, p.stderr)
        self.assertEqual({t["name"] for t in by[2]["result"]["tools"]}, TOOLS)
        self.assertEqual(by[3]["result"]["structuredContent"]["verdict"], "ACCEPT")
        self.assertIn("snippet", json.dumps(by[3]["result"]["structuredContent"]["evidence"]))
        self.assertEqual(by[4]["result"]["structuredContent"]["city"]["id"], "tokyo")
        self.assertTrue(by[5]["result"]["structuredContent"]["found"])


class Http(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.srv.server_close()

    def req(self, path, body=None, headers=None):
        data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data,
                                   headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(r, timeout=60) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def test_화면이_없으면_안내_페이지(self):
        os.environ.pop("WORLDTRIP_FRONTEND", None)
        st, h, b = self.req("/")
        self.assertEqual(st, 200)
        self.assertIn("worldtrip app".encode(), b)
        self.assertEqual(self.req("/app.js")[0], 404)

    def test_화면_폴더를_내준다_밖은_못_나간다(self):
        d = Path(tempfile.mkdtemp()) / "fe"
        d.mkdir()
        (d / "index.html").write_text("<h1>FE</h1>", encoding="utf-8")
        (d / "app.js").write_text("1", encoding="utf-8")
        (d / ".secret").write_text("x", encoding="utf-8")
        (d.parent / "outside.txt").write_text("nope", encoding="utf-8")
        os.environ["WORLDTRIP_FRONTEND"] = str(d)
        try:
            st, h, b = self.req("/")
            self.assertEqual((st, b), (200, b"<h1>FE</h1>"))
            self.assertIn("default-src 'self'", h["Content-Security-Policy"])
            self.assertEqual(self.req("/app.js")[1]["Content-Type"], "text/javascript; charset=utf-8")
            for bad in ("/../outside.txt", "/%2e%2e/outside.txt", "/.secret", "/nope.html"):
                self.assertEqual(self.req(bad)[0], 404, bad)
            self.assertTrue(json.loads(self.req("/healthz")[2])["frontend"].endswith("fe"))
        finally:
            del os.environ["WORLDTRIP_FRONTEND"]

    def test_CORS(self):
        os.environ["WORLDTRIP_ALLOWED_ORIGINS"] = "https://pages.example"
        try:
            r = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/plan", method="OPTIONS",
                                       headers={"Origin": "https://pages.example"})
            with urllib.request.urlopen(r, timeout=10) as resp:
                self.assertEqual(resp.status, 204)
                self.assertEqual(resp.headers["Access-Control-Allow-Origin"], "https://pages.example")
            st, h, b = self.req("/api/plan", {"request": ROME}, {"Origin": "https://pages.example"})
            self.assertEqual((st, h["Access-Control-Allow-Origin"]), (200, "https://pages.example"))
            st, h, _ = self.req("/api/plan", {"request": ROME}, {"Origin": "https://evil.example"})
            self.assertEqual(st, 403)
            self.assertNotIn("Access-Control-Allow-Origin", h)
        finally:
            del os.environ["WORLDTRIP_ALLOWED_ORIGINS"]

    def test_api(self):
        st, _, b = self.req("/api/plan", {"request": ROME})
        self.assertEqual(json.loads(b)["verdict"], "ACCEPT")
        st, _, b = self.req("/api/explore")
        self.assertEqual(len(json.loads(b)["regions"]), 4)
        st, _, b = self.req("/api/city", {"city": "nowhere"})
        self.assertEqual(json.loads(b)["verdict"], "REJECT")

    def test_mcp_http_과_경계(self):
        st, h, b = self.req("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual({t["name"] for t in json.loads(b)["result"]["tools"]}, TOOLS)
        self.assertEqual(self.req("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"Origin": "http://evil.example"})[0], 403)
        os.environ["WORLDTRIP_TOKEN"] = "t0k"
        try:
            self.assertEqual(self.req("/api/plan", {"request": ROME})[0], 401)
            self.assertEqual(self.req("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"Authorization": "Bearer t0k"})[0], 200)
        finally:
            del os.environ["WORLDTRIP_TOKEN"]
        self.assertEqual(self.req("/api/plan", b"x" * 1_000_001)[0], 413)


class 흔적(unittest.TestCase):
    def test_저장소에_원장이_안_생긴다(self):
        self.assertFalse((ROOT / "data-ledger").exists())


if __name__ == "__main__":
    unittest.main()
