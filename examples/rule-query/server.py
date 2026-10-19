# -*- coding: utf-8 -*-
"""轻量销售查询服务（规则引擎版）

和零售问数系统是同一个接口契约，但实现完全不同：
不用大模型、不用 GPU、纯关键词规则匹配。
用来验证交付工具链不绑定具体技术栈——只要 /api/health 和 /api/ask 的契约一致，
deliverkit 就能接手交付。

    python server.py          # 端口 8200
    python server.py 8201
"""

import csv
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(ROOT, "data", "sales.csv")
STATIC = os.path.join(ROOT, "static")
VERSION = "0.1.0"
ENGINE = "rule-engine-v1"

START = time.time()
STATS = {"total": 0, "ok": 0, "lat": []}


def load_rows():
    with open(CSV_PATH, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def _fmt(v):
    return f"{v:,.0f}" if abs(v) >= 1 else f"{v:.2f}"


def _group(rows, key, val):
    agg = {}
    for r in rows:
        agg[r[key]] = agg.get(r[key], 0) + _num(r[val])
    return sorted(agg.items(), key=lambda x: -x[1])


def answer(question: str) -> dict:
    """规则匹配：认出意图就答，认不出就给数据概况"""
    rows = load_rows()
    q = question.strip()
    n = len(rows)

    # 行数
    if any(k in q for k in ("多少条", "多少行", "记录数", "几行", "数据量")):
        return _ok(f"共 {n} 条记录。", ["记录数"], [[n]])

    # 按地区 / 月份分组
    for dim, label in (("地区", "地区"), ("月份", "月份")):
        if dim in q:
            metric = "订单数" if "订单" in q else "销售额"
            pairs = _group(rows, dim, metric)
            if not pairs:
                continue
            cols = [label, metric]
            body = [[k, int(v)] for k, v in pairs]
            top, bottom = pairs[0], pairs[-1]
            return _ok(
                f"{len(pairs)} 个{label}中，{top[0]} 最高（{_fmt(top[1])}），"
                f"{bottom[0]} 最低（{_fmt(bottom[1])}）。合计 {_fmt(sum(v for _, v in pairs))}。",
                cols, body)

    # 总数
    if "订单" in q and any(k in q for k in ("总", "合计", "一共", "多少")):
        total = sum(_num(r["订单数"]) for r in rows)
        return _ok(f"订单数合计 {_fmt(total)}。", ["订单数合计"], [[int(total)]])

    if "销售额" in q and any(k in q for k in ("总", "合计", "一共", "多少")):
        total = sum(_num(r["销售额"]) for r in rows)
        return _ok(f"销售额合计 {_fmt(total)}。", ["销售额合计"], [[int(total)]])

    # 认不出 → 给概况（和零售问数保持同样的兜底行为）
    regions = sorted({r["地区"] for r in rows})
    months = sorted({r["月份"] for r in rows})
    return {
        "status": "ok",
        "answer": (f"这个问题规则引擎没能识别。数据共 {n} 行，字段为 "
                   f"地区/月份/销售额/订单数，覆盖 {len(regions)} 个地区"
                   f"（{'、'.join(regions)}）、{len(months)} 个月"
                   f"（{months[0]} 至 {months[-1]}）。"
                   f"可以问：「各地区的销售额」「各月份的订单数」「销售额合计」。"),
        "columns": [], "rows": [],
        "fallback": True,
        "note": "规则引擎未匹配到意图",
    }


def _ok(text, cols, body):
    return {"status": "ok", "answer": text, "columns": cols, "rows": body}


class Handler(BaseHTTPRequestHandler):
    server_version = f"RuleQuery/{VERSION}"

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
        self._send(code, json.dumps(obj, ensure_ascii=False),
                   "application/json; charset=utf-8")

    def do_GET(self):
        p = urlparse(self.path).path
        try:
            if p in ("/", "/index.html"):
                path = os.path.join(STATIC, "index.html")
                if os.path.isfile(path):
                    with open(path, "rb") as f:
                        self._send(200, f.read(), "text/html; charset=utf-8")
                else:
                    self._send(200, "<h1>Rule Query Service</h1>"
                                    "<p>POST /api/ask</p>", "text/html; charset=utf-8")
            elif p == "/api/health":
                try:
                    rows = load_rows()
                    self._json({
                        "llm": {"ok": True, "model": ENGINE,
                                "provider": "rule-engine", "url": "-"},
                        "tables": {"sales": len(rows)},
                        "version": VERSION,
                    })
                except Exception as e:
                    self._json({"llm": {"ok": False, "error": str(e)}}, 200)
            elif p == "/api/metrics":
                lat = sorted(STATS["lat"])
                self._json({
                    "metrics": {
                        "total_requests": STATS["total"],
                        "uptime_s": int(time.time() - START),
                        "latency_ms": {
                            "p50": lat[len(lat) // 2] if lat else 0,
                            "p95": lat[int(len(lat) * 0.95)] if lat else 0,
                        },
                        "status_dist": {"ok": STATS["ok"]},
                    }
                })
            else:
                self._json({"error": "not found"}, 404)
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def do_POST(self):
        p = urlparse(self.path).path
        if p != "/api/ask":
            self._json({"error": "not found"}, 404)
            return
        t0 = time.perf_counter()
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            data = json.loads(raw.decode("utf-8") or "{}")
            q = (data.get("question") or "").strip()
            if not q:
                self._json({"error": "question 不能为空"}, 400)
                return
            res = answer(q)
            ms = int((time.perf_counter() - t0) * 1000)
            res.setdefault("fallback", False)
            res.setdefault("note", "")
            res.update({"question": q, "sql": "", "model": ENGINE,
                        "cached": False, "cache": "miss", "retries": 0,
                        "elapsed_ms": ms, "trace": {"execute_ms": ms}})
            STATS["total"] += 1
            if res["status"] == "ok":
                STATS["ok"] += 1
            STATS["lat"].append(ms)
            self._json(res)
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def log_message(self, fmt, *args):
        sys.stderr.write("[rule] " + (fmt % args) + "\n")


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8200
    print("=" * 50)
    print(f"Rule Query Service v{VERSION}  ({ENGINE})")
    print(f"数据 {CSV_PATH}")
    print(f"端口 {port}")
    print("=" * 50, flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
