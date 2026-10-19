# -*- coding: utf-8 -*-
"""环境体检：交付前先把客户环境摸清楚

12 项检查，每项给出 通过 / 警告 / 不通过 + 处理建议。
目标不是"跑个分"，而是让实施人员到现场 30 秒内知道这台机器能不能装。
"""

import ctypes
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import urllib.request

OK, WARN, FAIL = "ok", "warn", "fail"


def _r(name, status, detail, advice="", key=""):
    return {"name": name, "status": status, "detail": detail,
            "advice": advice, "key": key or name}


# ---------------- 单项检查 ----------------

def check_os():
    osname = platform.system()
    ver = platform.release()
    machine = platform.machine()
    support = osname in ("Windows", "Linux", "Darwin")
    return _r("操作系统", OK if support else WARN,
              f"{osname} {ver} ({machine})",
              "" if support else "未在目标平台验证过，需现场确认",
              key="os")


def check_python(min_ver="3.10"):
    cur = ".".join(map(str, sys.version_info[:3]))
    major, minor = sys.version_info[:2]

    def parse(v):
        p = v.split(".")
        return int(p[0]), int(p[1]) if len(p) > 1 else 0

    need = parse(min_ver)
    ok = (major, minor) >= need
    return _r("Python 版本", OK if ok else FAIL, f"当前 {cur}，要求 >= {min_ver}",
              "" if ok else f"需安装 Python {min_ver} 及以上", key="python")


def check_disk(path, min_gb=5):
    try:
        u = shutil.disk_usage(path)
        free_gb = u.free / 1024 ** 3
        total_gb = u.total / 1024 ** 3
        status = OK if free_gb >= min_gb else (WARN if free_gb >= min_gb * 0.5 else FAIL)
        return _r("磁盘空间", status,
                  f"{path} 剩余 {free_gb:.1f} GB / 共 {total_gb:.0f} GB（要求 {min_gb} GB）",
                  "" if status == OK else "清理空间或更换安装盘", key="disk")
    except Exception as e:
        return _r("磁盘空间", WARN, f"无法读取：{e}", key="disk")


def check_memory(min_gb=8):
    total_gb = 0.0
    try:
        if platform.system() == "Windows":
            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            ms = MS()
            ms.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
            total_gb = ms.ullTotalPhys / 1024 ** 3
        else:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal"):
                        total_gb = int(line.split()[1]) / 1024 ** 2
                        break
    except Exception:
        return _r("内存", WARN, "无法读取", key="memory")

    status = OK if total_gb >= min_gb else WARN
    return _r("内存", status, f"{total_gb:.1f} GB（建议 >= {min_gb} GB）",
              "" if status == OK else "内存偏小，大模型推理可能变慢", key="memory")


def check_gpu(require=False):
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8,
        )
        if out.returncode != 0 or not out.stdout.strip():
            raise RuntimeError("nvidia-smi 无输出")
        line = out.stdout.strip().splitlines()[0]
        parts = [p.strip() for p in line.split(",")]
        name = parts[0]
        total = float(parts[1]) / 1024
        free = float(parts[2]) / 1024
        status = OK if total >= 5.5 else WARN
        detail = f"{name}，共 {total:.1f} GB，可用 {free:.1f} GB"
        if total >= 5.5 and free < total * 0.15:
            detail += "（大部分已被运行中的模型占用，属正常）"
        advice = "" if total >= 5.5 else "显存偏小，7B 模型可能溢出到 CPU"
        return _r("GPU / 显存", status, detail, advice, key="gpu")
    except Exception as e:
        status = FAIL if require else WARN
        return _r("GPU / 显存", status, "未检测到 NVIDIA GPU",
                  "需要 GPU 才能本地推理" if require else "将回退到 CPU，速度会明显下降",
                  key="gpu")


def check_port(port=8000):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    try:
        s.bind(("127.0.0.1", port))
        free = True
    except OSError:
        free = False
    finally:
        s.close()

    if free:
        return _r("端口占用", OK, f"{port} 可用", key="port")

    # 端口被占，看看是不是自己的服务已经在跑
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=3)
        return _r("端口占用", OK, f"{port} 已被本应用占用（服务在运行）", key="port")
    except Exception:
        return _r("端口占用", FAIL, f"{port} 被其他程序占用",
                  f"释放端口或改用其他端口", key="port")


