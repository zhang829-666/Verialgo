"""工具层：Agent 用来读写文件、执行代码的"手脚"。

设计原则：
1. 所有路径都锁死在 workspace/ 目录内，越界直接拒绝。
2. 报错信息写成模型能读懂的自然语言，它才有机会自我纠正。
3. 输出一律截断，避免一次失败的运行把上下文撑爆。
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT / "workspace"
WORKSPACE.mkdir(parents=True, exist_ok=True)

MAX_OUTPUT_HEAD = 3000
MAX_OUTPUT_TAIL = 800
MAX_TIMEOUT = 60
IGNORED_DIRS = {"__pycache__", ".git", ".venv", "venv", ".pytest_cache"}


class ToolError(Exception):
    """工具执行失败。消息会原样回传给模型。"""


def _resolve(rel: str) -> Path:
    """把相对路径解析到 workspace 内，越界则报错。"""
    raw = (rel or "").strip().replace("\\", "/")
    if not raw:
        raise ToolError("路径不能为空。")
    if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        raise ToolError(
            f"不要使用绝对路径（收到 {rel}），请写相对于 workspace 的路径，例如 quicksort.py"
        )
    target = (WORKSPACE / raw).resolve()
    try:
        target.relative_to(WORKSPACE)
    except ValueError:
        raise ToolError(f"路径 {rel} 超出了工作区范围，只允许操作 workspace/ 内的文件。") from None
    return target


def _clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT_HEAD + MAX_OUTPUT_TAIL + 60:
        return text
    omitted = len(text) - MAX_OUTPUT_HEAD - MAX_OUTPUT_TAIL
    return (
        f"{text[:MAX_OUTPUT_HEAD]}\n"
        f"...（输出过长，中间省略 {omitted} 个字符）...\n"
        f"{text[-MAX_OUTPUT_TAIL:]}"
    )


def _describe(path: Path) -> str:
    try:
        return str(path.relative_to(WORKSPACE)).replace("\\", "/")
    except ValueError:
        return path.name


def write_file(path: str, content: str) -> str:
    target = _resolve(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists()
    text = content if isinstance(content, str) else str(content)
    target.write_text(text, encoding="utf-8", newline="\n")
    action = "覆盖写入" if existed else "新建"
    lines = text.count("\n") + (1 if text else 0)
    return f"已{action} {_describe(target)}，共 {lines} 行、{len(text)} 字符。"


def read_file(path: str) -> str:
    target = _resolve(path)
    if not target.is_file():
        siblings = sorted(p.name for p in target.parent.glob("*")) if target.parent.exists() else []
        hint = f"该目录下现有：{', '.join(siblings[:20])}" if siblings else "所在目录为空。"
        raise ToolError(f"文件 {path} 不存在。{hint}")
    text = target.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        return f"{_describe(target)} 是空文件。"
    numbered = "\n".join(f"{i:>4}| {line}" for i, line in enumerate(text.splitlines(), 1))
    return _clip(f"{_describe(target)}（行号仅用于定位，不要写进代码）：\n{numbered}")


def list_files(subdir: str = ".") -> str:
    base = _resolve(subdir)
    if not base.exists():
        return f"目录 {subdir} 不存在。"
    rows: list[str] = []
    for item in sorted(base.rglob("*")):
        if any(part in IGNORED_DIRS for part in item.relative_to(WORKSPACE).parts):
            continue
        if item.is_file():
            rows.append(f"{_describe(item)}  ({item.stat().st_size} B)")
        if len(rows) >= 200:
            rows.append("...（已达 200 条上限）")
            break
    return "当前工作区文件：\n" + "\n".join(rows) if rows else "工作区还是空的。"


def run_python(path: str, args: str = "", timeout: int = 20) -> str:
    target = _resolve(path)
    if not target.is_file():
        raise ToolError(f"要运行的脚本 {path} 不存在，先把它写出来。")

    try:
        timeout = max(1, min(int(timeout or 20), MAX_TIMEOUT))
    except (TypeError, ValueError):
        timeout = 20

    try:
        extra = shlex.split(args or "")
    except ValueError:
        extra = []

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env.pop("PYTHONSTARTUP", None)

    cmd = [sys.executable, "-u", str(target), *extra]
    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(WORKSPACE),
            capture_output=True,
            timeout=timeout,
            env=env,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        raise ToolError(
            f"运行超时（超过 {timeout} 秒）。常见原因：死循环、算法复杂度过高、"
            "代码里用了 input() 等待输入。请修正后重试。"
        ) from None
    except OSError as exc:
        raise ToolError(f"无法启动 Python 解释器：{exc}") from None

    elapsed = time.monotonic() - started
    stdout = proc.stdout.decode("utf-8", "replace").rstrip()
    stderr = proc.stderr.decode("utf-8", "replace").rstrip()

    parts = [
        f"运行 {_describe(target)}",
        f"退出码：{proc.returncode}    耗时：{elapsed:.2f}s",
        "--- stdout ---",
        stdout or "(空)",
    ]
    if stderr:
        parts += ["--- stderr ---", stderr]
    if proc.returncode != 0:
        parts.append("结论：执行失败，请根据上面的 stderr 修正代码后重新运行。")
    else:
        parts.append("结论：执行成功。")
    return _clip("\n".join(parts))


TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "在 workspace 内写入或覆盖一个文件。写算法代码时用它保存 .py 文件，"
                "路径用相对路径，例如 'quicksort.py' 或 'algorithms/bfs.py'。"
                "文件已存在时会被整体覆盖，所以修改代码要把完整内容重新写一遍。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对 workspace 的文件路径"},
                    "content": {"type": "string", "description": "完整文件内容"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取 workspace 内某个文件的当前内容，返回带行号的形式，用于复查或定位错误。",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "相对 workspace 的文件路径"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "列出 workspace 内已有的文件，用来确认之前写过哪些代码，避免重复创建。",
            "parameters": {
                "type": "object",
                "properties": {
                    "subdir": {"type": "string", "description": "子目录，默认 '.' 表示整个工作区"}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_python",
            "description": (
                "用 Python 解释器实际执行 workspace 内的一个 .py 文件，返回退出码、stdout 和 stderr。"
                "写完代码必须调用它验证，不要靠推测下结论。默认工作目录就是 workspace。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要运行的 .py 文件相对路径"},
                    "args": {"type": "string", "description": "命令行参数，空格分隔，通常留空"},
                    "timeout": {
                        "type": "integer",
                        "description": f"超时秒数，1-{MAX_TIMEOUT}，默认 20",
                    },
                },
                "required": ["path"],
            },
        },
    },
]

_DISPATCH = {
    "write_file": write_file,
    "read_file": read_file,
    "list_files": list_files,
    "run_python": run_python,
}


def tool_names() -> list[str]:
    return list(_DISPATCH)


def call_tool(name: str, arguments: dict) -> str:
    handler = _DISPATCH.get(name)
    if handler is None:
        raise ToolError(f"不存在名为 {name} 的工具，可用工具：{', '.join(_DISPATCH)}")
    if not isinstance(arguments, dict):
        raise ToolError("参数必须是一个 JSON 对象。")
    try:
        return handler(**arguments)
    except TypeError as exc:
        raise ToolError(f"工具 {name} 的参数不匹配：{exc}") from None
