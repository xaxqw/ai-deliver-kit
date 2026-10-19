# -*- coding: utf-8 -*-
"""报告生成：把检查结果转成能给客户看的 Markdown 验收文档

原则：结论放最前面。客户不看明细，只看"能不能验收"。
"""

import time

ICON = {"ok": "通过", "warn": "警告", "fail": "失败"}


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _head(target: dict, title: str) -> str:
    return (
        f"# {title}\n\n"
        f"| 项目 | 内容 |\n|---|---|\n"
        f"| 应用名称 | {target.get('name', '')} |\n"
        f"| 应用编号 | {target.get('code', '')} v{target.get('version', '')} |\n"
        f"| 部署路径 | `{target.get('app_dir', '')}` |\n"
        f"| 生成时间 | {_now()} |\n"
        f"| 生成工具 | AI 应用交付工具包 deliverkit |\n\n"
    )


def env_report(result: dict, target: dict) -> str:
    s = result["summary"]
    verdict = ("环境就绪，可以进入部署" if result["ready"]
               else "存在阻塞项，需处理后才能部署")

    md = _head(target, "环境检查报告")
    md += "## 一、总体结论\n\n"
    md += f"**{verdict}**\n\n"
    md += (f"- 检查项 {s['total']} 个：通过 {s['ok']}、警告 {s['warn']}、"
           f"失败 {s['fail']}\n")
    md += f"- 判定：{'可以部署' if result['ready'] else '暂不可部署'}\n\n"

    md += "## 二、检查明细\n\n"
    md += "| # | 检查项 | 结果 | 实测值 | 处理建议 |\n|---|---|---|---|---|\n"
    for i, c in enumerate(result["checks"], 1):
        md += (f"| {i} | {c['name']} | {ICON[c['status']]} | "
               f"{c['detail']} | {c['advice'] or '—'} |\n")

    blocking = [c for c in result["checks"] if c["status"] == "fail"]
    if blocking:
        md += "\n## 三、阻塞项处理清单\n\n"
        for c in blocking:
            md += f"- **{c['name']}**：{c['detail']}\n  - 处理：{c['advice']}\n"

    warn = [c for c in result["checks"] if c["status"] == "warn"]
    if warn:
        md += "\n## 四、风险提示\n\n"
        for c in warn:
            md += f"- **{c['name']}**：{c['detail']}"
            md += f"（{c['advice']}）\n" if c["advice"] else "\n"

    md += "\n---\n\n本报告由交付工具自动生成，检查时间点环境状态，不含后续变更。\n"
    return md


def smoke_report(result: dict, target: dict) -> str:
    md = _head(target, "部署验证报告")
    ok = result.get("passed", False)

    md += "## 一、总体结论\n\n"
    md += f"**{'部署成功，服务可正常响应' if ok else '部署未通过，需排查' }**\n\n"

    md += "## 二、验证步骤\n\n"
    md += "| 步骤 | 结果 | 耗时 | 说明 |\n|---|---|---|---|\n"
    for step in result.get("steps", []):
        md += (f"| {step['name']} | {ICON[step['status']]} | "
               f"{step.get('ms', '—')} | {step.get('detail', '')} |\n")

    if result.get("sample"):
        md += "\n## 三、样例查询\n\n"
        md += f"- 问题：{result['sample'].get('question', '')}\n"
        md += f"- 回答：{result['sample'].get('answer', '')}\n"
        md += f"- 耗时：{result['sample'].get('ms', 0)} ms\n"

    if result.get("error"):
        md += f"\n## 四、失败原因\n\n```\n{result['error']}\n```\n"

    md += "\n---\n\n验证基于一次端到端调用，不代表持续负载下的表现。\n"
    return md


