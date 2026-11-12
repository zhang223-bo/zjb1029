"""PowerContext 记忆层。

分工：seekdb 存「资料」（可检索的原文片段），PowerContext 存「人」（研究画像、
阅读进度、检索结论），并在下一次检索/下次打开时被复用。

用的都是 PowerContext 的原子记忆（atomic-memory）能力：
  - 画像：kind=research-profile，单条记录，内容是可解析的 JSON 文本
  - 进度：kind=reading-status，每篇文献一条，带 revision，可追溯改判
  - 结论：kind=search-note，每次检索落一条，下次进来能看到「上次在查什么」
另外调用 /v1/context/prepare 拿到一段有字节预算的 PreparedContext，
用来在「每日雷达」里还原上下文。
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from .config import (
    LOG_DIR,
    POWERCONTEXT_BASE_URL,
    POWERCONTEXT_SCOPE,
    POWERCONTEXT_TIMEOUT,
    POWERCONTEXT_TOKEN,
    USER_ID,
)

MEMORY_LOG = LOG_DIR / "memory_trace.jsonl"


class MemoryUnavailable(RuntimeError):
    pass


class PowerContextMemory:
    """PowerContext HTTP 客户端（只依赖标准 HTTP，便于替换成任意语言实现）。"""

    def __init__(self, base_url: str = POWERCONTEXT_BASE_URL, token: str = POWERCONTEXT_TOKEN):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.scope_id: str | None = None
        self._profile_id: str | None = None

    # ---------- 基础设施 ----------
    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json", "Connection": "close"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _request(self, method: str, path: str, **kwargs) -> Any:
        url = f"{self.base_url}{path}"
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                with httpx.Client(timeout=POWERCONTEXT_TIMEOUT) as client:
                    resp = client.request(method, url, headers=self._headers(), **kwargs)
                if resp.status_code >= 400:
                    raise MemoryUnavailable(f"{method} {path} -> {resp.status_code} {resp.text[:300]}")
                if not resp.content:
                    return {}
                try:
                    return resp.json()
                except Exception:
                    return {"raw": resp.text}
            except (httpx.TransportError, MemoryUnavailable) as exc:
                last_error = exc
                if attempt == 0:
                    time.sleep(0.15)
                    continue
        raise MemoryUnavailable(str(last_error))

    def health(self) -> dict:
        try:
            with httpx.Client(timeout=3) as client:
                r = client.get(f"{self.base_url}/health/ready")
            data = {}
            try:
                data = r.json()
            except Exception:
                data = {"raw": r.text[:200]}
            return {"ok": r.status_code == 200, "status_code": r.status_code, "detail": data}
        except Exception as exc:  # 连接失败
            return {"ok": False, "status_code": 0, "detail": str(exc)}

    # ---------- Scope ----------
    def ensure_scope(self, title: str = "paperradar") -> str:
        if self.scope_id:
            return self.scope_id
        if POWERCONTEXT_SCOPE and POWERCONTEXT_SCOPE.startswith("scp_"):
            self.scope_id = POWERCONTEXT_SCOPE
            return self.scope_id
        # 先看是否已有同名 Scope，避免重复创建
        try:
            data = self._request("GET", "/v1/scopes")
            for it in data.get("items") or []:
                if it.get("title") == title:
                    self.scope_id = it.get("scope_id")
                    return self.scope_id
        except MemoryUnavailable:
            pass
        data = self._request(
            "POST",
            "/v1/scopes",
            json={
                "title": title,
                "summary": "PaperRadar 文献雷达：科研文献与笔记的检索记忆库",
                "idempotency_key": f"paperradar-{title}",
            },
        )
        scope_id = data.get("scope_id")
        if not scope_id:
            raise MemoryUnavailable("无法创建 PowerContext Scope")
        self.scope_id = scope_id
        self._trace("scope.create", {"title": title, "scope_id": scope_id})
        return scope_id

    def _trace(self, op: str, detail: dict) -> None:
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            with MEMORY_LOG.open("a", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "op": op, **detail},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except Exception:
            pass

    # ---------- 原子记忆读写 ----------
    def save_memory(self, kind: str, text: str, description: str = "") -> dict:
        scope = self.ensure_scope()
        body = {
            "family": "atomic-memory",
            "content": {
                "schema": "powercontext.atomic-memory.v1",
                "kind": kind,
                "text": text[:8000],
            },
        }
        if description:
            body["content"]["description"] = description[:200]
        data = self._request("POST", f"/v1/scopes/{scope}/artifacts", json=body)
        ref = data.get("artifact") or data.get("ref") or data
        self._trace(
            "memory.create",
            {"kind": kind, "description": description, "text": text[:300], "artifact": ref},
        )
        return {"artifact": ref, "kind": kind, "text": text, "description": description}

    def list_memories(self, kind: str, limit: int = 100) -> list[dict]:
        scope = self.ensure_scope()
        body = {"scope_id": scope, "states": ["active"], "kind": kind, "limit": min(int(limit), 100)}
        data = self._request("POST", "/v1/atomic-memory/list", json=body)
        items = data.get("items") or []
        out = []
        for it in items:
            out.append(
                {
                    "artifact": it.get("artifact") or it.get("ref"),
                    "kind": it.get("kind", kind),
                    "text": it.get("text", ""),
                    "description": it.get("description", ""),
                    "state_version": it.get("state_version"),
                }
            )
        return out

    def search_memory(self, query: str, limit: int = 5) -> list[dict]:
        scope = self.ensure_scope()
        data = self._request(
            "POST",
            "/v1/atomic-memory/search",
            json={"scope_id": scope, "query": query, "mode": "text", "limit": int(limit)},
        )
        hits = []
        for h in data.get("hits") or []:
            mem = h.get("memory") or {}
            artifact = mem.get("artifact") or h.get("artifact")
            text = mem.get("text") or (mem.get("content") or {}).get("text") or ""
            artifact_id = _artifact_id(artifact)
            if not text and artifact_id:
                # 搜索接口只返回元数据，正文需要按精确引用读取
                try:
                    detail, _ = self._get_artifact(scope, artifact_id)
                    text = (detail.get("content") or {}).get("text", "")
                except Exception:
                    text = ""
            hits.append(
                {
                    "score": h.get("score"),
                    "matched_by": h.get("matched_by"),
                    "text": text,
                    "artifact": artifact,
                }
            )
        return hits

    def prepare_context(self, query: str) -> dict:
        scope = self.ensure_scope()
        data = self._request("POST", "/v1/context/prepare", json={"scope_id": scope, "query": query})
        return data

    def _get_artifact(self, scope: str, artifact_id: str) -> tuple[dict, str]:
        url = f"{self.base_url}/v1/scopes/{scope}/artifacts/atomic-memory/{artifact_id}"
        with httpx.Client(timeout=POWERCONTEXT_TIMEOUT) as client:
            r = client.get(url, headers=self._headers())
        if r.status_code >= 400:
            raise MemoryUnavailable(f"get artifact {artifact_id} -> {r.status_code}")
        return r.json(), r.headers.get("ETag", "")

    def replace_memory(self, artifact_id: str, kind: str, text: str, description: str = "") -> dict:
        scope = self.ensure_scope()
        etag = self._get_artifact(scope, artifact_id)[1]
        body = {"content": {"kind": kind, "text": text[:8000]}}
        if description:
            body["content"]["description"] = description[:200]
        headers = self._headers()
        if etag:
            headers["If-Match"] = etag
        url = f"{self.base_url}/v1/scopes/{scope}/artifacts/atomic-memory/{artifact_id}"
        with httpx.Client(timeout=POWERCONTEXT_TIMEOUT) as client:
            r = client.put(url, headers=headers, json=body)
        if r.status_code >= 400:
            raise MemoryUnavailable(f"replace {artifact_id} -> {r.status_code} {r.text[:200]}")
        try:
            data = r.json()
        except Exception:
            data = {}
        artifact = data.get("artifact") or {
            "family": "atomic-memory",
            "artifact_id": artifact_id,
            "revision": data.get("revision"),
        }
        self._trace(
            "memory.replace",
            {"artifact_id": artifact_id, "kind": kind, "revision": artifact.get("revision"), "text": text[:300]},
        )
        return {"artifact": artifact, "kind": kind, "text": text, "description": description}

    # ---------- 业务语义 ----------
    def get_profile(self) -> dict:
        items = self.list_memories("research-profile", limit=5)
        if not items:
            default = {
                "direction": "检索增强生成（RAG）在长文档问答中的应用",
                "focus": ["混合检索", "切分粒度", "评测"],
                "prefer_unread": True,
            }
            saved = self.save_memory(
                "research-profile",
                json.dumps(default, ensure_ascii=False),
                "研究画像：方向 / 关注点 / 是否优先未读",
            )
            default["_artifact"] = saved["artifact"]
            return default
        item = items[0]
        try:
            profile = json.loads(item["text"])
        except Exception:
            profile = {"direction": item["text"], "focus": [], "prefer_unread": True}
        profile["_artifact"] = item["artifact"]
        return profile

    def set_profile(self, profile: dict) -> dict:
        text = json.dumps(
            {
                "direction": profile.get("direction", ""),
                "focus": profile.get("focus", []),
                "prefer_unread": bool(profile.get("prefer_unread", True)),
            },
            ensure_ascii=False,
        )
        current = self.get_profile()
        artifact = current.get("_artifact")
        artifact_id = _artifact_id(artifact)
        if artifact_id:
            self.replace_memory(artifact_id, "research-profile", text, "研究画像")
        else:
            self.save_memory("research-profile", text, "研究画像")
        profile["_artifact"] = artifact
        return profile

    def get_reading_status(self) -> dict[str, str]:
        status: dict[str, str] = {}
        best_rev: dict[str, int] = {}
        for item in self.list_memories("reading-status", limit=100):
            try:
                payload = json.loads(item["text"])
            except Exception:
                continue
            doc_id = payload.get("doc_id")
            if not doc_id:
                continue
            rev = (item.get("artifact") or {}).get("revision") or 0
            if doc_id not in best_rev or rev >= best_rev[doc_id]:
                best_rev[doc_id] = rev
                status[doc_id] = payload.get("status", "read")
        return status

    def _find_reading_memory(self, doc_id: str) -> dict | None:
        found = None
        found_rev = -1
        for item in self.list_memories("reading-status", limit=100):
            try:
                if json.loads(item["text"]).get("doc_id") != doc_id:
                    continue
            except Exception:
                continue
            rev = (item.get("artifact") or {}).get("revision") or 0
            if rev > found_rev:
                found, found_rev = item, rev
        return found

    def set_reading_status(self, doc_id: str, title: str, status: str) -> dict:
        text = json.dumps({"doc_id": doc_id, "title": title, "status": status, "user": USER_ID}, ensure_ascii=False)
        existing = self._find_reading_memory(doc_id)
        if existing and _artifact_id(existing["artifact"]):
            return self.replace_memory(_artifact_id(existing["artifact"]), "reading-status", text, f"{title} 阅读状态")
        return self.save_memory("reading-status", text, f"{title} 阅读状态")

    def note_search(self, query: str, top_titles: list[str], mode: str) -> dict:
        text = (
            f"[{time.strftime('%Y-%m-%d %H:%M')}] 用户在 PaperRadar 检索「{query}」（{mode} 模式），"
            f"命中：{'、'.join(top_titles[:3]) if top_titles else '无'}。"
            f"这次检索已写入 seekdb.search_log，可回放。"
        )
        return self.save_memory("search-note", text, f"检索：{query[:40]}")

    def recent_notes(self, limit: int = 3) -> list[str]:
        items = self.list_memories("search-note", limit=30)
        return [i["text"] for i in items[:limit]]


def _artifact_id(artifact: Any) -> str | None:
    if isinstance(artifact, str):
        return artifact.split("/")[-1].split("@")[0] or None
    if isinstance(artifact, dict):
        return artifact.get("artifact_id") or artifact.get("id")
    return None


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]
