# AI 应用交付工具包（deliverkit）

把「到客户现场把 AI 应用装起来、并能验收」这件事工具化。

不是又一个 AI 应用，而是**交付 AI 应用的工具**——实施工程师每天在做环境摸底、部署、自检、证明效果、出验收单，这些事散落在临时脚本和手工记录里，这个工具把它们串成一条命令。

## 为什么做这个

AI 实施的现场和实验室完全不是一回事：

- 客户机房**不能联外网**，`pip install` 直接失败
- 显存、内存、磁盘、端口，每台机器都不一样
- 服务起来了不代表能用，业务接口挂掉的情况很常见
- 交付必须有**可验收的书面材料**，口头说"能跑"不算数

所以工具的四件事，对应实施的四个动作：

```
环境体检 → 离线打包 → 部署验证 → 效果评测 → 验收报告
```

## 快速开始

```bash
# 1. 环境体检（12 项，13 秒出结论）
python -m deliverkit check -t targets/retail-nl2sql.json

# 2. 制作离线部署包（含安装脚本与文件哈希）
python -m deliverkit bundle -t targets/retail-nl2sql.json

# 3. 部署验证（拉起来、走一次真实调用）
python -m deliverkit smoke -t targets/retail-nl2sql.json

# 4. 效果评测（跑测试集，出准确率与延迟）
python -m deliverkit eval -t targets/retail-nl2sql.json

# 一条命令跑完全流程，并汇总成一份验收报告
python -m deliverkit all -t targets/retail-nl2sql.json
```

## 实测输出

### 环境体检

```
环境检查：零售经营数据智能问数系统  (retail-nl2sql v0.3.0)
------------------------------------------------------------------------------
  通过  操作系统      Windows 11 (AMD64)
  通过  Python 版本   当前 3.13.14，要求 >= 3.10
  通过  磁盘空间      D:\...\retail-nl2sql 剩余 62.1 GB / 共 477 GB（要求 5 GB）
  通过  内存          15.7 GB（建议 >= 8 GB）
  通过  GPU / 显存    NVIDIA GeForce RTX 4050 Laptop GPU，共 6.0 GB
  通过  端口占用      8000 已被本应用占用（服务在运行）
  通过  外网连通      可访问外网（223.5.5.5:53）
  通过  模型服务      Ollama 正常，已加载 9 个模型
  通过  模型就绪      3 个模型齐全，约 7.8 GB
  通过  应用目录      D:\...\retail-nl2sql
  通过  数据文件      1 个文件，共 71 MB
------------------------------------------------------------------------------
  10 通过 / 1 警告 / 0 失败  ->  环境就绪，可以部署
```

体检里最关键的是**外网连通**和**模型就绪**两项——前者决定能不能在线装依赖，后者决定离线包要不要带模型。

### 离线部署包

```
  通过  收集文件        32 个文件，0.2 MB
  警告  依赖清单        未提供 requirements.txt（本项目零第三方依赖）
  通过  安装脚本        install.bat（Windows）/ install.sh（Linux）
  通过  文件清单        manifest.json 含 32 个文件的 SHA256
  通过  打包压缩        retail-nl2sql-bundlev0.3.0.zip，0.1 MB
```

包里有 `manifest.json`（每个文件的 SHA256，用于校验传输损坏）、双平台安装脚本、模型清单。数据库不入包（可由脚本重建），避免包体膨胀。

**一个必须说清的边界**：安装脚本里的模型步骤是 `ollama pull`，这一步**需要联网**。所以严格讲，这个包解决的是「应用代码与依赖的离线交付」，不是「全离线交付」。真要上内网客户，还得额外一步——在有网机器上 `ollama pull` 之后把 `~/.ollama/models` 整目录拷过去，或者用 GGUF 文件 `ollama create` 导入。这一步目前不在工具范围内。

这是它的边界，交付时不该含糊过去。知道自己工具在哪失效，比声称它能干一切更值钱。

### 部署验证

