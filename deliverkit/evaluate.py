# -*- coding: utf-8 -*-
"""效果验证：跑测试集，产出一份能交给客户的准确率与延迟数据

判定只看「返回类型是否符合预期」，不做结果集比对——
这一点必须在报告里写清楚，不能让客户误以为做过逐值核对。
"""

import csv
import json
import os
import time
import urllib.request

OK, FAIL = "ok", "fail"


def _post(url, payload, timeout=240):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _pct(vals, p):
    if not vals:
        return 0
    s = sorted(vals)
    idx = min(int(round(p / 100 * (len(s) - 1))), len(s) - 1)
    return s[idx]


def run_eval(target: dict, limit: int = None, use_cache: bool = False) -> dict:
    """效果验证

    默认关闭缓存：评测要测真实推理延迟，命中缓存会让 P50 失真到 0。
    """
    port = target.get("port", 8000)
    base = f"http://127.0.0.1:{port}"
    app_dir = target["app_dir"]
    testset = os.path.join(app_dir, target.get("testset", "tests/cases.csv"))

    if not os.path.exists(testset):
        return {"ok": False, "error": f"测试集不存在：{testset}"}

    with open(testset, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if limit:
        rows = rows[:limit]

    results, failures = [], []
    lat, by_cat = [], {}
    t_all = time.perf_counter()

    for i, r in enumerate(rows, 1):
        q = (r.get("question") or "").strip()
        expect = (r.get("expect") or "sql").strip().lower()
        cat = (r.get("category") or "未分类").strip()

        t = time.perf_counter()
        try:
            res = _post(f"{base}/api/ask",
                        {"question": q, "use_cache": use_cache})
            ms = int((time.perf_counter() - t) * 1000)
            status = res.get("status", "error")
        except Exception as e:
            ms = int((time.perf_counter() - t) * 1000)
            status = "error"
            res = {"answer": f"请求失败：{e}"}

        passed = (
            (expect == "reject" and status == "reject") or
            (expect == "clarify" and status == "clarify") or
            (expect in ("sql", "ok") and status == "ok")
        )

        lat.append(ms)
        slot = by_cat.setdefault(cat, {"total": 0, "passed": 0})
        slot["total"] += 1
        slot["passed"] += 1 if passed else 0

        results.append({"id": r.get("id", ""), "question": q, "expect": expect,
                        "status": status, "pass": passed, "ms": ms})
        if not passed:
            failures.append({"id": r.get("id", ""), "question": q,
                             "expect": expect, "status": status, "ms": ms})
        print(f"  [{i:>2}/{len(rows)}] {'OK' if passed else 'NG'} "
              f"{r.get('id', ''):<7} {q[:24]:<26} {status:<8} {ms:>6} ms")

    passed_n = sum(1 for x in results if x["pass"])
    return {
        "ok": True,
        "results": results,
        "failures": failures,
        "summary": {
            "total": len(results),
            "passed": passed_n,
            "accuracy": passed_n / max(len(results), 1),
            "p50_ms": _pct(lat, 50),
            "p95_ms": _pct(lat, 95),
            "avg_ms": int(sum(lat) / len(lat)) if lat else 0,
            "wall_s": round(time.perf_counter() - t_all, 1),
            "by_category": by_cat,
        },
    }
