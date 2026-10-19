# -*- coding: utf-8 -*-
"""命令行入口

    python -m deliverkit check  --target targets/retail-nl2sql.json
    python -m deliverkit smoke  --target targets/retail-nl2sql.json
    python -m deliverkit eval   --target targets/retail-nl2sql.json
    python -m deliverkit bundle --target targets/retail-nl2sql.json
    python -m deliverkit all    --target targets/retail-nl2sql.json
"""

import argparse
import json
import os
import sys
import time
import unicodedata

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    except Exception:
        pass

from . import env, evaluate, patrol, report, snapshot, smoke  # noqa: E402

KIT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICON = {"ok": "通过", "warn": "警告", "fail": "失败"}


def _w(s):
    """东亚字符算两个宽度，用来对齐控制台表格"""
    n = 0
    for ch in s:
        n += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return n


def pad(s, width):
    s = str(s)
    return s + " " * max(0, width - _w(s))


def line(width=78, ch="-"):
    print(ch * width)


def load_target(path):
    with open(path, encoding="utf-8") as f:
        t = json.load(f)
    p = t["app_dir"]
    if not os.path.isabs(p):
        # 支持相对路径（相对工具包根目录），换台机器 clone 下来也能跑
        p = os.path.join(KIT_DIR, p)
    t["app_dir"] = os.path.abspath(p)
    return t


def out_dir(override=None):
    d = override or os.path.join(KIT_DIR, "out")
    os.makedirs(d, exist_ok=True)
    return d


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"  报告已生成：{path}")


# ---------------- 各命令 ----------------

def cmd_check(target, out):
    print(f"\n环境检查：{target['name']}  ({target['code']} v{target['version']})")
    line()
    r = env.run_checks(target)
    for c in r["checks"]:
        print(f"  {pad(ICON[c['status']], 6)}{pad(c['name'], 14)}{c['detail']}")
        if c["advice"] and c["status"] != "ok":
            print(f"        {'':14}建议：{c['advice']}")
    line()
    s = r["summary"]
    print(f"  {s['ok']} 通过 / {s['warn']} 警告 / {s['fail']} 失败"
          f"  ->  {'环境就绪，可以部署' if r['ready'] else '存在阻塞项，不可部署'}\n")
    write(os.path.join(out, f"{target['code']}-env-report.md"),
          report.env_report(r, target))
    return r


def cmd_smoke(target, out):
    print(f"\n部署验证：{target['name']}")
    print(f"  应用目录 {target['app_dir']}")
    print(f"  端口 {target.get('port', 8000)}，最多等待 90 秒（含模型加载）")
    line()
    r = smoke.run_smoke(target)
    for s in r["steps"]:
        print(f"  {pad(ICON[s['status']], 6)}{pad(s['name'], 14)}"
              f"{pad(s.get('ms', '—'), 9)}{s.get('detail', '')}")
    line()
    print(f"  {'部署成功' if r['passed'] else '部署未通过'}\n")
    write(os.path.join(out, f"{target['code']}-smoke-report.md"),
          report.smoke_report(r, target))
    return r


def cmd_eval(target, out, limit=None):
    print(f"\n效果验证：{target['name']}")
    print(f"  测试集 {target.get('testset', 'tests/cases.csv')}")
    line()
    r = evaluate.run_eval(target, limit=limit)
    if not r.get("ok"):
        print(f"  失败：{r.get('error')}")
        return r
    s = r["summary"]
    line()
    for cat, v in s["by_category"].items():
        print(f"  {pad(cat, 10)}{v['passed']:>3}/{v['total']:<3}"
              f"{v['passed'] / max(v['total'], 1):>8.1%}")
    line()
    print(f"  准确率 {s['accuracy']:.1%}   P50 {s['p50_ms'] / 1000:.2f}s   "
          f"P95 {s['p95_ms'] / 1000:.2f}s   总耗时 {s['wall_s']}s\n")
    write(os.path.join(out, f"{target['code']}-eval-report.md"),
          report.eval_report(r, target))
    return r


def cmd_bundle(target, out):
    from . import bundle
    print(f"\n制作离线部署包：{target['name']}")
    line()
    r = bundle.make_bundle(target, out)
    for s in r["steps"]:
        print(f"  {pad(ICON[s['status']], 6)}{pad(s['name'], 16)}{s['detail']}")
    line()
    if r.get("zip"):
        size_mb = os.path.getsize(r["zip"]) / 1024 ** 2
        print(f"  部署包：{r['zip']}  ({size_mb:.1f} MB)\n")
    return r


