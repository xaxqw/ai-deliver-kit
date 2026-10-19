# -*- coding: utf-8 -*-
"""交付控制台：把 deliverkit 的命令行能力搬到网页上

端口默认 8100（避免和被测应用自己的 8000 冲突）。

    python server.py            # 控制台
    python server.py 8100
"""

import json
import os
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from deliverkit import bundle, env, evaluate, patrol, report, smoke, snapshot  # noqa: E402

STATIC = os.path.join(ROOT, "static")
TARGET_DIR = os.path.join(ROOT, "targets")
OUT_DIR = os.path.join(ROOT, "out")

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".md": "text/plain; charset=utf-8",
}


def load_target(filename):
    path = os.path.join(TARGET_DIR, filename)
    if not os.path.exists(path):
        path = filename                     # 也允许传绝对路径
    with open(path, encoding="utf-8") as f:
        t = json.load(f)
    p = t["app_dir"]
    if not os.path.isabs(p):
        p = os.path.join(ROOT, p)           # 相对路径按工具包根目录解析
    t["app_dir"] = os.path.abspath(p)
    t["_file"] = os.path.basename(path)
    return t


def list_targets():
    if not os.path.isdir(TARGET_DIR):
        return []
    out = []
    for fn in sorted(os.listdir(TARGET_DIR)):
        if not fn.endswith(".json"):
            continue
        try:
            t = load_target(fn)
            out.append({"file": fn, "name": t.get("name", fn),
                        "code": t.get("code", ""), "version": t.get("version", ""),
                        "port": t.get("port"), "app_dir": t.get("app_dir")})
        except Exception:
            continue
    return out


