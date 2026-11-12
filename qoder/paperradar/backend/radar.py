"""每日雷达：用记忆里的研究画像去召回未读文献，并给出可解释的推荐理由。"""

from __future__ import annotations

from .memory import MemoryUnavailable, PowerContextMemory
from .search import run_search
from .store import SeekDB


def build_radar(db: SeekDB, memory: PowerContextMemory, limit: int = 6) -> dict:
    profile = {}
    notes: list[str] = []
    memory_ok = True
    memory_error = ""
    try:
        profile = memory.get_profile()
        notes = memory.recent_notes(3)
        status_map = memory.get_reading_status()
    except MemoryUnavailable as exc:
        memory_ok = False
        memory_error = str(exc)
        status_map = {}

    focus = [t for t in (profile.get("focus") or []) if t]
    direction = profile.get("direction") or "检索增强生成"
    query = " ".join([direction] + focus)

    result = run_search(db, query, {"kind": "paper", "status": "unread"}, mode="hybrid", profile=profile, limit=60)
    docs = result["docs"]
    fallback_note = ""
    if len(docs) < 3:
        # 未读太少时放开状态过滤，但把已读的排在后面
        more = run_search(db, query, {"kind": "paper"}, mode="hybrid", profile=profile, limit=60)
        seen = {d["doc_id"] for d in docs}
        docs += [d for d in more["docs"] if d["doc_id"] not in seen]
        fallback_note = "未读文献不足，已把已读文献一并纳入"

    counts = _status_counts(db)
    items = []
    for d in docs[:limit]:
        hit = d["hits"][0] if d["hits"] else {}
        matched = [t for t in focus if t in (d["tags"] or []) or t in d["title"] or t in hit.get("content", "")]
        why = []
        if matched:
            why.append("命中关注点：" + "、".join(matched[:2]))
        if d["status"] == "unread":
            why.append("你还没读过")
        elif d["status"] == "reading":
            why.append("读到一半，可以续上")
        else:
            why.append("已读，可作为背景材料")
        if hit:
            why.append(f"最相关片段在「{hit.get('section') or '正文'}」")
        items.append(
            {
                "doc_id": d["doc_id"],
                "title": d["title"],
                "venue": d["venue"],
                "year": d["year"],
                "tags": d["tags"],
                "status": d["status"],
                "score": round(d["best_score"], 5),
                "why": "；".join(why),
                "snippet": (hit.get("content") or "")[:220],
                "section": hit.get("section", ""),
            }
        )

    return {
        "query": query,
        "profile": {k: v for k, v in profile.items() if not k.startswith("_")},
        "items": items,
        "counts": counts,
        "recent_notes": notes,
        "memory_ok": memory_ok,
        "memory_error": memory_error,
        "fallback_note": fallback_note,
        "evidence": result["evidence"],
    }


def _status_counts(db: SeekDB) -> dict:
    rows, _ = db.query("SELECT status, COUNT(*) AS c FROM docs WHERE kind = 'paper' GROUP BY status")
    counts = {"unread": 0, "reading": 0, "read": 0}
    for r in rows:
        counts[r["status"]] = int(r["c"])
    counts["total"] = sum(counts.values())
    return counts
