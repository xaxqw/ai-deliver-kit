# -*- coding: utf-8 -*-
"""部署验证：把应用拉起来，走一遍真实调用，证明"装完能用"

不是健康检查就完事——健康检查通过但业务接口挂掉的情况太多了。
这里要求完整走一次问数，拿到非空结果才算通过。
"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

OK, WARN, FAIL = "ok", "warn", "fail"


def _port_open(port, timeout=1):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def _get(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def _post(url, payload, timeout=180):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _kill_tree(pid):
    """Windows 上必须连子进程一起杀，否则 python 会变成孤儿进程占着端口"""
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, timeout=10)
        else:
            os.killpg(os.getpgid(pid), 9)
    except Exception:
        pass


def run_smoke(target: dict, timeout_s: int = 90) -> dict:
    app_dir = target["app_dir"]
    port = target.get("port", 8000)
    base = f"http://127.0.0.1:{port}"
    steps = []
    proc = None
    started_by_us = False

    # 1) 端口与既有服务
    if _port_open(port):
        try:
            _get(f"{base}{target.get('health_path', '/api/health')}", timeout=5)
            steps.append({"name": "服务探测", "status": OK, "ms": "—",
                          "detail": f"{port} 已有实例在运行，直接复用"})
        except Exception as e:
            steps.append({"name": "服务探测", "status": FAIL, "ms": "—",
                          "detail": f"{port} 被占用但健康检查失败：{e}"})
            return {"passed": False, "steps": steps,
                    "error": f"端口 {port} 被未知进程占用"}
    else:
        # 2) 启动服务
        entry = os.path.join(app_dir, target.get("entry", "server.py"))
        if not os.path.exists(entry):
            steps.append({"name": "启动服务", "status": FAIL, "ms": "—",
                          "detail": f"入口文件不存在：{entry}"})
            return {"passed": False, "steps": steps, "error": "入口文件缺失"}

        t0 = time.perf_counter()
        try:
            proc = subprocess.Popen(
                [sys.executable, "-u", entry, str(port)],
                cwd=app_dir,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
            )
            started_by_us = True
        except Exception as e:
            steps.append({"name": "启动服务", "status": FAIL, "ms": "—",
                          "detail": f"进程启动失败：{e}"})
            return {"passed": False, "steps": steps, "error": str(e)}

        # 3) 等待端口
        ready = False
        for _ in range(timeout_s):
            if proc.poll() is not None:
                break
            if _port_open(port):
                ready = True
                break
            time.sleep(1)
        ms = int((time.perf_counter() - t0) * 1000)
        if not ready:
            out = ""
            try:
                proc.kill()
                out = (proc.stdout.read() or "")[-800:] if proc.stdout else ""
            except Exception:
                pass
            steps.append({"name": "启动服务", "status": FAIL, "ms": f"{ms}ms",
                          "detail": f"{timeout_s}s 内未监听 {port}"})
            return {"passed": False, "steps": steps,
                    "error": f"服务未能启动\n{out}"}
        steps.append({"name": "启动服务", "status": OK, "ms": f"{ms}ms",
                      "detail": f"监听 {port}，冷启动耗时含模型加载"})

    try:
        # 4) 健康检查
        t = time.perf_counter()
        try:
            status, health = _get(f"{base}{target.get('health_path', '/api/health')}")
            hms = int((time.perf_counter() - t) * 1000)
            llm = (health.get("llm") or {})
            llm_ok = llm.get("ok", False)
            steps.append({"name": "健康检查", "status": OK if llm_ok else WARN,
                          "ms": f"{hms}ms",
                          "detail": f"HTTP {status}，模型 "
                                    f"{llm.get('model', '-')} "
                                    f"{'就绪' if llm_ok else '未就绪'}"})
        except Exception as e:
            steps.append({"name": "健康检查", "status": FAIL, "ms": "—",
                          "detail": str(e)[:120]})
            return {"passed": False, "steps": steps, "error": str(e)}

        # 5) 端到端问数
        q = target.get("smoke_question", "一共有多少条记录")
        t = time.perf_counter()
        try:
            res = _post(f"{base}/api/ask", {"question": q, "use_cache": False})
            ams = int((time.perf_counter() - t) * 1000)
            ok = res.get("status") == "ok" and bool(res.get("answer"))
            steps.append({"name": "端到端问数", "status": OK if ok else FAIL,
                          "ms": f"{ams}ms",
                          "detail": f"「{q}」→ {(res.get('answer') or '')[:40]}"})
            sample = {"question": q, "answer": (res.get("answer") or "")[:200],
                      "ms": ams}
        except Exception as e:
            steps.append({"name": "端到端问数", "status": FAIL, "ms": "—",
                          "detail": str(e)[:120]})
            return {"passed": False, "steps": steps, "error": str(e)}

        # 6) 指标接口
        try:
            _get(f"{base}/api/metrics", timeout=8)
            steps.append({"name": "指标接口", "status": OK, "ms": "—",
                          "detail": "/api/metrics 可访问，支持日常巡检"})
        except Exception as e:
            steps.append({"name": "指标接口", "status": WARN, "ms": "—",
                          "detail": f"不可访问：{str(e)[:60]}"})

        passed = ok
        return {"passed": passed, "steps": steps, "sample": sample,
                "detail": f"{sum(1 for s in steps if s['status'] == OK)}/"
                          f"{len(steps)} 项通过"}

    finally:
        if started_by_us and proc:
            _kill_tree(proc.pid)
            time.sleep(0.8)