def list_reports():
    if not os.path.isdir(OUT_DIR):
        return []
    out = []
    for fn in sorted(os.listdir(OUT_DIR)):
        if not fn.endswith(".md"):
            continue
        p = os.path.join(OUT_DIR, fn)
        st = os.stat(p)
        out.append({"name": fn, "size": st.st_size,
                    "mtime": time.strftime("%Y-%m-%d %H:%M:%S",
                                           time.localtime(st.st_mtime))})
    return sorted(out, key=lambda x: x["mtime"], reverse=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "DeliverKit/0.1"

    # ---------- 基础 ----------
    def _send(self, code, body, ctype):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str),
                   "application/json; charset=utf-8")

    def _static(self, name):
        path = os.path.join(STATIC, name.lstrip("/"))
        if not os.path.isfile(path):
            self._json({"error": "not found"}, 404)
            return
        ext = os.path.splitext(path)[1].lower()
        with open(path, "rb") as f:
            self._send(200, f.read(), MIME.get(ext, "application/octet-stream"))

    # ---------- SSE ----------
    def _start_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

    def _sse(self, event, data):
        self.wfile.write(
            f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
            .encode("utf-8"))
        self.wfile.flush()

    # ---------- GET ----------
    def do_GET(self):
        u = urlparse(self.path)
        p, qs = u.path, parse_qs(u.query)
        try:
            if p == "/api/eval/stream":
                tfile = (qs.get("target") or ["retail-nl2sql.json"])[0]
                raw_limit = (qs.get("limit") or ["0"])[0]
                limit = int(raw_limit) if raw_limit.isdigit() and int(raw_limit) > 0 else None
                target = load_target(tfile)
                os.makedirs(OUT_DIR, exist_ok=True)

                self._start_sse()
                final = None
                try:
                    for item in evaluate.run_eval_stream(target, limit=limit):
                        if "error" in item:
                            self._sse("error", item)
                            break
                        if item.get("done"):
                            final = item
                            # 先把报告落盘再推 summary，避免客户端断开导致文件没写
                            name = f"{target['code']}-eval-report.md"
                            self._write_md(name, report.eval_report(final, target))
                            self._sse("summary", {
                                "summary": item["summary"],
                                "failures": item["failures"][:20],
                            })
                            self._sse("report", {"name": name})
                        else:
                            self._sse("progress", item)
                    self._sse("done", {})
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return

            if p in ("/", "/index.html"):
                self._static("index.html")
            elif p == "/api/targets":
                self._json({"targets": list_targets()})
            elif p == "/api/snapshots":
                tfile = (qs.get("target") or ["retail-nl2sql.json"])[0]
                target = load_target(tfile)
                self._json({"snapshots": snapshot.list_snapshots(OUT_DIR, target["code"])})
            elif p == "/api/reports":
                self._json({"reports": list_reports()})
            elif p == "/api/report":
                name = (qs.get("name") or [""])[0]
                path = os.path.join(OUT_DIR, os.path.basename(name))
                if not os.path.isfile(path):
                    self._json({"error": "报告不存在"}, 404)
                    return
                with open(path, encoding="utf-8") as f:
                    self._json({"name": os.path.basename(name),
                                "content": f.read()})
            else:
                self._json({"error": "not found"}, 404)
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    # ---------- POST ----------
    def do_POST(self):
        p = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            data = json.loads(raw.decode("utf-8") or "{}")

            if p == "/api/open-dir":
                try:
                    os.startfile(OUT_DIR)
                    self._json({"ok": True})
                except Exception as e:
                    self._json({"error": str(e)}, 500)
                return

            target = load_target(data.get("target") or "retail-nl2sql.json")
            os.makedirs(OUT_DIR, exist_ok=True)

            if p == "/api/check":
                r = env.run_checks(target)
                md = report.env_report(r, target)
                self._write_md(f"{target['code']}-env-report.md", md)
                self._json({"type": "check", "result": r, "report":
                            f"{target['code']}-env-report.md"})

            elif p == "/api/bundle":
                r = bundle.make_bundle(target, OUT_DIR)
                self._json({"type": "bundle", "result": {
                    "steps": r["steps"],
                    "zip": os.path.basename(r["zip"]) if r.get("zip") else "",
                    "zip_mb": round(os.path.getsize(r["zip"]) / 1024 ** 2, 2)
                    if r.get("zip") else 0,
                    "file_count": (r.get("manifest") or {}).get("file_count", 0),
                }})

            elif p == "/api/smoke":
                r = smoke.run_smoke(target)
                md = report.smoke_report(r, target)
                self._write_md(f"{target['code']}-smoke-report.md", md)
                self._json({"type": "smoke", "result": r, "report":
                            f"{target['code']}-smoke-report.md"})

            elif p == "/api/eval":
                limit = data.get("limit")
                r = evaluate.run_eval(target, limit=limit)
                if not r.get("ok"):
                    self._json({"error": r.get("error")}, 400)
                    return
                md = report.eval_report(r, target)
                self._write_md(f"{target['code']}-eval-report.md", md)
                self._json({"type": "eval", "result": {
                    "summary": r["summary"],
                    "failures": r["failures"][:20],
                }, "report": f"{target['code']}-eval-report.md"})

            elif p == "/api/patrol":
                interval = int(data.get("interval", 2))
                count = int(data.get("count", 5))
                r = patrol.patrol(target, interval=interval, count=count)
                self._json({"type": "patrol", "result": r})

            elif p == "/api/snapshot":
                r = snapshot.create_snapshot(
                    target, OUT_DIR, label=data.get("label", "manual"))
                self._json({"type": "snapshot", "result": r})

            elif p == "/api/rollback":
                # 控制台只做预演：真回滚要人确认，走命令行 --apply
                r = snapshot.restore(target, OUT_DIR, data.get("to", ""), apply=False)
                self._json({"type": "rollback", "result": r})

            elif p == "/api/all":
                env_r = env.run_checks(target)
                self._write_md(f"{target['code']}-env-report.md",
                               report.env_report(env_r, target))
                smoke_r = smoke.run_smoke(target)
                self._write_md(f"{target['code']}-smoke-report.md",
                               report.smoke_report(smoke_r, target))
                eval_r = evaluate.run_eval(target)
                self._write_md(f"{target['code']}-eval-report.md",
                               report.eval_report(eval_r, target))
                bundle_r = bundle.make_bundle(target, OUT_DIR)
                md = report.delivery_report(env_r, smoke_r, eval_r, target)
                name = f"{target['code']}-delivery-report.md"
                self._write_md(name, md)
                self._json({"type": "all", "report": name, "result": {
                    "env": env_r["summary"],
                    "env_ready": env_r["ready"],
                    "smoke": {"passed": smoke_r.get("passed"),
                              "steps": smoke_r.get("steps", [])},
                    "eval": eval_r["summary"],
                    "bundle": {"zip": os.path.basename(bundle_r["zip"])
                               if bundle_r.get("zip") else ""},
                }})
            else:
                self._json({"error": "not found"}, 404)

        except Exception as e:
            tb = traceback.format_exc()[-600:]
            self._json({"error": f"{type(e).__name__}: {e}", "trace": tb}, 500)

    def _write_md(self, name, text):
        with open(os.path.join(OUT_DIR, name), "w", encoding="utf-8") as f:
            f.write(text)

    def log_message(self, fmt, *args):
        sys.stderr.write("[console] " + (fmt % args) + "\n")


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8100
    tg = list_targets()
    print("=" * 58)
    print(f"交付控制台  deliverkit")
    print(f"目标应用    {len(tg)} 个")
    for t in tg:
        print(f"            - {t['name']} ({t['code']} v{t['version']})")
    print(f"访问地址    http://127.0.0.1:{port}")
    print("=" * 58)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