def eval_report(result: dict, target: dict) -> str:
    md = _head(target, "效果验证报告")
    s = result.get("summary", {})

    md += "## 一、总体结论\n\n"
    md += (f"测试集 {s.get('total', 0)} 条，通过 {s.get('passed', 0)} 条，"
           f"准确率 **{s.get('accuracy', 0):.1%}**，"
           f"P50 {(s.get('p50_ms', 0) / 1000):.2f} s，"
           f"P95 {(s.get('p95_ms', 0) / 1000):.2f} s。\n\n")

    md += "## 二、分类准确率\n\n"
    if s.get("by_category"):
        md += "| 类别 | 条数 | 通过 | 准确率 |\n|---|---|---|---|\n"
        for cat, v in s["by_category"].items():
            md += (f"| {cat} | {v['total']} | {v['passed']} | "
                   f"{v['passed'] / max(v['total'], 1):.1%} |\n")
        md += "\n"

    fails = result.get("failures", [])
    if fails:
        md += "## 三、未通过用例\n\n"
        md += "| 编号 | 问题 | 期望 | 实际 |\n|---|---|---|---|\n"
        for f in fails[:20]:
            md += (f"| {f.get('id', '')} | {f.get('question', '')[:28]} | "
                   f"{f.get('expect', '')} | {f.get('status', '')} |\n")
        if len(fails) > 20:
            md += f"\n其余 {len(fails) - 20} 条见原始数据。\n"
        md += "\n"

    md += "## 四、说明\n\n"
    md += "- 准确率按「是否返回符合预期的结果类型」判定，不含人工复核\n"
    md += "- 延迟为单请求端到端耗时，不含并发排队\n"

    md += "\n---\n\n本报告基于固定测试集，实际效果与客户数据分布相关。\n"
    return md


def delivery_report(env_r, smoke_r, eval_r, target: dict) -> str:
    """汇总成一份交付验收报告——实施交付最终交出去的就是这个"""
    md = _head(target, "AI 应用交付验收报告")

    items = []
    if env_r:
        items.append(("环境检查", "通过" if env_r["ready"] else "未通过",
                      f"{env_r['summary']['ok']} 项通过 / "
                      f"{env_r['summary']['warn']} 项警告 / "
                      f"{env_r['summary']['fail']} 项失败"))
    if smoke_r:
        items.append(("部署验证", "通过" if smoke_r.get("passed") else "未通过",
                      smoke_r.get("detail", "")))
    if eval_r:
        s = eval_r.get("summary", {})
        items.append(("效果验证", "已执行",
                      f"准确率 {s.get('accuracy', 0):.1%}，"
                      f"P95 {(s.get('p95_ms', 0) / 1000):.2f} s"))

    all_ok = all(v[1] in ("通过", "已执行") for v in items)

    md += "## 一、验收结论\n\n"
    md += f"**{'验收通过' if all_ok else '存在未通过项，需整改后复验'}**\n\n"
    md += "| 验收项 | 结果 | 关键指标 |\n|---|---|---|\n"
    for name, res, detail in items:
        md += f"| {name} | {res} | {detail} |\n"

    md += "\n## 二、交付物清单\n\n"
    md += "| 类别 | 文件 |\n|---|---|\n"
    md += "| 应用代码 | `app/`、`server.py`、`static/index.html` |\n"
    md += "| 数据集 | 由脚本重建，不入包 |\n"
    md += "| 部署包 | `out/<code>-bundle.zip`（含安装脚本与清单） |\n"
    md += "| 验收文档 | 环境检查报告、部署验证报告、效果验证报告 |\n"

    md += "\n## 三、后续运维要点\n\n"
    md += "- 服务以本地进程运行，重启需要重新拉起 `server.py`\n"
    md += "- 模型常驻策略 `keep_alive=30m`，长时间空闲后首次请求会变慢\n"
    md += "- 日志位于 `logs/requests.jsonl`，可据此排查请求级问题\n"
    md += "- 指标接口 `GET /api/metrics`，可用于日常巡检\n"

    md += "\n---\n\n报告由 deliverkit 自动生成，各项结果对应各自检查时间点的状态。\n"
    return md