```
  通过  服务探测      —        8000 已有实例在运行，直接复用
  通过  健康检查      67ms     HTTP 200，模型 qwen2.5:7b 就绪
  通过  端到端问数    1937ms   「一共有多少条订单」→ 数量：499,984
  通过  指标接口      —        /api/metrics 可访问，支持日常巡检
```

只做健康检查是不够的——健康检查通过、业务接口挂掉的情况太常见。这里要求完整走一次问数并拿到非空结果。

### 效果评测

```
  T1 单表直答    15/15   100.0%
  T2 多表关联    10/10   100.0%
  T3 口径模糊     7/8     87.5%
  T4 应拒答       6/7     85.7%
------------------------------------------------------------------------------
  准确率 95.0%   P50 1.38s   P95 3.94s   总耗时 71.0s
```

评测默认**关闭缓存**——开着缓存跑，P50 会失真成 0 秒，看起来漂亮但没意义。这一点在报告里也写明了。

## 通用性验证：交付三个完全不同的应用

只交付自己的项目说明不了什么。所以准备了三个形态完全不同的目标：

| | 零售问数系统 | 轻量销售查询 | 智能时序预测平台 |
|---|---|---|---|
| 技术栈 | 大模型 + SQLite | 纯 Python 规则 | FastAPI + uvicorn |
| 启动方式 | `python server.py 8000` | `python server.py 8200` | `uvicorn api:app --port 8300` |
| 健康判定 | `llm.ok == true` | `llm.ok == true` | `status == "ok"` |
| 业务接口 | `POST /api/ask` | `POST /api/ask` | `POST /api/forecast` |
| 模型依赖 | 3 个 | 无 | 无 |
| GPU | 必需 | 不需要 | 不需要 |
| 业务调用延迟 | 1937 ms | 15 ms | 169 ms |
| 体检 / 部署验证 | 11 通过 / 4-4 | 11 通过 / 4-4 | 11 通过 / 4-4 |

三个应用的启动命令、健康判定字段、业务接口全都不一样，工具靠 `targets/*.json` 里的声明来适配，**没有改任何一行业务代码**：

```json
"launch": {
  "python": "<装了 fastapi 的那个解释器>",
  "cmd": ["{python}", "-m", "uvicorn", "api:app", "--host", "127.0.0.1", "--port", "{port}"]
},
"health": {"path": "/api/health", "ok_when": {"field": "status", "equals": "ok"}},
"smoke": {
  "path": "/api/forecast",
  "body": {"values": [120, 132, 138], "horizon": 3},
  "ok_when": {"field": "forecast", "non_empty": true}
}
```

**这才是「通用」的意思**：不是让所有应用迁就一套固定契约，而是让工具能读懂不同应用的契约。

三个细节值得说：

- **解释器可以不一样**：时序预测要用装了 FastAPI 的 venv，不能用跑工具的那个 Python，所以 `launch.python` 单独指定
- **健康判定不能硬编码**：写死 `llm.ok` 只能适配一类服务，改成声明式规则（`equals` / `non_empty` / `gte`）
- **缺指标接口不算失败**：时序预测没有 `/api/metrics`，判为警告而不是错误——不是每个应用都必须有可观测性接口

注意 `targets/ts-forecast.json` 指向本机绝对路径，别人 clone 后需要改；`targets/rule-query.json` 用相对路径，开箱即可跑。

## 巡检与回滚

冒烟只能证明「那一刻没问题」。真实运维还要两件事：稳定性要看一段时间，出问题要能退回去。

### 巡检（看时间维度）

```bash
python -m deliverkit patrol -t targets/rule-query.json -i 2 -n 5
```

```
  [1/5] 通过  23:02:12  健康 65ms · 问数 14ms · rule-engine-v1
  [2/5] 通过  23:02:15  健康 3ms · 问数 22ms · rule-engine-v1
  ...
  成功率 100%   平均 13ms   最大 22ms   波动 161.5%   ->  稳定
```

**判定逻辑里有个坑**：低延迟场景下百分比抖动会失真——1ms 到 22ms 是「161% 波动」，但绝对差只有 21ms，毫无意义。所以抖动同时看比例和绝对差（`<50%` 或 `绝对差 <100ms` 都算稳定）。

