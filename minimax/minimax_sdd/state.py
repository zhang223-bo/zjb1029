"""把验证结果写进 PowerContext，重启进程后仍从这里读。

只用四个公开接口：
  POST /v1/scopes               建 scope
  POST /v1/memory/remember      写入验证结果
  POST /v1/memory/entries/list  列出记忆（重启后复用的主通道）
  POST /v1/memory/search        全文检索（可选，失败不影响复用）
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCOPE_FILE = ROOT / "var" / "scope_id"

PASS_TEXT = (
    "验证结果：test_filter_by_status 已通过，test_list_sorted_by_time 已通过。"
    "status 筛选已完成，后续上下文不要再把「实现 status 筛选」放进待办。"
)


def base_url() -> str:
    return os.environ.get("POWERCONTEXT_URL", "http://127.0.0.1:8000").rstrip("/")


def _opener(url: str):
    """本机服务不走系统代理。

    设了 HTTP_PROXY 的机器上，urllib 会把 127.0.0.1 的请求也送进代理，
    结果是一个和 PowerContext 无关的 502。
    """
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host in ("127.0.0.1", "localhost", "::1"):
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


def _request(path: str, body: dict | None = None, method: str = "POST", timeout: int = 30) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    url = base_url() + path
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json")
    token = os.environ.get("POWERCONTEXT_TOKEN", "")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with _opener(url).open(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"PowerContext {path} 返回 {error.code}：{detail}") from error
    except urllib.error.URLError as error:
        reason = str(error.reason).rstrip("。")
        raise RuntimeError(
            f"连不上 PowerContext {base_url()}：{reason}。先按 RUNBOOK.md 启动服务端。"
        ) from error
    return json.loads(raw) if raw else {}


def ready() -> str:
    """读 /health/ready。就绪返回 ready，退化返回 degraded。"""
    health = _request("/health/ready", method="GET", timeout=10)
    status = health.get("status", "unknown")
    if status == "not_ready":
        raise RuntimeError(f"PowerContext 未就绪：{json.dumps(health, ensure_ascii=False)}")
    return f"PowerContext {base_url()} 状态 {status}"


def create_scope() -> str:
    created = _request(
        "/v1/scopes",
        {
            "title": "阅读列表",
            "summary": "status 筛选这一次修改的验证状态",
            "idempotency_key": f"reading-list-{uuid.uuid4()}",
        },
    )
    scope_id = created["scope_id"]
    SCOPE_FILE.parent.mkdir(parents=True, exist_ok=True)
    SCOPE_FILE.write_text(scope_id + "\n", encoding="utf-8")
    return scope_id


def scope_id() -> str:
    if not SCOPE_FILE.is_file():
        raise RuntimeError("还没有 scope。先运行 python -m minimax_sdd init")
    value = SCOPE_FILE.read_text(encoding="utf-8").strip()
    if not value:
        raise RuntimeError("var/scope_id 是空的。先运行 python -m minimax_sdd init")
    return value


def remember_pass() -> dict:
    return _request(
        "/v1/memory/remember",
        {"scope_id": scope_id(), "kind": "verification", "text": PASS_TEXT},
    )


def list_memories() -> list[str]:
    listed = _request(
        "/v1/memory/entries/list",
        {"scope_id": scope_id(), "limit": 50},
    )
    return [entry["text"] for entry in listed.get("entries", [])]


def search_memories() -> dict:
    return _request(
        "/v1/memory/search",
        {
            "scope_id": scope_id(),
            "query": "test_filter_by_status",
            "limit": 5,
            "mode": "fts",
        },
    )
