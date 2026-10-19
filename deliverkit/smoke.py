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


def _get_path(obj, path, default=None):
    """按点路径取值：health.llm.ok -> obj['health']['llm']['ok']"""
    cur = obj
    for k in (path or "").split("."):
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        else:
            return default
    return cur


def _judge(obj, rule):
    """按声明式规则判定响应是否正常

    不同应用的返回结构千差万别，硬编码 llm.ok 只能适配一类服务。
    规则写成 {"field": "status", "equals": "ok"} 这种形式就能覆盖大多数情况。
    """
    if not rule:
        return None
    v = _get_path(obj, rule.get("field", ""))
    if "equals" in rule:
        return v == rule["equals"]
    if "non_empty" in rule:
        return bool(v)
    if "gte" in rule:
        try:
            return float(v) >= float(rule["gte"])
        except (TypeError, ValueError):
            return False
    return bool(v)


def run_smoke(target: dict, timeout_s: int = 90) -> dict:
    app_dir = target["app_dir"]
    port = target.get("port", 8000)
    base = f"http://127.0.0.1:{port}"
    steps = []
    proc = None
    started_by_us = False

    # 健康检查路径：优先用 health.path，兼容旧的 health_path
    hcfg = target.get("health") or {}
    health_path = hcfg.get("path") or target.get("health_path", "/api/health")
    health_rule = hcfg.get("ok_when")

    # 1) 端口与既有服务
    if _port_open(port):
        try:
            _get(f"{base}{health_path}", timeout=5)
            steps.append({"name": "服务探测", "status": OK, "ms": "—",
                          "detail": f"{port} 已有实例在运行，直接复用"})
        except Exception as e:
            steps.append({"name": "服务探测", "status": FAIL, "ms": "—",
                          "detail": f"{port} 被占用但健康检查失败：{e}"})
            return {"passed": False, "steps": steps,
                    "error": f"端口 {port} 被未知进程占用"}
    else:
        # 2) 启动服务：优先用 target 声明的 launch 命令
        launch = target.get("launch")
        if launch:
            # 应用可能需要特定的解释器（比如装了 FastAPI 的那个 venv），
            # 不能用跑工具的这个解释器硬套
            py = launch.get("python") or sys.executable
            cmd = [str(c).format(python=py, port=port, app_dir=app_dir)
                   for c in launch.get("cmd", [])]
            cwd = os.path.join(app_dir, launch.get("cwd") or ".")
            entry_desc = " ".join(cmd[1:])[:80]
        else:
            entry = os.path.join(app_dir, target.get("entry", "server.py"))
            if not os.path.exists(entry):
                steps.append({"name": "启动服务", "status": FAIL, "ms": "—",
                              "detail": f"入口文件不存在：{entry}"})
                return {"passed": False, "steps": steps, "error": "入口文件缺失"}
            cmd = [sys.executable, "-u", entry, str(port)]
            cwd = app_dir
            entry_desc = target.get("entry", "server.py")

        t0 = time.perf_counter()
        try:
            proc = subprocess.Popen(
                cmd, cwd=cwd,
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
                      "detail": f"监听 {port}（{entry_desc}）"})

    try:
        # 4) 健康检查
        t = time.perf_counter()
        try:
            status, health = _get(f"{base}{health_path}")
            hms = int((time.perf_counter() - t) * 1000)
            if health_rule:
                # 声明式判定：不同应用的返回结构不同，不能硬编码 llm.ok
                health_ok = bool(_judge(health, health_rule))
                shown = _get_path(health, health_rule.get("field", ""), "-")
                detail = f"HTTP {status}，{health_rule.get('field')}={shown}"
            else:
                llm = (health.get("llm") or {})
                health_ok = llm.get("ok", False)
                detail = (f"HTTP {status}，模型 {llm.get('model', '-')} "
                          f"{'就绪' if health_ok else '未就绪'}")
            steps.append({"name": "健康检查", "status": OK if health_ok else WARN,
                          "ms": f"{hms}ms", "detail": detail})
        except Exception as e:
            steps.append({"name": "健康检查", "status": FAIL, "ms": "—",
                          "detail": str(e)[:120]})
            return {"passed": False, "steps": steps, "error": str(e)}

        # 5) 端到端业务调用
        scfg = target.get("smoke") or {}
        t = time.perf_counter()
        try:
            if scfg:
                spath = scfg.get("path", "")
                method = (scfg.get("method") or "POST").upper()
                body = scfg.get("body") or {}
                if method == "GET":
                    data = _get(f"{base}{spath}", timeout=180)[1]
                else:
                    body = json.loads(json.dumps(body))   # 深拷贝，避免改动配置
                    data = _post(f"{base}{spath}", body)
                ams = int((time.perf_counter() - t) * 1000)
                ok = _judge(data, scfg.get("ok_when")) if scfg.get("ok_when") else bool(data)
                brief = json.dumps(data, ensure_ascii=False)[:110]
                steps.append({
                    "name": scfg.get("name", "业务调用"),
                    "status": OK if ok else FAIL,
                    "ms": f"{ams}ms",
                    "detail": f"{method} {spath} → {brief}",
                })
                sample = {"question": f"{method} {spath}",
                          "answer": brief, "ms": ams}
            else:
                q = target.get("smoke_question", "一共有多少条记录")
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
