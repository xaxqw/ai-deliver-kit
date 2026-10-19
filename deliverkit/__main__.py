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

from . import env, evaluate, report, smoke  # noqa: E402

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
    t["app_dir"] = os.path.abspath(t["app_dir"])
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


def main():
    ap = argparse.ArgumentParser(
        prog="deliverkit",
        description="AI 应用交付工具包：环境体检 / 部署验证 / 效果评测 / 离线打包")
    sub = ap.add_subparsers(dest="cmd", required=True)

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

    args = ap.parse_args()
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
    print(f"总耗时 {time.perf_counter() - t0:.1f}s")


if __name__ == "__main__":
    main()
