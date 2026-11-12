"""检索层：向量 / 全文 / 混合三种模式，全部落到 seekdb 的一条 SQL 上。

混合模式的思路：
  WHERE 里放全文条件与结构化过滤（把候选集收窄，且能用上倒排索引），
  ORDER BY cosine_distance(...) APPROXIMATE 让向量近邻排序在库内完成，
  应用层只做 RRF 融合与解释，不重新排序整体候选池。
"""

from typing import Any

from .config import TOP_K
from .embedding import embed_query, to_vec_literal
from .textproc import build_match_query
from .store import SeekDB

RRF_K = 60

BASE_COLUMNS = """
    c.id AS chunk_id, c.doc_id, c.seq, c.section, c.page, c.content,
    d.title, d.kind, d.authors, d.venue, d.year, d.tags, d.status, d.url
"""


def build_filters(filters: dict | None) -> tuple[str, list[Any]]:
    filters = filters or {}
    clauses: list[str] = []
    params: list[Any] = []

    if filters.get("year_from"):
        clauses.append("d.year >= %s")
        params.append(int(filters["year_from"]))
    if filters.get("year_to"):
        clauses.append("d.year <= %s")
        params.append(int(filters["year_to"]))
    if filters.get("venue"):
        clauses.append("d.venue = %s")
        params.append(filters["venue"])
    if filters.get("kind"):
        clauses.append("d.kind = %s")
        params.append(filters["kind"])
    if filters.get("status"):
        clauses.append("d.status = %s")
        params.append(filters["status"])
    tags = filters.get("tags") or []
    for tag in tags:
        clauses.append("d.tags LIKE %s")
        params.append(f"%{tag}%")

    where = (" AND " + " AND ".join(clauses)) if clauses else ""
    return where, params