def cmd_all(target, out):
    env_r = cmd_check(target, out)
    smoke_r = cmd_smoke(target, out)
    eval_r = cmd_eval(target, out)
    bundle_r = cmd_bundle(target, out)

    print("\n" + "=" * 78)
    md = report.delivery_report(env_r, smoke_r, eval_r, target)
    p = os.path.join(out, f"{target['code']}-delivery-report.md")
    write(p, md)
    print("=" * 78 + "\n")
    return p


def cmd_menu(target_path=None, out_override=None):
    """交互菜单：给桌面快捷方式用，双击就能操作，不用记命令"""
    import glob

    if target_path:
        tp = target_path
    else:
        found = sorted(glob.glob(os.path.join(KIT_DIR, "targets", "*.json")))
        if not found:
            print("未找到 targets/*.json，请先添加目标应用配置")
            return
        if len(found) == 1:
            tp = found[0]
        else:
            print("\n可用目标应用：")
            for i, f in enumerate(found, 1):
                print(f"  {i}. {os.path.basename(f)}")
            pick = input("请选择编号：").strip()
            try:
                tp = found[int(pick) - 1]
            except (ValueError, IndexError):
                print("选择无效")
                return

    target = load_target(tp)
    out = out_dir(out_override)

    menu = f"""
  ============================================================
    AI 应用交付工具包
    目标应用：{target['name']}
    版本：{target['code']} v{target['version']}
  ============================================================

    1. 环境体检          12 项检查，判断这台机器能不能装
    2. 制作离线部署包     打包 + 安装脚本 + 文件哈希
    3. 部署验证          拉起来跑一次真实调用
    4. 效果评测          跑测试集，出准确率与延迟
    5. 完整交付流程       1-4 全跑，再汇总验收报告

    0. 退出
"""
    while True:
        print(menu)
        try:
            c = input("  请选择 [0-5]：").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if c == "0" or c.lower() == "q":
            break
        elif c == "1":
            cmd_check(target, out)
        elif c == "2":
            cmd_bundle(target, out)
        elif c == "3":
            cmd_smoke(target, out)
        elif c == "4":
            cmd_eval(target, out)
        elif c == "5":
            cmd_all(target, out)
        else:
            print("  无效选择，请输入 0-5")

        try:
            input("\n  按回车返回菜单...")
        except (EOFError, KeyboardInterrupt):
            break

    print("\n  已退出。报告在 out/ 目录下。")


def cmd_patrol(target, out, interval=5, count=6):
    print(f"\n持续巡检：{target['name']}")
    print(f"  每 {interval} 秒一次，共 {count} 次（约 {interval * (count - 1)} 秒）")
    line()

    def on_prog(i, total, rec):
        print(f"  [{i}/{total}] {pad(ICON[rec['status']], 6)}{rec['ts']}  {rec['detail']}")

    r = patrol.patrol(target, interval=interval, count=count, on_progress=on_prog)
    s = r["summary"]
    line()
    print(f"  成功率 {s['success_rate']:.0%}   平均 {s['avg_ms']}ms   "
          f"最大 {s['max_ms']}ms   波动 {s['jitter_pct']}%   ->  {s['verdict']}\n")
    if s["fail"]:
        print(f"  异常 {s['fail']} 次，明细：")
        for rec in r["records"]:
            if rec["status"] == "fail":
                print(f"    {rec['ts']}  {rec['detail']}")
        print()
    return r


def cmd_snapshot(target, out, label="manual"):
    r = snapshot.create_snapshot(target, out, label)
    print()
    if not r.get("ok"):
        print(f"  快照失败：{r.get('error')}")
        return r
    print(f"  快照已创建：{r['id']}")
    print(f"  {r['file_count']} 个文件，{r['size_kb']} KB")
    print(f"  路径：{r['path']}\n")
    return r


def cmd_snapshots(target, out):
    items = snapshot.list_snapshots(out, target["code"])
    print(f"\n{target['name']} 的快照（{len(items)} 个）")
    line()
    if not items:
        print("  还没有快照，先执行 snapshot 命令\n")
        return items
    for it in items:
        print(f"  {pad(it['id'], 26)}{pad(it['created'], 22)}"
              f"{it['file_count']:>3} 文件")
    print()
    return items


