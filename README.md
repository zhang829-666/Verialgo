# Verialgo

**一个会写算法代码、并且自己运行验证过的 Agent。**

给它一句算法需求，它会写代码 → 真正执行 → 读报错 → 自己改 → 改到通过，才向你汇报结果和复杂度。

不是"生成一段代码让你自己跑"，而是**它自己跑通了**才交付。

```
任务：实现并查集，路径压缩 + 按秩合并
[1/20] ▸ write_file   union_find.py
[2/20] ▸ run_python   union_find.py   退出码 0 · 0.31s
[3/20] 完成，3 步 24 秒
```

---

## 为什么是它

大多数"AI 写代码"的方案，止步于把代码吐到屏幕上。模型不会知道自己的输出其实跑不起来——取错一个下标、边界条件漏一个分支，肉眼看不出来。

Verialgo 把**执行结果**重新喂回模型。所以：

- 报错会触发自我修正，而不是把错误代码递给你
- 只有跑通之后才输出最终答复
- 每一步都对模型可见、对你可见

实际运行中出现过这样的过程：斐波那契矩阵快速幂，第 1 步取错了元素（`F(0)` 返回 1），执行失败；第 2 步模型自己看出"应该取右上角"，重写；第 3 步通过。这个闭环就是它能干活的原因。

---

## 特性

| 特性 | 说明 |
|---|---|
| **零依赖** | 纯标准库，不需要 `pip install`，Python 3.10+ 直接跑 |
| **零框架** | 手写循环不到 100 行，出问题一眼看到哪一步。没有 LangChain / CrewAI |
| **双协议自适应** | 同时支持 OpenAI `chat/completions` 与 `responses` 协议，自动探测 |
| **两种前端** | 命令行 + 浏览器界面，共用同一个执行引擎 |
| **实时可见** | 思考、工具调用、执行结果流式推送，等待时显示计时 |
| **安全边界** | 文件操作锁死在工作目录内，超时与步数硬上限 |

---

## 快速开始

### 1. 配置模型

复制 `.env.example` 为 `.env`：

```ini
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_API_KEY=sk-xxxxxxxx
VERIALGO_MODEL=deepseek-chat
```

三种常见后端：

| 后端 | `OPENAI_BASE_URL` | `VERIALGO_MODEL` |
|---|---|---|
| DeepSeek 官方 | `https://api.deepseek.com/v1` | `deepseek-chat` |
| Ollama 本地 | `http://127.0.0.1:11434/v1` | `qwen2.5-coder:14b` |
| 任意 OpenAI 兼容网关 | 网关地址 + `/v1` | 网关支持的模型名 |

> 只要接口兼容 OpenAI 协议即可，不限定厂商。

### 2. 自检（不消耗 token）

```bash
python agent.py --selftest
```

它会真写一个文件、真跑一段代码、真拦一次越界访问。五项全过再往下走。

### 3. 开工

```bash
python agent.py "实现一个支持区间加和区间求和的线段树"
```

不带参数即进入交互模式，可连续提需求。

---

## 浏览器界面

```bash
python web.py
```

浏览器自动打开 `http://127.0.0.1:8765/`（端口被占用会自动往后找）。Windows 下双击 `serve.bat` 亦可。

界面上能看到：

- **左侧时间线** — 每一步的推理、工具调用、执行结果实时滚动，等待模型时显示计时
- **右侧产物文件** — 点开直接看生成的代码，带行号与 Python 语法高亮
- **顶部状态** — 当前模型与接口连通情况

实现上只有 `http.server` + SSE：任务跑在后台线程，事件通过 `text/event-stream` 推给页面，不轮询，也没有任何前端构建步骤。整个界面是一个 HTML 文件。

---

## 它是怎么工作的

```
你的需求  →  模型推理  →  调用工具  →  结果回灌上下文  →  再推理
                                                        ↓
                                            模型不再要工具 = 结束
```

模型手上有四个工具：

| 工具 | 作用 |
|---|---|
| `write_file` | 把代码写入 `workspace/` |
| `read_file` | 带行号读回来自查 |
| `run_python` | **真的执行**，返回退出码 / stdout / stderr |
| `list_files` | 查看工作区已有内容 |

关键在 `run_python` 返回**原始报错**。模型看到 `IndexError: list index out of range`，才知道自己错在哪，于是重写、再跑。没有这个回环，它就只是个代码生成器。

两个决定成败的细节：

1. **工具的描述文字就是提示词。** 模型凭什么决定调不调、传什么参数，全靠这段文字。写"检索文档"和写"问题涉及内部资料时必须调用"，命中率差一个量级。
2. **报错要翻译成模型能读懂的话。** 不要把 traceback 原样丢回去，要写成"参数 `start_date` 格式应为 `YYYY-MM-DD`"。模型有纠错能力，前提是它看得懂错在哪。

---

## 配置项

