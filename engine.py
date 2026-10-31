"""执行引擎：一个任务从推理到交付的完整流程，过程通过回调上报。

命令行（agent.py）和浏览器（web.py）共用这一份逻辑，只是消费事件的方式不同。
事件类型：

    step        {"step": 1, "total": 20}          开始新一轮推理
    wait        {"elapsed": 6}                    仍在等待模型返回
    thought     {"text": "..."}                   模型的中间思考
    tool_start  {"name": "write_file", "hint": "a.py"}
    tool_end    {"name": "...", "ok": true, "summary": "...", "detail": "..."}
    final       {"text": "...", "steps": 3, "elapsed": 18.2}
    error       {"message": "..."}
"""

from __future__ import annotations

import json
import threading
import time
from typing import Callable

import llm
import prompt as prompt_mod
import tools

Event = dict
Emitter = Callable[[Event], None]


class _Ticker:
    """模型思考期间定时上报，避免前端看起来像卡死。"""

    def __init__(self, emit: Emitter, interval: int = 1):
        self.emit = emit
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.elapsed = 0

    def __enter__(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc_info):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=0.3)
        return False

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self.elapsed += 1
            if self.elapsed >= 4 and self.elapsed % 3 == 0:
                self.emit({"type": "wait", "elapsed": self.elapsed})


def _parse_arguments(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        args = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"arguments 不是合法 JSON（收到 {str(raw)[:120]}）") from exc
    if not isinstance(args, dict):
        raise ValueError("arguments 必须是一个 JSON 对象")
    return args


def _hint(raw) -> str:
    """从参数里挑一个适合展示的短标签。"""
    try:
        args = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return ""
    if isinstance(args, dict):
        for key in ("path", "subdir"):
            if args.get(key):
                return str(args[key])
    return ""


def _summarize(result: str) -> str:
    """挑一行最有信息量的内容做摘要。"""
    lines = result.splitlines()
    head = lines[0] if lines else ""
    for line in lines[:4]:
        if line.startswith("退出码"):
            return f"{head}   {line.strip()}"
    return head


def run_task(task: str, cfg, max_steps: int, emit: Emitter) -> str:
    started = time.monotonic()
    session = llm.Session(cfg, prompt_mod.SYSTEM_PROMPT, tools.TOOL_SCHEMAS)
    session.add_user(prompt_mod.build_task_prompt(task))

    for step in range(1, max_steps + 1):
        emit({"type": "step", "step": step, "total": max_steps})

        try:
            with _Ticker(emit):
                text, calls = session.send()
        except llm.LLMError as exc:
            emit({"type": "error", "message": str(exc)})
            return ""

        if text:
            emit({"type": "thought", "text": text})

        if not calls:
            emit(
                {
                    "type": "final",
                    "text": text,
                    "steps": step,
                    "elapsed": round(time.monotonic() - started, 1),
                }
            )
            return text

        for call in calls:
            name = call.get("name") or ""
            emit({"type": "tool_start", "name": name, "hint": _hint(call.get("arguments"))})

            try:
                args = _parse_arguments(call.get("arguments"))
                result = tools.call_tool(name, args)
                ok = "失败" not in result and "超时" not in result
            except (ValueError, tools.ToolError) as exc:
                result = f"工具执行失败：{exc}"
                ok = False
            except Exception as exc:  # 兜底：别让单个工具崩掉整个循环
                result = f"工具意外报错：{type(exc).__name__}: {exc}"
                ok = False

            emit(
                {
                    "type": "tool_end",
                    "name": name,
                    "ok": ok,
                    "summary": _summarize(result),
                    "detail": result[:4000],
                }
            )
            session.add_tool_result(call.get("id", ""), result)

    emit({"type": "error", "message": f"达到最大步数 {max_steps} 仍未完成，可以把任务拆小或调大步数上限"})
    return ""
