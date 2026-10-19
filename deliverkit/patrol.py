# -*- coding: utf-8 -*-
"""巡检：部署完成不等于一直好用

单次冒烟只能证明"那一刻没问题"。真实运维要的是持续探测：
每隔几秒问一次，看成功率、延迟漂移、有没有间歇性故障。

这个区别很重要——很多线上问题是偶发的，跑一次全绿不代表稳定。
"""

import json
import socket
import time
import urllib.error
import urllib.request

OK, WARN, FAIL = "ok", "warn", "fail"


def _port_open(port, timeout=1.5):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def probe_once(target: dict) -> dict:
    """一次探测：够轻，可以高频跑"""
    port = target.get("port", 8000)
    base = f"http://127.0.0.1:{port}"
    ts = time.strftime("%H:%M:%S")

    if not _port_open(port):
        return {"ts": ts, "status": FAIL, "ms": 0,
                "detail": f"端口 {port} 无响应"}

    # 健康
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(
                f"{base}{target.get('health_path', '/api/health')}",
                timeout=8) as r:
            health = json.loads(r.read().decode("utf-8"))
        health_ms = int((time.perf_counter() - t) * 1000)
    except Exception as e:
        return {"ts": ts, "status": FAIL, "ms": 0,
                "detail": f"健康检查失败：{str(e)[:60]}"}

    llm = health.get("llm") or {}
    if not llm.get("ok", False):
        return {"ts": ts, "status": WARN, "ms": health_ms,
                "detail": f"服务在线但模型未就绪：{llm.get('model', '-')}"}

    # 端到端
    q = target.get("smoke_question", "一共有多少条记录")
    t = time.perf_counter()
    try:
        data = json.dumps({"question": q}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{base}/api/ask", data=data,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            res = json.loads(r.read().decode("utf-8"))
        qms = int((time.perf_counter() - t) * 1000)
    except Exception as e:
        return {"ts": ts, "status": FAIL, "ms": 0,
                "detail": f"业务接口失败：{str(e)[:60]}"}

    ok = res.get("status") == "ok" and bool(res.get("answer"))
    return {
        "ts": ts,
        "status": OK if ok else WARN,
        "ms": qms,
        "detail": f"健康 {health_ms}ms · 问数 {qms}ms · {llm.get('model', '-')}",
        "answer": (res.get("answer") or "")[:40],
    }


def patrol(target: dict, interval: int = 5, count: int = 6, on_progress=None) -> dict:
    """连续巡检并汇总

    on_progress(index, total, record) 用于命令行/网页实时显示。
    """
    records = []
    for i in range(count):
        rec = probe_once(target)
        rec["index"] = i + 1
        records.append(rec)
        if on_progress:
            on_progress(i + 1, count, rec)
        if i < count - 1:
            time.sleep(max(interval, 0))

    ok_n = sum(1 for r in records if r["status"] == OK)
    warn_n = sum(1 for r in records if r["status"] == WARN)
    fail_n = sum(1 for r in records if r["status"] == FAIL)
    lat = [r["ms"] for r in records if r["status"] in (OK, WARN) and r["ms"] > 0]

    avg = int(sum(lat) / len(lat)) if lat else 0
    mx = max(lat) if lat else 0
    mn = min(lat) if lat else 0
    jitter = ((mx - mn) / avg * 100) if avg else 0

    # 低延迟场景下百分比会失真（1ms 到 22ms 是 21 倍，但绝对值毫无意义），
    # 所以抖动要同时看比例和绝对差
    span = mx - mn
    jitter_ok = jitter < 50 or span < 100

    success_rate = ok_n / len(records) if records else 0
    verdict = ("稳定" if success_rate == 1 and jitter_ok
               else ("可接受" if success_rate >= 0.8 and span < 3000
                     else "不稳定"))

    return {
        "ok": success_rate >= 0.8,
        "records": records,
        "summary": {
            "count": len(records),
            "ok": ok_n, "warn": warn_n, "fail": fail_n,
            "success_rate": success_rate,
            "avg_ms": avg, "max_ms": mx, "min_ms": mn,
            "span_ms": span,
            "jitter_pct": round(jitter, 1),
            "verdict": verdict,
            "interval": interval,
        },
    }
