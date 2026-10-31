"""模型通信层：同时支持 chat/completions 与 responses 两种协议。

只依赖标准库。自建网关大多走 chat 协议，Codex 一类网关只开放 responses 协议，
Session 在 auto 模式下会自动挑可用的那个，上层代码不用关心差异。
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
ROUTE_MISSING_HINTS = ("not_configured", "尚未启用", "not found", "unknown url", "不支持")


class LLMError(Exception):
    """模型调用失败。"""


def _request(url: str, api_key: str, payload: dict, timeout: int, retries: int = 2) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    last = "未知错误"
    for attempt in range(retries + 1):
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", "replace")
            return json.loads(raw)

        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:600]
            last = f"HTTP {exc.code}：{detail}"
            lowered = detail.lower()
            if exc.code in (404, 405, 501) or any(h in lowered for h in ROUTE_MISSING_HINTS):
                raise LLMError(last) from exc
            if exc.code in RETRYABLE_STATUS and attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise LLMError(last) from exc

        except urllib.error.URLError as exc:
            last = f"连不上模型服务 {url}（{exc.reason}）"
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise LLMError(
                last + "。请确认网关已启动，或检查 .env 里的 OPENAI_BASE_URL 是否正确。"
            ) from exc

        except (socket.timeout, TimeoutError) as exc:
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise LLMError(f"请求超时（>{timeout} 秒）：{exc}") from exc

        except json.JSONDecodeError as exc:
            raise LLMError(f"返回内容不是合法 JSON：{exc}") from exc

    raise LLMError(last)


def _to_responses_tools(tools: list[dict] | None) -> list[dict]:
    """chat 格式的工具声明转成 responses 格式（扁平结构）。"""
    converted = []
    for item in tools or []:
        fn = item.get("function") or {}
        converted.append(
            {
                "type": "function",
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return converted


class Session:
    """一次完整对话，内部维护协议无关的历史。"""

    def __init__(self, cfg, system_prompt: str, tools: list[dict]):
        self.cfg = cfg
        self.instructions = system_prompt
        self.tools = tools
        self.mode: str | None = None if cfg.api_mode == "auto" else cfg.api_mode
        self._chat: list[dict] = [{"role": "system", "content": system_prompt}]
        self._resp: list[dict] = []

    @property
    def protocol(self) -> str:
        return self.mode or "未确定"

    def add_user(self, text: str) -> None:
        self._chat.append({"role": "user", "content": text})
        self._resp.append({"role": "user", "content": text})

    def add_tool_result(self, call_id: str, output: str) -> None:
        if self.mode == "chat":
            self._chat.append({"role": "tool", "tool_call_id": call_id, "content": output})
        else:
            self._resp.append(
                {"type": "function_call_output", "call_id": call_id, "output": output}
            )

    def send(self) -> tuple[str, list[dict]]:
        """发一轮对话，返回 (文本, 工具调用列表)。工具调用项为 {id,name,arguments}。"""
        if self.mode in (None, "chat"):
            try:
                return self._send_chat()
            except LLMError:
                if self.mode == "chat":
                    raise
                self.mode = "responses"  # 该网关没开 chat 路由，切协议重试
        return self._send_responses()

    def _send_chat(self) -> tuple[str, list[dict]]:
        payload: dict = {
            "model": self.cfg.model,
            "messages": self._chat,
            "temperature": 0.2,
            "stream": False,
        }
        if self.tools:
            payload["tools"] = self.tools
            payload["tool_choice"] = "auto"

        data = _request(self.cfg.chat_endpoint, self.cfg.api_key, payload, self.cfg.timeout)
        choices = data.get("choices") or []
        if not choices:
            raise LLMError(f"返回里没有 choices：{json.dumps(data, ensure_ascii=False)[:300]}")

        self.mode = "chat"
        message = choices[0].get("message") or {}
        text = (message.get("content") or "").strip()

        calls: list[dict] = []
        raw_calls = message.get("tool_calls") or []
        for item in raw_calls:
            fn = item.get("function") or {}
            calls.append(
                {
                    "id": item.get("id", ""),
                    "name": fn.get("name", ""),
                    "arguments": fn.get("arguments") or "{}",
                }
            )

        record: dict = {"role": "assistant", "content": text}
        if raw_calls:
            record["tool_calls"] = raw_calls
        self._chat.append(record)
        return text, calls

    def _send_responses(self) -> tuple[str, list[dict]]:
        payload: dict = {
            "model": self.cfg.model,
            "instructions": self.instructions,
            "input": self._resp,
            "stream": False,
            "store": False,
        }
        if self.tools:
            payload["tools"] = _to_responses_tools(self.tools)
            payload["tool_choice"] = "auto"

        data = _request(self.cfg.responses_endpoint, self.cfg.api_key, payload, self.cfg.timeout)
        self.mode = "responses"

        texts: list[str] = []
        calls: list[dict] = []
        replay: list[dict] = []

        for item in data.get("output") or []:
            kind = item.get("type")
            if kind == "message":
                for part in item.get("content") or []:
                    if part.get("type") in ("output_text", "text"):
                        texts.append(part.get("text") or "")
                replay.append(item)
            elif kind == "function_call":
                calls.append(
                    {
                        "id": item.get("call_id") or item.get("id", ""),
                        "name": item.get("name", ""),
                        "arguments": item.get("arguments") or "{}",
                    }
                )
                replay.append(item)

        self._resp.extend(replay)
        return "\n".join(t for t in texts if t).strip(), calls


def probe(cfg) -> tuple[str, str]:
    """连通性探测，返回 (协议, 模型回复)。"""
    session = Session(cfg, "你是助手，回答保持极简。", [])
    session.add_user("回复两个字：就绪")
    text, _ = session.send()
    return session.protocol, text
