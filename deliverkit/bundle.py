# -*- coding: utf-8 -*-
"""离线部署包：把应用打成能拷到客户机器上直接装的东西

内网客户装不了 pip、拉不了模型，所以包里必须包含：
应用代码 + 文件清单（含哈希，防传输损坏）+ 安装脚本 + 模型清单。

数据库不入包（几十 MB 且可由脚本重建），安装脚本会提示先跑初始化。
"""

import fnmatch
import hashlib
import json
import os
import shutil
import time
import zipfile

OK, WARN, FAIL = "ok", "warn", "fail"

# 体积大且能重建的东西一律不入包
FORCE_EXCLUDE = ["data/*.db", "*.db", "*.db-journal", "__pycache__", "*.pyc",
                 ".venv", ".git", "logs", "data/uploads"]

INSTALL_BAT = r"""@echo off
setlocal
title Install - {name}
cd /d "%~dp0"

echo.
echo   ==========================================
echo     Install: {name}  v{version}
echo   ==========================================
echo.

echo   [1/4] Check Python ...
python --version >nul 2>&1
if errorlevel 1 (
    echo         Python not found. Install Python {py_min}+ and retry.
    pause
    exit /b 1
)
python --version

echo   [2/4] Install dependencies ...
if exist wheels (
    if exist requirements.txt (
        python -m pip install --no-index --find-links wheels -r requirements.txt
    ) else (
        echo         no requirements.txt, skip
    )
) else (
    echo         no offline wheels, using system packages
)

echo   [3/4] Check models ...
echo         required:
{model_lines}
for %%m in ({model_args}) do (
    echo         pulling %%m ...
    ollama pull %%m
)

echo   [4/4] Build dataset and start ...
cd app
if not exist data (
    python scripts\gen_data.py
)
start "App Server - keep this window" /min python -u {entry} {port}
timeout /t 5 /nobreak >nul
start "" http://127.0.0.1:{port}

echo.
echo   Done. If the page does not open, check the minimized server window.
echo.
pause
"""

INSTALL_SH = """#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

echo "=========================================="
echo "  Install: {name}  v{version}"
echo "=========================================="

echo "[1/4] Check Python"
PY=$(command -v python3 || command -v python)
$PY --version

echo "[2/4] Install dependencies"
if [ -d wheels ] && [ -f requirements.txt ]; then
  $PY -m pip install --no-index --find-links wheels -r requirements.txt
else
  echo "    no offline wheels, skip"
fi

echo "[3/4] Check models"
{model_sh_lines}
for m in {model_args}; do
  echo "    pulling $m ..."
  ollama pull "$m" || true
done

echo "[4/4] Build dataset and start"
cd app
[ -d data ] || $PY scripts/gen_data.py
nohup $PY -u {entry} {port} > server.log 2>&1 &
sleep 5
echo "    started, open http://127.0.0.1:{port}"
"""


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _excluded(rel, patterns):
    rel = rel.replace("\\", "/")
    for p in patterns:
        if fnmatch.fnmatch(rel, p):
            return True
        if rel == p or rel.startswith(p.rstrip("/") + "/"):
            return True
    return False


def _collect(app_dir, patterns):
    out = []
    for root, dirs, names in os.walk(app_dir):
        dirs[:] = [d for d in dirs
                   if not _excluded(os.path.relpath(os.path.join(root, d), app_dir),
                                    patterns)]
        for n in names:
            full = os.path.join(root, n)
            rel = os.path.relpath(full, app_dir)
            if _excluded(rel, patterns):
                continue
            out.append(rel)
    return sorted(out)


