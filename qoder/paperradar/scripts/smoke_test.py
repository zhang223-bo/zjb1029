"""端到端冒烟测试：验证 seekdb 检索与 PowerContext 记忆是否真实工作。

用法：
    python scripts/smoke_test.py
依赖：seekdb 已启动、PowerContext Server 已启动、后端服务已启动（默认 127.0.0.1:8099）。
"""

import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASE = "http://127.0.0.1:8099"
PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = ""):
    (PASS if ok else FAIL).append(name)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def main() -> int:
    c = httpx.Client(timeout=60, headers={"Connection": "close"})

    # 1. 健康检查
    h = c.get(f"{BASE}/api/health").json()
    check("seekdb 可连接且有数据", h["seekdb"]["ok"] and h["seekdb"]["docs"] > 0,
          f"{h['seekdb']['docs']} docs / {h['seekdb']['chunks']} chunks")
    check("PowerContext 可连接", h["powercontext"]["ok"], str(h["powercontext"].get("scope_id") or ""))

    # 2. 三种模式检索
    results = {}
    for mode in ("hybrid", "vector", "keyword"):
        d = c.post(f"{BASE}/api/search", json={"query": "长文档切分粒度怎么选", "mode": mode, "filters": {"kind": "paper"}}).json()
        results[mode] = d
        check(f"{mode} 检索返回结果", len(d["docs"]) >= 3, f"{len(d['docs'])} docs / {d['elapsed_ms']} ms")

    # 3. 检索记录已落库（可回放）
    ev = c.get(f"{BASE}/api/evidence?limit=10").json()
    check("seekdb.search_log 有检索记录", len(ev["searches"]) > 0, f"{len(ev['searches'])} 条")

    # 4. 记忆读写：标记状态 → 读回
    doc_id = results["hybrid"]["docs"][0]["doc_id"]
    r = c.post(f"{BASE}/api/docs/{doc_id}/status", json={"status": "reading"}).json()
    check("阅读状态写入 PowerContext", r.get("memory_ok") is True)
    h2 = c.get(f"{BASE}/api/health").json()
    # 通过 docs 接口确认镜像同步
    docs = c.get(f"{BASE}/api/docs", params={"status": "reading"}).json()
    ids = [x["id"] for x in docs["items"]]
    check("阅读状态同步到 seekdb 镜像列", doc_id in ids, f"{doc_id} -> reading")

    # 5. 每日雷达
    radar = c.get(f"{BASE}/api/radar?limit=4").json()
    check("每日雷达可生成", len(radar["items"]) > 0 and radar["memory_ok"],
          f"{len(radar['items'])} 条 / 未读 {radar['counts']['unread']}")

    # 6. 记忆检索（跨会话复用的证据）
    mem = c.get(f"{BASE}/api/memory/search", params={"q": "切分"}).json()
    check("PowerContext 记忆可检索", mem.get("ok") and len(mem.get("hits", [])) > 0,
          f"{len(mem.get('hits', []))} hits")

    print()
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：", FAIL)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