| 变量 | 默认 | 说明 |
|---|---|---|
| `OPENAI_BASE_URL` | `http://127.0.0.1:3000/v1` | 接口地址，需含 `/v1` 之类的路径前缀 |
| `OPENAI_API_KEY` | 空 | 密钥，本地网关通常不校验 |
| `VERIALGO_MODEL` | `deepseek-chat` | 模型名 |
| `VERIALGO_API_MODE` | `auto` | `auto` / `chat` / `responses` |
| `VERIALGO_MAX_STEPS` | `16` | 单任务最大循环步数，上限 60 |
| `VERIALGO_TIMEOUT` | `180` | 单次模型请求超时（秒） |

系统环境变量优先于 `.env`。

**关于 `VERIALGO_API_MODE`**：多数服务提供 `/chat/completions`，但部分网关（如 Codex 系）只开放 `/responses`——两者的历史结构与工具声明格式完全不同。`auto` 会先试前者，遇到 404 自动切换，不用手动判断。跑 `python agent.py --probe` 可以看到实际走的是哪条。

---

## 常用命令

| 命令 | 作用 |
|---|---|
| `python agent.py` | 交互模式，连续提任务 |
| `python agent.py "任务"` | 单次执行 |
| `python agent.py "..." --max-steps 25` | 复杂任务放宽步数 |
| `python agent.py "..." --model deepseek-reasoner` | 临时换模型 |
| `python agent.py --selftest` | 本地自检，不联网 |
| `python agent.py --probe` | 只测接口连通 |
| `python demo_offline.py` | 离线演示，内置模拟模型，无 key 也能跑通完整循环 |
| `python web.py` | 启动浏览器界面 |

---

## 目录结构

```
Verialgo/
├── agent.py          命令行入口：事件渲染、自检
├── engine.py         执行引擎：事件的唯一来源，CLI 与 Web 共用
├── web.py            本地 Web 服务（http.server + SSE）
├── web/index.html    浏览器界面，单文件、零外部依赖
├── tools.py          四个工具，含路径越界防护
├── llm.py            模型通信，双协议自适应
├── prompt.py         系统提示词，行为约束写在这
├── config.py         配置加载，环境变量优先
├── demo_offline.py   离线演示（模拟模型，不耗 token）
├── .env.example      配置模板
├── run.bat           命令行启动脚本（Windows）
├── serve.bat         浏览器界面启动脚本（Windows）
└── workspace/        Agent 的所有产出落在这里
```

`engine.py` 是刻意拆出来的：它只负责"跑循环、发事件"，命令行把事件渲染成彩色文本，网页把事件推给浏览器。**两个前端共用同一套逻辑**，改一处两边都生效。

---

## 安全边界

- 所有文件操作锁死在 `workspace/` 内，`../` 与绝对路径直接拒绝（自检中包含这一项）
- 单次执行有超时上限，死循环不会挂住机器
- 输出超过 4000 字符自动截断，避免一次失败撑爆上下文
- 单任务默认最多 16 步，到顶就停，不会无限烧 token

⚠️ **但它执行的是模型生成的代码，跑在你的用户权限下。** 不要用它处理不可信来源的任务，重要目录请自行备份。

---

## 想改哪里

| 需求 | 改动位置 |
|---|---|
| 让它更严格 / 更爱写注释 | `prompt.py` 的 `SYSTEM_PROMPT` |
| 加工具（`run_pytest`、`fetch_url`…） | `tools.py`：写函数 + 加 schema + 注册到 `_DISPATCH` |
| 换模型、调参数 | `.env`，或 `llm.py` 中的请求体 |
| 改超时、步数上限 | `.env` 的 `VERIALGO_TIMEOUT` / `VERIALGO_MAX_STEPS` |

---

## 常见问题

**报 404 或 "尚未启用本机 API 路由"**
该网关没开放 chat 路由。把 `VERIALGO_API_MODE` 设为 `auto` 或 `responses`。

**连不上模型**
`python agent.py --probe`，看报错里的地址对不对。

**Windows 下运行后半天没动静，也不报错**
先确认解释器：PowerShell 里执行 `Get-Command python`。如果路径落在 `WindowsApps\python.exe` 且文件大小为 **0 字节**，那是应用商店的占位程序，执行后既不运行也不报错。去「设置 → 应用 → 高级应用设置 → 应用执行别名」把 `python.exe` / `python3.exe` 关掉，或改用 `run.bat`。

**确认解释器没问题但仍无输出**
是输出缓冲。本版已强制行缓冲，等待超过 4 秒会显示计时。若仍遇到，用 `python -u`。

**生命周期结束得太早 / 反复改都改不对**
多半是任务太大。拆成"先写核心函数"和"再补边界处理"两次提，比调参有效。

**中文输出乱码**
工具层已强制 UTF-8（`PYTHONUTF8=1`）。若仍乱，检查终端代码页：`chcp 65001`。

---

## 说明

本项目是一个**教学向的最小实现**——刻意不引入 Agent 框架，目的是让整个循环可以被完整读懂。生产环境还需要补的东西：并发控制、工具权限分级、结果持久化、评测集。

如果用得上，欢迎提 Issue。
