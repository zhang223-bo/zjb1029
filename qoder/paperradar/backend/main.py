"""PaperRadar HTTP 服务：检索、文献库、每日雷达、技术证据。"""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import LOG_DIR, POWERCONTEXT_BASE_URL, POWERCONTEXT_SCOPE, WEB_DIR
from .ingest import index_all
from .memory import MEMORY_LOG, MemoryUnavailable, PowerContextMemory
from .radar import build_radar
from .search import run_search
from .store import SeekDB

app = FastAPI(title="PaperRadar", version="1.0.0")
db = SeekDB()
memory = PowerContextMemory()


class SearchBody(BaseModel):
    query: str
    mode: str = "hybrid"
    filters: dict = {}


class ProfileBody(BaseModel):
    direction: str = ""
    focus: Any = []
    prefer_unread: bool = True


class StatusBody(BaseModel):
    status: str


def _memory_or_error(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs), None
    except MemoryUnavailable as exc:
        return None, str(exc)


def refresh_status() -> dict[str, str]:
    """PowerContext 是阅读进度的真源，这里把它同步到 seekdb 的镜像列，便于 SQL 过滤。"""
    status_map, err = _memory_or_error(memory.get_reading_status)
    if not status_map:
        return {}
    changed = 0
    for doc_id, status in status_map.items():
        affected, _ = db.execute(
            "UPDATE docs SET status = %s WHERE id = %s AND status <> %s",
            [status, doc_id, status],
        )
        changed += int(affected or 0)
    return {"synced": len(status_map), "changed": changed}


