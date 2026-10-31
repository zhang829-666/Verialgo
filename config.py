"""配置：优先读环境变量，其次读同目录下的 .env 文件。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent

DEFAULTS = {
    "OPENAI_BASE_URL": "http://127.0.0.1:3000/v1",
    "OPENAI_API_KEY": "",
    "VERIALGO_MODEL": "deepseek-chat",
    "VERIALGO_API_MODE": "auto",
    "VERIALGO_MAX_STEPS": "16",
    "VERIALGO_TIMEOUT": "180",
}


def _load_env_file(path: Path) -> dict[str, str]:
    """极简 .env 解析：KEY=VALUE，支持 # 注释和成对引号。"""
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        data[key] = value
    return data


@dataclass
class Config:
    base_url: str
    api_key: str
    model: str
    api_mode: str
    max_steps: int
    timeout: int

    @property
    def base(self) -> str:
        return self.base_url.rstrip("/")

    @property
    def chat_endpoint(self) -> str:
        return self.base + "/chat/completions"

    @property
    def responses_endpoint(self) -> str:
        return self.base + "/responses"

    def masked_key(self) -> str:
        if not self.api_key:
            return "(未设置)"
        if len(self.api_key) <= 8:
            return "****"
        return self.api_key[:4] + "****" + self.api_key[-4:]


def _as_int(raw: str, fallback: int, low: int, high: int) -> int:
    try:
        return max(low, min(int(raw), high))
    except (TypeError, ValueError):
        return fallback


def load_config() -> Config:
    file_env = _load_env_file(ROOT / ".env")

    def get(name: str) -> str:
        # 系统环境变量优先，其次是 .env，最后是默认值
        return os.environ.get(name) or file_env.get(name) or DEFAULTS[name]

    mode = get("VERIALGO_API_MODE").strip().lower()
    if mode not in {"auto", "chat", "responses"}:
        mode = "auto"

    return Config(
        base_url=get("OPENAI_BASE_URL"),
        api_key=get("OPENAI_API_KEY"),
        model=get("VERIALGO_MODEL"),
        api_mode=mode,
        max_steps=_as_int(get("VERIALGO_MAX_STEPS"), 16, 1, 60),
        timeout=_as_int(get("VERIALGO_TIMEOUT"), 180, 10, 600),
    )