def cmd_rollback(target, out, snap_id, apply=False):
    mode = "执行回滚" if apply else "预演回滚（不写文件）"
    print(f"\n{mode}：{target['name']}  ->  {snap_id}")
    line()
    r = snapshot.restore(target, out, snap_id, apply=apply)
    if not r.get("ok"):
        print(f"  失败：{r.get('error')}\n")
        return r

    d = r.get("diff") or {}
    if d:
        print(f"  与当前目录的差异：共 {d['total_diff']} 个文件")
        for tag, key in (("修改", "changed"), ("新增", "removed"), ("删除", "added")):
            for f in d.get(key, [])[:10]:
                print(f"    {tag}  {f}")
            if len(d.get(key, [])) > 10:
                print(f"    {tag}  ... 另有 {len(d[key]) - 10} 个")
    print(f"\n  {r.get('message', '')}")
    if not apply:
        print(f"  确认后加 --apply 真正执行（会自动先备份当前状态）")
    print()
    return r


def main():
    ap = argparse.ArgumentParser(
        prog="deliverkit",
        description="AI 应用交付工具包：环境体检 / 部署验证 / 效果评测 / 离线打包")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_menu = sub.add_parser("menu", help="交互菜单（桌面快捷方式用）")
    p_menu.add_argument("--target", "-t", default=None, help="目标应用配置 json")
    p_menu.add_argument("--out", "-o", default=None, help="报告输出目录")

    for name, help_text in [
        ("check", "环境体检"),
        ("smoke", "部署验证"),
        ("eval", "效果评测"),
        ("bundle", "制作离线部署包"),
        ("all", "完整交付流程 + 汇总报告"),
    ]:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--target", "-t", required=True, help="目标应用配置 json")
        p.add_argument("--out", "-o", default=None, help="报告输出目录")
        if name == "eval":
            p.add_argument("--limit", "-n", type=int, default=None,
                           help="只跑前 N 条")

    p = sub.add_parser("patrol", help="持续巡检（反复探测服务稳定性）")
    p.add_argument("--target", "-t", required=True)
    p.add_argument("--out", "-o", default=None)
    p.add_argument("--interval", "-i", type=int, default=5, help="间隔秒数")
    p.add_argument("--count", "-n", type=int, default=6, help="巡检次数")

    p = sub.add_parser("snapshot", help="创建代码快照")
    p.add_argument("--target", "-t", required=True)
    p.add_argument("--out", "-o", default=None)
    p.add_argument("--label", "-l", default="manual", help="快照标签")

    p = sub.add_parser("snapshots", help="列出已有快照")
    p.add_argument("--target", "-t", required=True)
    p.add_argument("--out", "-o", default=None)

    p = sub.add_parser("rollback", help="回滚到指定快照")
    p.add_argument("--target", "-t", required=True)
    p.add_argument("--out", "-o", default=None)
    p.add_argument("--to", required=True, help="快照 id")
    p.add_argument("--apply", action="store_true",
                   help="真正执行（不加则只预演差异）")

    args = ap.parse_args()

    if args.cmd == "menu":
        cmd_menu(args.target, args.out)
        return

    target = load_target(args.target)
    out = out_dir(args.out)

    t0 = time.perf_counter()
    if args.cmd == "check":
        cmd_check(target, out)
    elif args.cmd == "smoke":
        cmd_smoke(target, out)
    elif args.cmd == "eval":
        cmd_eval(target, out, limit=args.limit)
    elif args.cmd == "bundle":
        cmd_bundle(target, out)
    elif args.cmd == "all":
        cmd_all(target, out)
    elif args.cmd == "patrol":
        cmd_patrol(target, out, interval=args.interval, count=args.count)
    elif args.cmd == "snapshot":
        cmd_snapshot(target, out, label=args.label)
    elif args.cmd == "snapshots":
        cmd_snapshots(target, out)
    elif args.cmd == "rollback":
        cmd_rollback(target, out, args.to, apply=args.apply)
    print(f"总耗时 {time.perf_counter() - t0:.1f}s")


if __name__ == "__main__":
    main()
