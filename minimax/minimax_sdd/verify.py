"""按验收顺序跑一遍：环境检查、测试、上下文、规格更新、状态复用。

每一步把证据写到 records/run/，失败时退出码非 0 并指明是哪一项。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from minimax_sdd.context import PASS_MARK, TODO_TEXT, render_context
from minimax_sdd.seekdb_store import import_documents, init_database, preflight, search
from minimax_sdd.state import (
    PASS_TEXT,
    create_scope,
    list_memories,
    ready,
    remember_pass,
    search_memories,
)
from minimax_sdd.tests_runner import run_items_tests

ROOT = Path(__file__).resolve().parent.parent
RUN_DIR = ROOT / "records" / "run"
TASK = "为阅读列表增加 status 筛选"


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"[不通过] {message}")


def _write(name: str, text: str) -> Path:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    path = RUN_DIR / name
    path.write_text(text, encoding="utf-8")
    return path


def _context(memories: list[str]) -> tuple[str, str]:
    sql, params, rows = search(TASK)
    rendered = render_context(TASK, rows, memories, sql)
    query_log = (
        f"SQL: {sql}\n"
        f"params: {params}\n"
        "rows:\n"
        + "\n".join(
            f"- {row['doc_key']} kind={row['kind']} path={row['source_path']} symbol={row['symbol']}"
            for row in rows
        )
        + "\n"
    )
    return rendered, query_log


def _fresh_context() -> str:
    """另起一个进程读状态，等价于「重启模块」。"""
    completed = subprocess.run(
        [sys.executable, "-m", "minimax_sdd", "context", "--task", TASK],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit(completed.stdout + completed.stderr)
    return completed.stdout


def verify() -> None:
    # 0. 环境
    print(preflight())
    print(ready())

    # 1. 从空状态开始
    init_database()
    import_documents("specs/items.md")
    scope = create_scope()
    print(f"scope: {scope}")

    # 2. 同一组测试：改前失败，改后通过
    before_code, before_log = run_items_tests("snapshots/before")
    _write("test_run_before.log", before_log)
    _need(before_code == 1, "改前测试应该失败，实际退出码不是 1")
    _need("AssertionError" in before_log, "改前失败必须是断言失败，不能是导入或环境错误")
    _need(
        "test_filter_by_status" in before_log and "FAIL" in before_log,
        "改前功能测试没有失败",
    )
    _need(
        "test_list_sorted_by_time" in before_log and "ok" in before_log,
        "改前回归测试没有通过",
    )

    after_code, after_log = run_items_tests("src")
    _write("test_run_after.log", after_log)
    _need(after_code == 0, "改后测试应该通过")
    _need("FAILED" not in after_log, "改后仍有失败")

    # 3. 空状态下的上下文
    before_state, query_log = _context([])
    _need("specs/items.md" in before_state, "上下文没有规格来源")
    _need("items.list" in before_state, "上下文没有代码符号")
    _need("test_filter_by_status" in before_state, "上下文没有功能测试")
    _need("test_list_sorted_by_time" in before_state, "上下文没有回归测试")
    _need("tests/test_items.py" in before_state, "上下文没有测试来源")
    _need("archived" not in before_state, "更新前的规格不应含 archived")
    _need(TODO_TEXT in before_state, "空状态时应把筛选放进待办")
    _need(PASS_MARK not in before_state, "空状态不应已经记录测试通过")
    _write("context-before.txt", before_state)
    _write("seekdb-queries.txt", query_log)

    # 4. 规格更新后重新导入，输出必须跟着变
    try:
        import_documents("specs/items-updated.md")
        updated, updated_query = _context([])
        _need("archived" in updated, "规格重新导入后，上下文没有使用新内容")
        _need("read、unread 和 archived" in updated, "上下文没有带上更新后的取值")
        _write("context-updated.txt", updated)
        _write("seekdb-queries-updated.txt", updated_query)
    finally:
        import_documents("specs/items.md")

    # 5. 保存状态，重启后读取并用于上下文
    remembered = remember_pass()
    try:
        searched = search_memories()
    except RuntimeError as error:
        searched = {"error": str(error)}
    restarted = _fresh_context()
    memories = list_memories()
    _need(any(PASS_TEXT in text for text in memories), "重启后没有读到验证结果")
    _need(PASS_MARK in restarted, "后续上下文没有使用已保存的验证结果")
    _need(f"- {TODO_TEXT}" not in restarted, "已完成后待办仍在重复这项")
    _write(
        "powercontext-state.txt",
        "remember:\n"
        f"{remembered}\n\n"
        "search:\n"
        f"{searched}\n\n"
        "list:\n"
        + "\n".join(memories)
        + "\n\ncontext:\n"
        + restarted,
    )

    print("verify ok")
    print(f"证据目录：{RUN_DIR}")
