"""명령줄.

    worldtrip plan <요청.json>        계획 + 판정 (종료 코드 0=ACCEPT 1=REJECT)
    worldtrip city <도시id> [등급]     도시 안내
    worldtrip status                  데이터가 아는 것 / 모르는 것
    worldtrip app [--frontend 폴더] [--ref main] [--update] [--no-open]
                                      어플리케이션: gentleMonster 의 화면을 받아 엔진에 붙이고 브라우저를 연다
    worldtrip serve [--host] [--port] [--frontend 폴더]   엔진(API + MCP) — 화면은 폴더를 줄 때만
    worldtrip mcp                     MCP stdio
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading

from . import engine


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="worldtrip", description="세계여행 계획 엔진")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("plan"); a.add_argument("request")
    a = sub.add_parser("city"); a.add_argument("city"); a.add_argument("style", nargs="?", default="mid")
    sub.add_parser("status")
    a = sub.add_parser("serve"); a.add_argument("--host"); a.add_argument("--port", type=int); a.add_argument("--frontend")
    a = sub.add_parser("app"); a.add_argument("--host"); a.add_argument("--port", type=int); a.add_argument("--frontend")
    a.add_argument("--ref", default=os.environ.get("WORLDTRIP_FRONTEND_REF", "main"))
    a.add_argument("--update", action="store_true"); a.add_argument("--no-open", action="store_true")
    sub.add_parser("mcp")
    args = ap.parse_args(argv)
    if args.cmd == "serve":
        from .http_server import serve
        serve(args.host, args.port, args.frontend)
        return 0
    if args.cmd == "app":
        return _app(args)
    if args.cmd == "mcp":
        from .mcp import run_stdio
        run_stdio()
        return 0
    if args.cmd == "plan":
        with (sys.stdin if args.request == "-" else open(args.request, encoding="utf-8")) as f:
            r = engine.plan_trip(json.load(f))
    elif args.cmd == "city":
        r = engine.city_guide(args.city, args.style)
    else:
        r = engine.data_status()
    print(json.dumps(r, ensure_ascii=False, indent=1))
    return 0 if r.get("verdict", "ACCEPT") == "ACCEPT" else 1


def _app(args) -> int:
    """화면을 붙인 엔진 하나 -- 어플리케이션."""
    from . import frontend
    from .http_server import make_server
    fd = args.frontend or os.environ.get("WORLDTRIP_FRONTEND")
    if not fd:
        try:
            fd = str(frontend.fetch(args.ref, update=args.update, log=lambda m: print(m, file=sys.stderr)))
        except frontend.FetchError as e:
            print(f"[worldtrip] {e}", file=sys.stderr)
            print("[worldtrip] gentleMonster 를 받아 두었다면 --frontend <gentleMonster>/gentle_monster/apps/worldtrip", file=sys.stderr)
            return 2
    if not frontend.complete(__import__("pathlib").Path(fd)):
        print(f"[worldtrip] {fd} 에 화면 파일({', '.join(frontend.NEED)})이 다 있지 않다", file=sys.stderr)
        return 2
    httpd = make_server(args.host, args.port, fd)
    h, p = httpd.server_address[:2]
    url = f"http://{'127.0.0.1' if h in ('0.0.0.0', '') else h}:{p}/"
    print(f"[worldtrip] 어플리케이션: {url}  (화면 {fd} · MCP {url}mcp) -- 끄려면 Ctrl+C", file=sys.stderr)
    if not args.no_open:
        import webbrowser
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0
