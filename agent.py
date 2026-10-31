"""Verialgo - 会写算法代码并自我验证的 Agent。

用法：
    python agent.py                         进入交互模式
    python agent.py "实现 Dijkstra 算法"      单次执行
    python agent.py --selftest              不联网自检工具链
    python agent.py --probe                 检测模型接口是否连通
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time

import config as cfg_mod
import engine
import llm
import prompt as prompt_mod
import tools


class C:
    """终端配色，非交互环境自动关闭。"""

    _on = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

    @classmethod
    def _w(cls, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if cls._on else text

    @classmethod
    def dim(cls, t: str) -> str:
        return cls._w("2", t)

    @classmethod
    def bold(cls, t: str) -> str:
        return cls._w("1", t)

    @classmethod
    def cyan(cls, t: str) -> str:
        return cls._w("36", t)

    @classmethod
    def green(cls, t: str) -> str:
        return cls._w("32", t)

    @classmethod
    def red(cls, t: str) -> str:
        return cls._w("31", t)

    @classmethod
    def yellow(cls, t: str) -> str:
        return cls._w("33", t)


def banner() -> None:
    print(C.bold("Verialgo") + C.dim("  ·  算法代码智能体"))


def rule(char: str = "─", width: int = 62) -> None:
    print(C.dim(char * width))


def _preview(text: str, limit: int = 96) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def run_agent(task: str, cfg: cfg_mod.Config, max_steps: int, show_thought: bool = True) -> str:
    """跑一轮完整的 agent 循环，返回最终答复。执行逻辑在 engine 里，这里只负责渲染。"""

    def clear_line() -> None:
        sys.stdout.write("\r" + " " * 40 + "\r")
        sys.stdout.flush()

    def emit(ev: dict) -> None:
        kind = ev.get("type")
        if kind == "step":
            print(C.dim(f"[{ev['step']}/{ev['total']}] 模型推理中…"))
        elif kind == "wait":
            sys.stdout.write("\r    " + C.dim(f"…已等待 {ev['elapsed']} 秒") + "   ")
            sys.stdout.flush()
        elif kind == "thought":
            if show_thought:
                clear_line()
                for line in (ev.get("text") or "").splitlines():
                    if line.strip():
                        print(f"    {C.dim(line.strip())}")
        elif kind == "tool_start":
            clear_line()
            hint = ev.get("hint") or ""
            print(C.cyan(f"  ▸ {ev['name']}") + (C.dim(f"  {hint}") if hint else ""))
        elif kind == "tool_end":
            flag = C.green("✓") if ev.get("ok") else C.red("✗")
            print(f"    {flag} {C.dim(_preview(ev.get('summary') or '', 88))}")
        elif kind == "final":
            print(C.green(f"完成，共 {ev.get('steps')} 步。"))
        elif kind == "error":
            clear_line()
            print(C.red(str(ev.get("message") or "未知错误")))

    return engine.run_task(task, cfg, max_steps, emit)


def selftest(cfg: cfg_mod.Config) -> bool:
    """不联网，验证工具链真的能写文件、跑代码、捕获错误。"""
    banner()
    rule()
    print(C.bold("环境自检"))
    print(f"  Python      {sys.version.split()[0]}")
    print(f"  工作区      {tools.WORKSPACE}")
    print(f"  接口地址    {cfg.base}")
    print(f"  模型名      {cfg.model}")
    print(f"  API Key     {cfg.masked_key()}")
    rule()

    checks: list[tuple[str, bool, str]] = []

    code = '''"""自检脚本：验证解释器、编码、标准库都正常。"""

from __future__ import annotations


def sieve(limit: int) -> list[int]:
    """埃氏筛，返回 [2, limit] 区间内所有质数。"""
    if limit < 2:
        return []
    flags = bytearray([1]) * (limit + 1)
    flags[0:2] = b"\\x00\\x00"
    for i in range(2, int(limit**0.5) + 1):
        if flags[i]:
            flags[i * i :: i] = bytearray(len(flags[i * i :: i]))
    return [i for i, f in enumerate(flags) if f]


assert sieve(0) == []
assert sieve(1) == []
assert sieve(10) == [2, 3, 5, 7]
assert len(sieve(100_000)) == 9592
print("中文输出正常")
print("SELFTEST-OK")
'''

    try:
        msg = tools.write_file("_selftest.py", code)
        checks.append(("写入文件", True, msg))
    except Exception as exc:
        checks.append(("写入文件", False, str(exc)))

    if checks[-1][1]:
        try:
            out = tools.run_python("_selftest.py", timeout=30)
            ok = "SELFTEST-OK" in out and "退出码：0" in out
            detail = "100000 以内质数个数校验通过" if ok else _preview(out, 200)
            checks.append(("执行代码", ok, detail))
        except tools.ToolError as exc:
            checks.append(("执行代码", False, str(exc)))

    try:
        tools.read_file("_selftest.py")
        checks.append(("读取文件", True, "带行号回读成功"))
    except tools.ToolError as exc:
        checks.append(("读取文件", False, str(exc)))

    try:
        tools.write_file("../escape.py", "x = 1")
        checks.append(("越界拦截", False, "危险：居然允许写出工作区"))
    except tools.ToolError:
        checks.append(("越界拦截", True, "../ 路径被正确拒绝"))

    try:
        tools.run_python("_not_exists_.py")
        checks.append(("缺失文件提示", False, "应当报错却没有"))
    except tools.ToolError as exc:
        checks.append(("缺失文件提示", True, _preview(str(exc), 60)))

    for name, ok, detail in checks:
        mark = C.green("通过") if ok else C.red("失败")
        print(f"  [{mark}] {name:<12} {C.dim(detail)}")

    try:
        (tools.WORKSPACE / "_selftest.py").unlink(missing_ok=True)
    except OSError:
        pass

    rule()
    all_ok = all(ok for _, ok, _ in checks)
    if not all_ok:
        print(C.red("工具链存在问题，请把上面的失败项发给我。"))
        return False

    if not cfg.api_key:
        print(C.yellow("工具链全部正常。但还没配 API Key，暂时无法调用模型。"))
        print(C.dim("  复制 .env.example 为 .env，填入 base_url 和 key 即可。"))
        return True

    print(C.dim("工具链全部正常，正在检测模型接口…"))
    try:
        protocol, reply = llm.probe(cfg)
        print(C.green(f"模型就绪（{protocol} 协议），返回：{_preview(reply, 60)}"))
        print()
        print("可以开工了：")
        print(C.cyan('  python agent.py "实现一个 LRU 缓存，要求 O(1) 查询"'))
        return True
    except llm.LLMError as exc:
        print(C.red(f"模型接口不通：{exc}"))
        return False


def interactive(cfg: cfg_mod.Config, max_steps: int) -> None:
    banner()
    print(C.dim(f"模型 {cfg.model}  ·  最大 {max_steps} 步  ·  输入 exit 退出"))
    print(C.dim(f"产出目录 {tools.WORKSPACE}"))
    rule()
    while True:
        try:
            task = input(C.bold("\n你 > ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not task:
            continue
        if task.lower() in {"exit", "quit", "q", ":q"}:
            return
        rule()
        started = time.monotonic()
        answer = run_agent(task, cfg, max_steps)
        rule()
        if answer:
            print(answer)
        print(C.dim(f"（用时 {time.monotonic() - started:.1f}s）"))


def main(argv: list[str] | None = None) -> int:
    os.system("")  # Windows 下启用 ANSI 转义

    # 关掉输出缓冲，否则在部分终端里提示会攒到程序结束才一起出现
    for stream in (sys.stdout, sys.stderr):
        try:
            # 控制台保留系统原生编码（gb2312 下中文才不会乱码），仅重定向时才强制 UTF-8
            if stream.isatty():
                stream.reconfigure(line_buffering=True)
            else:
                stream.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass

    parser = argparse.ArgumentParser(
        prog="agent.py",
        description="Verialgo - 会写算法代码并自我验证的 Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            '  python agent.py "实现快速排序并分析复杂度"\n'
            '  python agent.py "用动态规划解决背包问题" --max-steps 20\n'
            "  python agent.py --selftest\n"
            "  python agent.py --probe\n"
        ),
    )
    parser.add_argument("task", nargs="*", help="要完成的算法任务，留空则进入交互模式")
    parser.add_argument("--model", help="临时覆盖模型名")
    parser.add_argument("--max-steps", type=int, help="单次任务的最大推理步数")
    parser.add_argument("--selftest", action="store_true", help="本地自检，不调用模型")
    parser.add_argument("--probe", action="store_true", help="只检测模型接口连通性")
    parser.add_argument("--quiet", action="store_true", help="不打印模型的中间思考")
    args = parser.parse_args(argv)

    cfg = cfg_mod.load_config()
    if args.model:
        cfg.model = args.model
    max_steps = args.max_steps or cfg.max_steps

    if args.selftest:
        return 0 if selftest(cfg) else 1

    if args.probe:
        banner()
        print(C.dim(f"检测 {cfg.base} / {cfg.model} …"))
        try:
            protocol, reply = llm.probe(cfg)
            print(C.green(f"连通正常（{protocol} 协议）：{_preview(reply, 80)}"))
            return 0
        except llm.LLMError as exc:
            print(C.red(f"不通：{exc}"))
            return 1

    if not cfg.api_key:
        banner()
        print(C.red("还没有配置 API Key。"))
        print(C.dim("  1. 复制 .env.example 为 .env"))
        print(C.dim("  2. 填写 OPENAI_BASE_URL 和 OPENAI_API_KEY"))
        print(C.dim("  3. 重新运行；也可以先跑 python agent.py --selftest 验证工具链"))
        return 1

    task = " ".join(args.task).strip()
    if task:
        banner()
        rule()
        print(C.bold("任务  ") + _preview(task, 200))
        rule()
        started = time.monotonic()
        answer = run_agent(task, cfg, max_steps, show_thought=not args.quiet)
        rule()
        if answer:
            print(answer)
            print(C.dim(f"\n（用时 {time.monotonic() - started:.1f}s，产出在 {tools.WORKSPACE}）"))
            return 0
        return 1

    interactive(cfg, max_steps)
    return 0


if __name__ == "__main__":
    sys.exit(main())
