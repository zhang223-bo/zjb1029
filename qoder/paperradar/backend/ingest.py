"""入库：把论文与个人笔记切块、向量化，写入 seekdb。"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Iterable

from .config import DATA_DIR
from .embedding import embed_texts, to_vec_literal
from .store import SeekDB
from .textproc import build_search_text, iter_chunks

EMBED_BATCH = 16


def _chunk_id(doc_id: str, seq: int, content: str) -> str:
    digest = hashlib.md5(content.encode("utf-8")).hexdigest()[:10]
    return f"{doc_id}#{seq:03d}#{digest}"[:96]


def load_papers() -> list[dict]:
    path = DATA_DIR / "papers.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    docs = []
    for p in data["papers"]:
        docs.append(
            {
                "id": p["id"],
                "kind": "paper",
                "title": p["title"],
                "authors": p.get("authors", ""),
                "venue": p.get("venue", ""),
                "year": int(p.get("year") or 0),
                "tags": p.get("tags", ""),
                "abstract": p.get("abstract", ""),
                "url": p.get("url", ""),
                "sections": p.get("sections", []),
            }
        )
    return docs


def load_notes() -> list[dict]:
    notes = []
    for path in sorted((DATA_DIR / "notes").glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        title = path.stem
        m = re.search(r"^#\s+(.+)$", raw, re.MULTILINE)
        if m:
            title = m.group(1).strip()
        sections = []
        # 以二级标题切小节，保留标题作为小节名
        parts = re.split(r"^##\s+(.+)$", raw, flags=re.MULTILINE)
        if len(parts) > 1:
            for i in range(1, len(parts) - 1, 2):
                sections.append({"section": parts[i].strip(), "page": 0, "text": parts[i + 1].strip()})
        if not sections:
            sections = [{"section": "全文", "page": 0, "text": raw.strip()}]
        notes.append(
            {
                "id": f"n_{path.stem}",
                "kind": "note",
                "title": title,
                "authors": "我的笔记",
                "venue": "笔记",
                "year": 2026,
                "tags": "笔记,实验记录,心得",
                "abstract": sections[0]["text"][:180] if sections else "",
                "url": "",
                "sections": sections,
            }
        )
    return notes


def index_doc(db: SeekDB, doc: dict, embed: bool = True) -> dict:
    db.upsert_doc(doc)
    rows: list[tuple] = []
    pending: list[tuple[int, str, int, str]] = []

    # 摘要单独成块：回答「这篇讲什么」这类整体性问题时，它比任何局部片段都稳
    abstract = (doc.get("abstract") or "").strip()
    if abstract:
        pending.append((0, "摘要", 1, f"【{doc['title']} · 摘要】{abstract}"))

    for seq, (section, page, text) in enumerate(iter_chunks(doc["title"], doc["sections"]), start=1):
        pending.append((seq, section, page, text))

    vectors: list[list[float]] = []
    if embed and pending:
        for start in range(0, len(pending), EMBED_BATCH):
            batch = [p[3] for p in pending[start:start + EMBED_BATCH]]
            vectors.extend(embed_texts(batch))

    for i, (seq, section, page, text) in enumerate(pending):
        emb = to_vec_literal(vectors[i]) if vectors else None
        rows.append(
            (
                _chunk_id(doc["id"], seq, text),
                doc["id"],
                seq,
                section[:120],
                page,
                text,
                build_search_text(text),
                emb,
            )
        )
    db.replace_chunks(doc["id"], rows)
    return {"doc_id": doc["id"], "title": doc["title"], "chunks": len(rows)}


def index_all(db: SeekDB, docs: Iterable[dict] | None = None, embed: bool = True) -> dict:
    db.init_schema()
    docs = list(docs) if docs is not None else (load_papers() + load_notes())
    results = [index_doc(db, d, embed=embed) for d in docs]
    return {
        "docs": len(results),
        "chunks": sum(r["chunks"] for r in results),
        "detail": results,
    }


if __name__ == "__main__":
    db = SeekDB()
    print(json.dumps(index_all(db), ensure_ascii=False, indent=2)[:2000])