def _rows_to_results(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        out.append(
            {
                "chunk_id": r["chunk_id"],
                "doc_id": r["doc_id"],
                "title": r["title"],
                "kind": r["kind"],
                "authors": r["authors"],
                "venue": r["venue"],
                "year": r["year"],
                "tags": [t for t in (r["tags"] or "").split(",") if t],
                "status": r["status"],
                "url": r["url"],
                "section": r["section"],
                "page": r["page"],
                "content": r["content"],
                "scores": {
                    "vector": None if r.get("vec_dist") is None else round(1 - float(r["vec_dist"]) / 2, 4),
                    "fulltext": None if r.get("ft_score") is None else round(float(r["ft_score"]), 4),
                },
            }
        )
    return out


def vector_search(db: SeekDB, query: str, filters: dict, limit: int = 40) -> tuple[list[dict], str, float]:
    vec = to_vec_literal(embed_query(query))
    where, params = build_filters(filters)
    sql = (
        f"SELECT {BASE_COLUMNS}, cosine_distance(c.emb, %s) AS vec_dist FROM chunks c "
        f"JOIN docs d ON d.id = c.doc_id WHERE 1=1{where} "
        f"ORDER BY vec_dist APPROXIMATE LIMIT {int(limit)}"
    )
    args = [vec] + params
    rows, elapsed = db.query(sql, args)
    return _rows_to_results(rows), _render(sql, args), elapsed


def keyword_search(db: SeekDB, query: str, filters: dict, limit: int = 40) -> tuple[list[dict], str, float]:
    match = build_match_query(query)
    if not match:
        return [], "", 0.0
    where, params = build_filters(filters)
    sql = (
        f"SELECT {BASE_COLUMNS}, MATCH(c.search_text) AGAINST(%s) AS ft_score FROM chunks c "
        f"JOIN docs d ON d.id = c.doc_id "
        f"WHERE MATCH(c.search_text) AGAINST(%s){where} "
        f"ORDER BY ft_score DESC LIMIT {int(limit)}"
    )
    args = [match, match] + params
    rows, elapsed = db.query(sql, args)
    return _rows_to_results(rows), _render(sql, args), elapsed


def hybrid_search(db: SeekDB, query: str, filters: dict, limit: int = 40) -> tuple[list[dict], str, float]:
    """全文条件 + 结构化过滤 + 向量近邻排序，一次下发到 seekdb。"""
    match = build_match_query(query)
    if not match:
        return vector_search(db, query, filters, limit)
    vec = to_vec_literal(embed_query(query))
    where, params = build_filters(filters)
    sql = (
        f"SELECT {BASE_COLUMNS}, MATCH(c.search_text) AGAINST(%s) AS ft_score, "
        f"cosine_distance(c.emb, %s) AS vec_dist FROM chunks c "
        f"JOIN docs d ON d.id = c.doc_id "
        f"WHERE MATCH(c.search_text) AGAINST(%s){where} "
        f"ORDER BY vec_dist APPROXIMATE LIMIT {int(limit)}"
    )
    args = [match, vec, match] + params
    rows, elapsed = db.query(sql, args)
    return _rows_to_results(rows), _render(sql, args), elapsed


def fuse_scores(results: list[dict], mode: str = "hybrid") -> list[dict]:
    """分数级融合：把余弦相似度与全文得分归一到 [0,1] 后加权。

    两种分数不可比（余弦在 0~2 的区间里浮动，ngram 全文得分随 chunk 长度变化），
    所以先把全文得分在本批候选内做最大归一化，再按权重融合。
    """
    if not results:
        return results
    max_ft = max((r["scores"]["fulltext"] or 0) for r in results) or 1.0
    for r in results:
        vec = r["scores"]["vector"] or 0.0
        ft_raw = r["scores"]["fulltext"] or 0.0
        ft = ft_raw / max_ft
        r["scores"]["fulltext_norm"] = round(ft, 4)
        if mode == "vector":
            fused = vec
        elif mode == "keyword":
            fused = ft
        else:
            fused = 0.65 * vec + 0.35 * ft
        r["scores"]["fused"] = round(fused, 6)
    return sorted(results, key=lambda r: -r["scores"]["fused"])


def apply_memory_rerank(results: list[dict], profile: dict | None) -> list[dict]:
    """用 PowerContext 里的研究画像调整排序，并给每条结果写一句「为什么推给你」。

    记忆只做小幅加权（最多 0.03），保证它影响排序但不覆盖检索本身的相关性。
    """
    if not profile:
        for r in results:
            r["memory_boost"] = 0.0
            r["scores"]["final"] = r["scores"].get("fused") or 0.0
            r["reason"] = _reason(r, None)
        return results

    focus = [t for t in (profile.get("focus") or []) if t]
    prefer_unread = bool(profile.get("prefer_unread", True))

    for r in results:
        boost = 0.0
        hits = [t for t in focus if t and (t in (r.get("tags") or []) or t in r["title"] or t in r["content"])]
        if hits:
            boost += 0.012 * min(len(hits), 2)
        if prefer_unread and r["status"] == "unread":
            boost += 0.008
        if r["status"] == "read":
            boost -= 0.004
        r["memory_boost"] = round(boost, 5)
        base = r["scores"].get("fused") or 0.0
        r["scores"]["final"] = round(base + boost, 6)
        r["reason"] = _reason(r, {"focus_hits": hits, "prefer_unread": prefer_unread})
    return sorted(results, key=lambda r: -(r["scores"].get("final") or 0))


def _reason(r: dict, memory: dict | None) -> str:
    bits: list[str] = []
    ft = r["scores"].get("fulltext_norm", r["scores"].get("fulltext"))
    if ft:
        bits.append(f"全文命中 {ft:.2f}")
    if r["scores"].get("vector") is not None:
        bits.append(f"语义相似 {r['scores']['vector']:.2f}")
    if memory and memory.get("focus_hits"):
        bits.append("符合你的关注点：" + "、".join(memory["focus_hits"][:2]))
    if memory and memory.get("prefer_unread") and r["status"] == "unread":
        bits.append("尚未读过")
    return " · ".join(bits)


def grouped(results: list[dict], top_k: int = TOP_K) -> list[dict]:
    """按论文聚合，供前端以「论文卡片」展示，每张卡带最相关的几个片段。"""
    order: list[str] = []
    buckets: dict[str, dict] = {}
    for r in results:
        if r["doc_id"] not in buckets:
            buckets[r["doc_id"]] = {
                "doc_id": r["doc_id"],
                "title": r["title"],
                "kind": r["kind"],
                "authors": r["authors"],
                "venue": r["venue"],
                "year": r["year"],
                "tags": r["tags"],
                "status": r["status"],
                "url": r["url"],
                "best_score": r["scores"].get("final") or r["scores"].get("fused") or 0,
                "reason": r.get("reason", ""),
                "hits": [],
            }
            order.append(r["doc_id"])
        buckets[r["doc_id"]]["hits"].append(r)
    docs = [buckets[d] for d in order]
    for d in docs:
        d["hits"] = d["hits"][:3]
    return docs[:top_k]


def _render(sql: str, args: list[Any]) -> str:
    """把参数填回 SQL，给「技术证据」面板展示真实执行的语句。"""
    out = sql
    for a in args:
        text = a if isinstance(a, str) else str(a)
        if len(text) > 90:
            text = text[:90] + "..."
        out = out.replace("%s", repr(text), 1)
    return " ".join(out.split())


def run_search(
    db: SeekDB,
    query: str,
    filters: dict | None = None,
    mode: str = "hybrid",
    profile: dict | None = None,
    limit: int = 40,
) -> dict:
    filters = filters or {}
    if mode == "vector":
        raw, sql, elapsed = vector_search(db, query, filters, limit)
        fused = fuse_scores(raw, mode)
    elif mode == "keyword":
        raw, sql, elapsed = keyword_search(db, query, filters, limit)
        fused = fuse_scores(raw, mode)
    else:
        raw, sql, elapsed = hybrid_search(db, query, filters, limit)
        fused = fuse_scores(raw, mode)

    fallback = False
    if mode == "hybrid" and len(fused) < 5:
        # 全文条件过严时补一轮纯向量召回，避免「零结果」
        extra, sql2, elapsed2 = vector_search(db, query, filters, limit)
        seen = {r["chunk_id"] for r in fused}
        fused += [r for r in extra if r["chunk_id"] not in seen]
        fused = fuse_scores(fused, mode) if fused else fused
        sql = sql + "\n-- 兜底补召回：\n" + sql2
        elapsed += elapsed2
        fallback = True

    ranked = apply_memory_rerank(fused, profile)
    docs = grouped(ranked)

    memory_note = ""
    if profile:
        memory_note = "画像：方向=%s；关注=%s；偏好未读=%s" % (
            profile.get("direction", ""),
            "、".join((profile.get("focus") or [])[:3]),
            profile.get("prefer_unread", True),
        )

    log_id = db.log_search(
        query,
        mode,
        filters,
        sql,
        [d["doc_id"] for d in docs],
        len(fused),
        elapsed,
        memory_note,
    )

    return {
        "query": query,
        "mode": mode,
        "fallback": fallback,
        "candidates": len(fused),
        "elapsed_ms": round(elapsed, 2),
        "docs": docs,
        "evidence": {"log_id": log_id, "sql": sql, "elapsed_ms": round(elapsed, 2), "memory": memory_note},
    }
