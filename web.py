"""本地 Web 服务：把 Verialgo 搬到浏览器里。

只用标准库。启动后打开 http://127.0.0.1:8765 即可提交任务，
执行过程通过 SSE 实时推送到页面。
"""

from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import config as cfg_mod
import engine
import tools

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"

TASKS: dict[str, queue.Queue] = {}
TASKS_LOCK = threading.Lock()


def _safe_path(rel: str) -> Path | None:
    """把相对路径解析到 workspace 内，越界返回 None。"""
    if not rel:
        return None
    candidate = (tools.WORKSPACE / rel.replace("\\", "/")).resolve()
    try:
        candidate.relative_to(tools.WORKSPACE)
    except ValueError:
        return None
    return candidate


def start_task(task: str, max_steps: int) -> str:
    task_id = uuid.uuid4().hex[:12]
    channel: queue.Queue = queue.Queue()
    with TASKS_LOCK:
        TASKS[task_id] = channel

    def worker() -> None:
        try:
            cfg = cfg_mod.load_config()
            engine.run_task(task, cfg, max_steps, channel.put)
        except Exception as exc:  # 任何意外都要让前端看到
            channel.put({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        finally:
            channel.put({"type": "__end__"})

    threading.Thread(target=worker, daemon=True).start()
    return task_id


class Handler(BaseHTTPRequestHandler):
    server_version = "Verialgo"

    def log_message(self, fmt: str, *args) -> None:
        return  # 静默访问日志，只保留自己打印的信息

    def _send(self, body: bytes, status: int = 200, ctype: str = "application/json; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, status: int = 200) -> None:
        self._send(json.dumps(data, ensure_ascii=False).encode("utf-8"), status)

    def _error(self, message: str, status: int = 400) -> None:
        self._json({"error": message}, status)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            index = WEB_DIR / "index.html"
            if not index.exists():
                self._send(b"web/index.html missing", 500, "text/plain; charset=utf-8")
                return
            self._send(index.read_bytes(), 200, "text/html; charset=utf-8")
            return

        if path == "/api/status":
            cfg = cfg_mod.load_config()
            self._json(
                {
                    "model": cfg.model,
                    "base": cfg.base,
                    "has_key": bool(cfg.api_key),
                    "workspace": str(tools.WORKSPACE),
                }
            )
            return

        if path == "/api/files":
            self._json({"files": self._file_list()})
            return

        if path == "/api/file":
            rel = (query.get("path") or [""])[0]
            target = _safe_path(rel)
            if target is None:
                self._error("路径不合法", 400)
                return
            if not target.is_file():
                self._error("文件不存在", 404)
                return
            self._json(
                {
                    "path": rel,
                    "content": target.read_text(encoding="utf-8", errors="replace"),
                }
            )
            return

        if path == "/api/stream":
            task_id = (query.get("id") or [""])[0]
            self._stream(task_id)
            return

        self._error("未找到该地址", 404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/run":
            self._error("未找到该地址", 404)
            return

        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self._error("请求体不是合法 JSON")
            return

        task = (payload.get("task") or "").strip()
        if not task:
            self._error("任务描述不能为空")
            return

        cfg = cfg_mod.load_config()
        try:
            max_steps = max(1, min(int(payload.get("max_steps") or cfg.max_steps), 60))
        except (TypeError, ValueError):
            max_steps = cfg.max_steps

        self._json({"task_id": start_task(task, max_steps), "max_steps": max_steps})

    def _file_list(self) -> list[dict]:
        rows: list[dict] = []
        for item in sorted(tools.WORKSPACE.rglob("*")):
            if any(part in tools.IGNORED_DIRS for part in item.relative_to(tools.WORKSPACE).parts):
                continue
            if not item.is_file():
                continue
            stat = item.stat()
            rows.append(
                {
                    "path": str(item.relative_to(tools.WORKSPACE)).replace("\\", "/"),
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                }
            )
        return rows[:200]

    def _stream(self, task_id: str) -> None:
        with TASKS_LOCK:
            channel = TASKS.get(task_id)
        if channel is None:
            self._error("任务不存在或已结束", 404)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        try:
            while True:
                try:
                    event = channel.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    continue

                if event.get("type") == "__end__":
                    self.wfile.write(b"event: end\ndata: {}\n\n")
                    self.wfile.flush()
                    break

                payload = json.dumps(event, ensure_ascii=False)
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # 浏览器关掉页面了，正常退出
        finally:
            with TASKS_LOCK:
                TASKS.pop(task_id, None)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="web.py", description="Verialgo 的浏览器界面")
    parser.add_argument("--port", type=int, default=8765, help="监听端口，默认 8765")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    args = parser.parse_args(argv)

    cfg = cfg_mod.load_config()
    port = args.port
    server = None
    for candidate in range(port, port + 12):
        try:
            server = Server(("127.0.0.1", candidate), Handler)
            port = candidate
            break
        except OSError:
            continue

    if server is None:
        print("端口都被占用了，换个 --port 再试。", file=sys.stderr)
        return 1

    url = f"http://127.0.0.1:{port}/"
    print("Verialgo  ·  浏览器界面")
    print(f"  地址    {url}")
    print(f"  模型    {cfg.model}")
    print(f"  网关    {cfg.base}")
    print(f"  工作区  {tools.WORKSPACE}")
    print("  Ctrl+C 停止服务")
    print()

    if not args.no_browser:
        threading.Timer(0.9, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
