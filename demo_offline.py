"""离线演示：不需要 API Key，用内置的模拟模型跑通完整 agent 循环。

它会启动一个假的 OpenAI 兼容服务，按预设剧本返回工具调用，
用来验证"写文件 → 运行代码 → 读结果 → 汇报"这条链路是否真的工作。

    python demo_offline.py

看完如果满意，配好 .env 换成真模型即可，Agent 逻辑一行都不用改。
"""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

QUICKSORT_CODE = '''"""快速排序：三路划分版本，遇到大量重复元素也不会退化。"""

from __future__ import annotations


def quicksort(items: list[int]) -> list[int]:
    """返回升序的新列表，不修改入参。

    采用三路划分（小于 / 等于 / 大于基准），重复元素多时仍为 O(n log n)。
    时间复杂度：平均 O(n log n)，最坏 O(n^2)；空间复杂度：O(n)。
    """
    if len(items) <= 1:
        return list(items)
    pivot = items[len(items) // 2]
    less = [x for x in items if x < pivot]
    equal = [x for x in items if x == pivot]
    greater = [x for x in items if x > pivot]
    return quicksort(less) + equal + quicksort(greater)


if __name__ == "__main__":
    assert quicksort([]) == [], "空输入"
    assert quicksort([1]) == [1], "单元素"
    assert quicksort([2, 1]) == [1, 2], "两元素"
    assert quicksort([3, 3, 3, 3]) == [3, 3, 3, 3], "全部重复"
    assert quicksort([-5, -1, 0, 1, 5]) == [-5, -1, 0, 1, 5], "已有序"
    data = [5, -1, 0, 5, 100, -50, 5]
    assert quicksort(data) == sorted(data), "随机样例"
    assert data == [5, -1, 0, 5, 100, -50, 5], "不应修改入参"
    print("全部断言通过（7 组用例）")
'''

FINAL_ANSWER = """- 思路：三路划分快排，把数组按小于、等于、大于基准拆成三段分别递归，重复元素多时避免退化
- 文件：quicksort.py
- 复杂度：平均 O(n log n)，最坏 O(n²)；空间 O(n)
- 验证：实际运行通过 7 组断言，覆盖空输入、单元素、两元素、全重复、已有序、随机样例，并确认未修改入参"""


def build_script(messages: list[dict]) -> dict:
    """根据已经执行过几步工具，决定这一步返回什么。"""
    tool_turns = sum(1 for m in messages if m.get("role") == "tool")

    if tool_turns == 0:
        return {
            "role": "assistant",
            "content": "需求明确：实现快速排序，要求不修改入参并覆盖边界情况。先写文件，再用三路划分防止重复元素退化。",
            "tool_calls": [
                {
                    "id": "call_write_1",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps(
                            {"path": "quicksort.py", "content": QUICKSORT_CODE},
                            ensure_ascii=False,
                        ),
                    },
                }
            ],
        }

    if tool_turns == 1:
        return {
            "role": "assistant",
            "content": "文件已写入，现在实际执行一次，确认断言全部通过。",
            "tool_calls": [
                {
                    "id": "call_run_2",
                    "type": "function",
                    "function": {
                        "name": "run_python",
                        "arguments": json.dumps({"path": "quicksort.py", "timeout": 20}),
                    },
                }
            ],
        }

    return {"role": "assistant", "content": FINAL_ANSWER}


class MockHandler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # 静默访问日志
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {}

        message = build_script(payload.get("messages") or [])
        body = json.dumps(
            {"id": "mock", "object": "chat.completion", "choices": [{"index": 0, "message": message}]},
            ensure_ascii=False,
        ).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    server = HTTPServer(("127.0.0.1", 0), MockHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    os.environ["OPENAI_BASE_URL"] = f"http://127.0.0.1:{port}/v1"
    os.environ["OPENAI_API_KEY"] = "demo-key"
    os.environ["VERIALGO_MODEL"] = "mock-algorithm-model"

    print(f"模拟模型已启动于 127.0.0.1:{port}（本次演示不消耗任何 token）")

    import agent

    try:
        return agent.main(["实现快速排序，要求不修改入参，并覆盖边界情况"])
    finally:
        server.shutdown()


if __name__ == "__main__":
    sys.exit(main())