@app.get("/api/health")
def health():
    pc = memory.health()
    stats = db.stats()
    scope_id = None
    if pc.get("ok"):
        scope_id, _ = _memory_or_error(memory.ensure_scope)
    return {
        "seekdb": {"ok": True, "version": "seekdb", **stats},
        "powercontext": {"ok": pc.get("ok"), "base_url": POWERCONTEXT_BASE_URL, "scope_id": scope_id, "detail": pc.get("detail")},
        "scope_title": POWERCONTEXT_SCOPE,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


@app.post("/api/search")
def api_search(body: SearchBody):
    if not body.query.strip():
        raise HTTPException(status_code=400, detail="检索词不能为空")
    if body.mode not in ("hybrid", "vector", "keyword"):
        raise HTTPException(status_code=400, detail="mode 只能是 hybrid / vector / keyword")

    refresh_status()
    profile, mem_err = _memory_or_error(memory.get_profile)
    result = run_search(db, body.query, body.filters, body.mode, profile)
    result["memory"] = {
        "ok": mem_err is None,
        "error": mem_err,
        "profile": {k: v for k, v in (profile or {}).items() if not k.startswith("_")},
    }
    # 把这次检索写进记忆，下一次会话能看到「上次在查什么」
    if mem_err is None:
        titles = [d["title"] for d in result["docs"][:3]]
        _, note_err = _memory_or_error(memory.note_search, body.query, titles, body.mode)
        result["memory"]["noted"] = note_err is None
    return result


@app.get("/api/docs")
def api_docs(status: str = "", kind: str = "", q: str = ""):
    refresh_status()
    clauses = ["1=1"]
    args: list[Any] = []
    if status:
        clauses.append("d.status = %s")
        args.append(status)
    if kind:
        clauses.append("d.kind = %s")
        args.append(kind)
    if q:
        clauses.append("(d.title LIKE %s OR d.tags LIKE %s)")
        args += [f"%{q}%", f"%{q}%"]
    rows, elapsed = db.query(
        "SELECT d.id, d.kind, d.title, d.authors, d.venue, d.year, d.tags, d.abstract, "
        "d.url, d.status, (SELECT COUNT(*) FROM chunks c WHERE c.doc_id = d.id) AS chunks "
        f"FROM docs d WHERE {' AND '.join(clauses)} ORDER BY d.year DESC, d.title",
        args,
    )
    for r in rows:
        r["tags"] = [t for t in (r["tags"] or "").split(",") if t]
    return {"items": rows, "elapsed_ms": round(elapsed, 2)}


@app.post("/api/docs/{doc_id}/status")
def api_set_status(doc_id: str, body: StatusBody):
    if body.status not in ("unread", "reading", "read"):
        raise HTTPException(status_code=400, detail="status 只能是 unread / reading / read")
    rows, _ = db.query("SELECT title FROM docs WHERE id = %s", [doc_id])
    if not rows:
        raise HTTPException(status_code=404, detail="文献不存在")
    title = rows[0]["title"]
    saved, err = _memory_or_error(memory.set_reading_status, doc_id, title, body.status)
    db.set_status(doc_id, body.status)  # 镜像列，供 SQL 过滤
    return {"doc_id": doc_id, "status": body.status, "memory_ok": err is None, "memory_error": err, "saved": saved}


@app.get("/api/radar")
def api_radar(limit: int = 6):
    refresh_status()
    return build_radar(db, memory, limit=limit)


@app.get("/api/profile")
def api_profile():
    data, err = _memory_or_error(memory.get_profile)
    if err:
        return JSONResponse(status_code=503, content={"ok": False, "error": err})
    return {"ok": True, "profile": {k: v for k, v in data.items() if not k.startswith("_")}, "artifact": data.get("_artifact")}


@app.put("/api/profile")
def api_set_profile(body: ProfileBody):
    focus = body.focus
    if isinstance(focus, str):
        focus = [t.strip() for t in focus.replace("，", ",").split(",") if t.strip()]
    payload = {"direction": body.direction, "focus": list(focus or []), "prefer_unread": body.prefer_unread}
    saved, err = _memory_or_error(memory.set_profile, payload)
    if err:
        return JSONResponse(status_code=503, content={"ok": False, "error": err})
    return {"ok": True, "profile": payload, "saved": saved}


@app.get("/api/memory/search")
def api_memory_search(q: str, limit: int = 5):
    hits, err = _memory_or_error(memory.search_memory, q, limit)
    if err:
        return JSONResponse(status_code=503, content={"ok": False, "error": err})
    return {"ok": True, "hits": hits}


@app.get("/api/evidence")
def api_evidence(limit: int = 12):
    searches = db.recent_searches(limit)
    for s in searches:
        s["ts"] = str(s["ts"])
    traces: list[dict] = []
    if MEMORY_LOG.exists():
        lines = MEMORY_LOG.read_text(encoding="utf-8").strip().splitlines()[-limit * 2:]
        for line in reversed(lines):
            try:
                traces.append(json.loads(line))
            except Exception:
                continue
    return {"searches": searches, "memory_trace": traces, "sql_index": _sql_samples()}


def _sql_samples() -> list[dict]:
    return [
        {
            "name": "建表：一张 chunks 表同时承载向量、全文与结构化字段",
            "sql": "CREATE TABLE chunks (id VARCHAR(96) PRIMARY KEY, doc_id VARCHAR(48), seq INT, section VARCHAR(128), "
                   "page INT, content TEXT, search_text TEXT, emb VECTOR(384), "
                   "FULLTEXT INDEX ft_search (search_text) WITH PARSER ngram, "
                   "VECTOR INDEX vidx_emb (emb) WITH (DISTANCE=cosine, TYPE=hnsw, LIB=vsag))",
        },
        {
            "name": "混合检索：全文条件 + 结构化过滤 + 向量近邻排序，一次下发",
            "sql": "SELECT ... MATCH(c.search_text) AGAINST('长文档 切分') AS ft_score, "
                   "cosine_distance(c.emb, '[...]') AS vec_dist FROM chunks c JOIN docs d ON d.id = c.doc_id "
                   "WHERE MATCH(c.search_text) AGAINST('长文档 切分') AND d.kind = 'paper' AND d.status = 'unread' "
                   "ORDER BY vec_dist APPROXIMATE LIMIT 40",
        },
        {
            "name": "检索记录：每次查询都落库，可用于回放核对",
            "sql": "SELECT ts, query, mode, candidates, elapsed_ms FROM search_log ORDER BY ts DESC LIMIT 20",
        },
    ]


@app.post("/api/ingest")
def api_ingest():
    result = index_all(db)
    return {"ok": True, **result}


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