### 快照与回滚（看版本维度）

```bash
python -m deliverkit snapshot -t targets/rule-query.json -l before-upgrade
python -m deliverkit rollback -t targets/rule-query.json --to <快照id>          # 预演
python -m deliverkit rollback -t targets/rule-query.json --to <快照id> --apply  # 执行
```

设计上刻意保守，因为回滚本身可能造成不可逆损失：

- **默认只预演**，列出会修改/新增/删除哪些文件，不写盘
- **执行前先自动备份当前状态**，回滚失败还能再回滚回去
- **只碰代码**，`data/`、`logs/`、`*.db` 一律不动——数据没法回滚

实测：注入一行改动 → 预演显示「修改 server.py」→ 执行回滚 → 文件恢复、改动消失、回滚前状态已备份。

## 技术要点

**1. 零第三方依赖**
体检要读内存（Windows 走 `ctypes` 调 `GlobalMemoryStatusEx`）、探端口（`socket.bind`）、查 GPU（`nvidia-smi` 子进程）、测外网（`socket` 连 DNS）。全部标准库完成，客户机器上不需要先装一堆包才能体检。

**2. 孤儿进程必杀**
Windows 上启动的服务进程如果只 `terminate()`，python 子进程会残留占着端口，下次部署直接失败。所以用 `taskkill /F /T /PID` 连进程树一起杀——这个坑在本地踩过一次，写进来当教训。

**3. 模型名归一化**
Ollama 返回 `bge-m3:latest`，配置里写的是 `bge-m3`，直接比较会误判为"模型缺失"。加了 `:latest` 兼容和基础名匹配，但只在配置没写版本号时启用，避免把 `qwen2.5:3b` 误认成 `qwen2.5:7b`。

**4. 状态三值而非布尔**
每项检查是「通过 / 警告 / 失败」，不是 true/false。显存 5.8 GB 不是不能用，是"可能溢出"；内网不是错误，是"需要离线部署"。这种区分让报告能直接拿去和客户沟通。

**5. 报告面向客户**
生成的 Markdown 结论在第一节，明细在后。客户只看第一节，实施人员看明细，双方看同一份文件。

## 目录结构

```
ai-deliver-kit/
├── deliverkit/
│   ├── env.py        12 项环境体检
│   ├── bundle.py     离线打包 + 安装脚本生成
│   ├── smoke.py      部署验证（启动/健康/端到端/指标）
│   ├── evaluate.py   测试集评测（支持流式进度）
│   ├── patrol.py     持续巡检
│   ├── snapshot.py   快照与回滚
│   ├── report.py     四类报告生成
│   └── __main__.py   命令行入口
├── targets/
│   └── retail-nl2sql.json   待交付应用描述（换一个 json 就能交付别的应用）
└── out/                     报告与部署包输出
```

## 换一个应用来交付

工具不绑定某个具体应用。`targets/*.json` 描述要交付什么：

```json
{
  "name": "零售经营数据智能问数系统",
  "code": "retail-nl2sql",
  "version": "0.3.0",
  "app_dir": "D:/.../retail-nl2sql",
  "entry": "server.py",
  "port": 8000,
  "health_path": "/api/health",
  "smoke_question": "一共有多少条订单",
  "python_min": "3.10",
  "require_gpu": true,
  "min_free_gb": 5,
  "models": [{"name": "qwen2.5:7b", "role": "主模型", "size_gb": 4.68}],
  "data_files": ["data/retail_v1.db"],
  "testset": "tests/cases.csv"
}
```

只要应用提供 `/api/health` 和一个问数接口，就能被这套流程接管。

## 已知限制

- 只支持 HTTP 健康检查，gRPC / WebSocket 类应用需要扩展
- 离线包不包含模型权重，依赖现场 `ollama pull` 或预先导入模型盘
- 评测只判定返回类型是否符合预期，不做结果集逐值比对
- 未做多机批量部署（当前是单机场景）