def check_internet():
    for host, port in (("223.5.5.5", 53), ("114.114.114.114", 53)):
        try:
            socket.create_connection((host, port), timeout=3).close()
            return _r("外网连通", OK, f"可访问外网（{host}:{port}）",
                      "在线部署可直接拉取依赖", key="net")
        except Exception:
            continue
    return _r("外网连通", WARN, "无法访问外网，判断为内网/离线环境",
              "需使用离线部署包：提前打包依赖与模型", key="net")


def check_ollama(url="http://localhost:11434"):
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=6) as r:
            data = json.loads(r.read().decode("utf-8"))
        names = [m.get("name", "") for m in data.get("models", [])]
        return _r("模型服务", OK, f"Ollama 正常，已加载 {len(names)} 个模型",
                  key="ollama"), names
    except Exception as e:
        return _r("模型服务", FAIL, f"无法连接 {url}",
                  "启动 Ollama 服务后重试", key="ollama"), []


def _model_present(name, installed):
    """Ollama 返回的名字可能带 :latest，配置里通常不带，要能对上"""
    if name in installed or f"{name}:latest" in installed:
        return True
    if ":" not in name:          # 配置没写版本号时按基础名匹配
        return any(i.split(":")[0] == name for i in installed)
    return False


def check_models(required, installed):
    if not required:
        return _r("模型就绪", OK, "该应用不依赖本地模型", key="models")
    missing = [m["name"] for m in required if not _model_present(m["name"], installed)]
    total_gb = sum(m.get("size_gb", 0) for m in required)
    if not missing:
        return _r("模型就绪", OK,
                  f"{len(required)} 个模型齐全，约 {total_gb:.1f} GB", key="models")
    size = sum(m.get("size_gb", 0) for m in required if m["name"] in missing)
    return _r("模型就绪", FAIL,
              f"缺少 {len(missing)} 个：{', '.join(missing)}（约 {size:.1f} GB）",
              "执行 ollama pull <模型名>，或从离线包导入", key="models")


def check_app_dir(app_dir):
    if not os.path.isdir(app_dir):
        return _r("应用目录", FAIL, f"不存在：{app_dir}", "检查部署路径", key="appdir")
    return _r("应用目录", OK, app_dir, key="appdir")


def check_data_files(app_dir, files):
    if not files:
        return _r("数据文件", OK, "该应用无预置数据", key="data")
    missing = []
    total_mb = 0
    for rel in files:
        p = os.path.join(app_dir, rel)
        if os.path.exists(p):
            total_mb += os.path.getsize(p) / 1024 ** 2
        else:
            missing.append(rel)
    if missing:
        return _r("数据文件", FAIL, f"缺少 {len(missing)} 个：{', '.join(missing)}",
                  "执行数据初始化脚本，或从备份恢复", key="data")
    return _r("数据文件", OK, f"{len(files)} 个文件，共 {total_mb:.0f} MB", key="data")


# ---------------- 汇总 ----------------

def run_checks(target: dict) -> dict:
    checks = []
    checks.append(check_os())
    checks.append(check_python(target.get("python_min", "3.10")))
    checks.append(check_disk(target["app_dir"], target.get("min_free_gb", 5)))
    checks.append(check_memory())
    checks.append(check_gpu(target.get("require_gpu", False)))
    checks.append(check_port(target.get("port", 8000)))
    checks.append(check_internet())

    ollama_r, installed = check_ollama()
    if not target.get("models"):
        # 该应用不依赖本地模型（如规则引擎），Ollama 在不在都不影响交付
        checks.append(_r("模型服务", OK, "该应用不依赖本地模型，跳过检查",
                         key="ollama"))
        checks.append(check_models([], []))
    else:
        checks.append(ollama_r)
        checks.append(check_models(target.get("models", []), installed))

    checks.append(check_app_dir(target["app_dir"]))
    checks.append(check_data_files(target["app_dir"], target.get("data_files", [])))

    fails = sum(1 for c in checks if c["status"] == FAIL)
    warns = sum(1 for c in checks if c["status"] == WARN)
    return {
        "target": target.get("name", ""),
        "checks": checks,
        "summary": {"total": len(checks), "ok": len(checks) - fails - warns,
                    "warn": warns, "fail": fails},
        "ready": fails == 0,
    }
