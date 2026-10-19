# -*- coding: utf-8 -*-
"""快照与回滚

上线出问题第一反应是"退回到上一版"，但现场往往没有版本管理。
这里提供最小可用的能力：把代码快照下来，需要时能比对差异并恢复。

设计上刻意保守：
- 默认只做 dry-run，列出会改哪些文件，不真写
- 真恢复前先把当前状态再备份一份
- 只碰代码，不碰 data/ logs/（数据丢了没法回滚）
"""

import fnmatch
import hashlib
import json
import os
import shutil
import time
import zipfile

OK, WARN, FAIL = "ok", "warn", "fail"

# 回滚不碰的东西：数据和日志不属于代码版本
NEVER_TOUCH = ["data/", "logs/", "out/", "__pycache__", "*.pyc",
               ".venv", ".git", "*.db", "*.db-journal"]


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _skip(rel, extra=()):
    rel = rel.replace("\\", "/")
    for p in list(NEVER_TOUCH) + list(extra):
        if fnmatch.fnmatch(rel, p) or rel.startswith(p.rstrip("/") + "/"):
            return True
    return False


def _collect(app_dir, extra_exclude=()):
    out = []
    for root, dirs, names in os.walk(app_dir):
        dirs[:] = [d for d in dirs
                   if not _skip(os.path.relpath(os.path.join(root, d), app_dir),
                                extra_exclude)]
        for n in names:
            rel = os.path.relpath(os.path.join(root, n), app_dir).replace("\\", "/")
            if not _skip(rel, extra_exclude):
                out.append(rel)
    return sorted(out)


def snapshot_dir(out_dir, code):
    return os.path.join(out_dir, "snapshots", code)


def create_snapshot(target: dict, out_dir: str, label: str = "manual") -> dict:
    """创建代码快照"""
    app_dir = target["app_dir"]
    code = target["code"]
    if not os.path.isdir(app_dir):
        return {"ok": False, "error": f"应用目录不存在：{app_dir}"}

    files = _collect(app_dir, target.get("exclude", []))
    if not files:
        return {"ok": False, "error": "没有可快照的文件"}

    sid = time.strftime("%Y%m%d-%H%M%S") + "-" + label
    dest = os.path.join(snapshot_dir(out_dir, code), sid)
    os.makedirs(dest, exist_ok=True)

    manifest = []
    total = 0
    for rel in files:
        src = os.path.join(app_dir, rel)
        dst = os.path.join(dest, "files", rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        size = os.path.getsize(dst)
        total += size
        manifest.append({"path": rel, "size": size, "sha256": _sha256(dst)})

    meta = {
        "id": sid, "code": code, "name": target.get("name", code),
        "version": target.get("version", ""), "label": label,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "file_count": len(manifest), "total_bytes": total,
        "files": manifest,
    }
    with open(os.path.join(dest, "snapshot.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    return {"ok": True, "id": sid, "path": dest,
            "file_count": len(manifest),
            "size_kb": round(total / 1024, 1)}


def list_snapshots(out_dir: str, code: str):
    base = snapshot_dir(out_dir, code)
    if not os.path.isdir(base):
        return []
    out = []
    for sid in sorted(os.listdir(base), reverse=True):
        p = os.path.join(base, sid, "snapshot.json")
        if not os.path.isfile(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                m = json.load(f)
            out.append({k: m.get(k) for k in
                        ("id", "created", "label", "version",
                         "file_count", "total_bytes")})
        except Exception:
            continue
    return out


def diff_against(target: dict, out_dir: str, snap_id: str) -> dict:
    """对比快照与当前目录，列出差异（不写任何文件）"""
    code = target["code"]
    app_dir = target["app_dir"]
    snap_path = os.path.join(snapshot_dir(out_dir, code), snap_id, "snapshot.json")
    if not os.path.isfile(snap_path):
        return {"ok": False, "error": f"快照不存在：{snap_id}"}

    with open(snap_path, encoding="utf-8") as f:
        meta = json.load(f)
    snap_files = {f["path"]: f["sha256"] for f in meta["files"]}

    cur_files = {}
    for rel in _collect(app_dir, target.get("exclude", [])):
        try:
            cur_files[rel] = _sha256(os.path.join(app_dir, rel))
        except OSError:
            continue

    added = sorted(set(cur_files) - set(snap_files))       # 快照里没有 → 恢复时删除
    removed = sorted(set(snap_files) - set(cur_files))     # 快照有但当前没有 → 恢复时新增
    changed = sorted(k for k in set(cur_files) & set(snap_files)
                     if cur_files[k] != snap_files[k])

    return {"ok": True, "snapshot": meta, "added": added,
            "removed": removed, "changed": changed,
            "total_diff": len(added) + len(removed) + len(changed)}


def restore(target: dict, out_dir: str, snap_id: str, apply: bool = False) -> dict:
    """从快照恢复。apply=False 时只返回差异，不动任何文件。"""
    d = diff_against(target, out_dir, snap_id)
    if not d.get("ok"):
        return d

    app_dir = target["app_dir"]
    snap_root = os.path.join(snapshot_dir(out_dir, target["code"]), snap_id, "files")

    if not apply:
        return {"ok": True, "dry_run": True, "diff": d,
                "message": f"预演完成：将修改 {d['total_diff']} 个文件，"
                           f"确认无误后请加 --apply 真正执行"}

    # 真恢复前先把当前状态备份，避免回滚本身造成不可逆损失
    backup = create_snapshot(target, out_dir, label=f"pre-rollback-{snap_id}")
    if not backup.get("ok"):
        return {"ok": False, "error": f"回滚前备份失败，已中止：{backup.get('error')}"}

    written, deleted = 0, 0
    try:
        for rel in d["removed"] + d["changed"]:
            src = os.path.join(snap_root, rel)
            dst = os.path.join(app_dir, rel)
            if os.path.isfile(src):
                os.makedirs(os.path.dirname(dst) or app_dir, exist_ok=True)
                shutil.copy2(src, dst)
                written += 1
        for rel in d["added"]:
            p = os.path.join(app_dir, rel)
            if os.path.isfile(p):
                os.remove(p)
                deleted += 1
    except Exception as e:
        return {"ok": False, "error": f"恢复中断：{type(e).__name__}: {e}",
                "backup": backup["id"], "written": written, "deleted": deleted}

    return {"ok": True, "dry_run": False,
            "written": written, "deleted": deleted,
            "backup": backup["id"],
            "message": f"已恢复 {written} 个文件、删除 {deleted} 个文件；"
                       f"回滚前状态已备份为 {backup['id']}"}