def make_bundle(target: dict, out_dir: str):
    steps = []
    app_dir = target["app_dir"]
    code = target["code"]
    name = target.get("name", code)
    version = target.get("version", "0.0.0")

    if not os.path.isdir(app_dir):
        return {"steps": [{"name": "检查目录", "status": FAIL,
                           "detail": f"应用目录不存在：{app_dir}"}],
                "zip": None}

    patterns = list(target.get("exclude", [])) + FORCE_EXCLUDE
    files = _collect(app_dir, patterns)
    if not files:
        return {"steps": [{"name": "收集文件", "status": FAIL,
                           "detail": "没有可打包的文件"}],
                "zip": None}

    stage_name = f"{code}-bundlev{version}"
    stage = os.path.join(out_dir, stage_name)
    if os.path.isdir(stage):
        try:
            shutil.rmtree(stage)
        except Exception:
            # 某些环境不允许删除目录（如回收站不可用），换个目录名继续
            stage = os.path.join(out_dir, f"{stage_name}-{int(time.time())}")
    os.makedirs(stage, exist_ok=True)

    # 1) 复制文件
    total = 0
    manifest_files = []
    for rel in files:
        src = os.path.join(app_dir, rel)
        dst = os.path.join(stage, "app", rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        size = os.path.getsize(dst)
        total += size
        manifest_files.append({"path": rel, "size": size, "sha256": _sha256(dst)})
    steps.append({"name": "收集文件", "status": OK,
                  "detail": f"{len(files)} 个文件，{total / 1024 ** 2:.1f} MB"})

    # 2) 依赖清单（有 requirements.txt 就带上，离线装包需要）
    has_req = os.path.exists(os.path.join(app_dir, "requirements.txt"))
    steps.append({
        "name": "依赖清单", "status": OK if has_req else WARN,
        "detail": "requirements.txt 已包含" if has_req
                  else "未提供 requirements.txt，安装时用系统已有包（本项目零第三方依赖）",
    })

    # 3) 模型清单
    models = target.get("models", [])
    model_lines = "\n".join(
        f"echo           - {m['name']} ({m.get('role', '')}, {m.get('size_gb', '?')} GB)"
        for m in models) or "echo           - none"
    model_sh_lines = "\n".join(
        f'echo "    - {m["name"]} ({m.get("role", "")})"' for m in models) or 'echo "    - none"'
    model_args = " ".join(m["name"] for m in models)

    # 4) 安装脚本
    ctx = dict(name=name, version=version, py_min=target.get("python_min", "3.10"),
               port=target.get("port", 8000), entry=target.get("entry", "server.py"),
               model_lines=model_lines, model_sh_lines=model_sh_lines,
               model_args=model_args)
    bat = INSTALL_BAT.format(**ctx).replace("\r\n", "\n").replace("\n", "\r\n")
    with open(os.path.join(stage, "install.bat"), "wb") as f:
        f.write(bat.encode("ascii", errors="replace"))
    sh = INSTALL_SH.format(**ctx).replace("\r\n", "\n")
    with open(os.path.join(stage, "install.sh"), "w", encoding="utf-8", newline="\n") as f:
        f.write(sh)
    steps.append({"name": "安装脚本", "status": OK,
                  "detail": "install.bat（Windows）/ install.sh（Linux）"})

    # 5) 清单
    manifest = {
        "code": code, "name": name, "version": version,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "python_min": target.get("python_min", "3.10"),
        "port": target.get("port", 8000),
        "entry": target.get("entry", "server.py"),
        "models": models,
        "data_files": target.get("data_files", []),
        "file_count": len(manifest_files),
        "total_bytes": total,
        "files": manifest_files,
        "note": "data/*.db 未打包，安装时由 scripts/gen_data.py 重建",
    }
    with open(os.path.join(stage, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    steps.append({"name": "文件清单", "status": OK,
                  "detail": f"manifest.json 含 {len(manifest_files)} 个文件的 SHA256"})

    # 6) 打包
    zip_path = os.path.join(out_dir, f"{stage_name}.zip")
    if os.path.exists(zip_path):
        try:
            os.remove(zip_path)
        except OSError:
            zip_path = os.path.join(out_dir, f"{stage_name}-{int(time.time())}.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, names in os.walk(stage):
            for n in names:
                full = os.path.join(root, n)
                z.write(full, os.path.relpath(full, stage))
    size_mb = os.path.getsize(zip_path) / 1024 ** 2
    steps.append({"name": "打包压缩", "status": OK,
                  "detail": f"{os.path.basename(zip_path)}，{size_mb:.1f} MB"})

    return {"steps": steps, "zip": zip_path, "manifest": manifest, "stage": stage}
