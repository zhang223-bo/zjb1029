"""上下文模块入口。

python -m minimax_sdd <cmd>
  doctor       检查 seekdb 和 PowerContext 是否就绪
  init         建库、导入原始规格，并新建空的 PowerContext scope
  context      按任务输出开发上下文
  import-spec  把一份规格重新导入 seekdb
  remember     把测试通过写入 PowerContext
  test-before  用改前代码跑同一组测试
  test-after   用改后代码跑同一组测试
  verify       按验收顺序跑完全部检查
  reset        恢复原始规格，并换一个空 scope
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from minimax_sdd.context import render_context
from minimax_sdd.seekdb_store import import_documents, init_database, preflight, search
from minimax_sdd.state import create_scope, list_memories, ready, remember_pass
from minimax_sdd.tests_runner import run_items_tests
from minimax_sdd.verify import TASK, verify


def _build(task: str) -> str:
    sql, _params, rows = search(task)
    return render_context(task, rows, list_memories(), sql)


def _log(name: str, text: str) -> Path:
    path = Path(__file__).resolve().parent.parent / "records" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def doctor() -> int:
    """逐项检查两项外部依赖，全部打印后再决定退出码。"""
    failures = 0
    for label, probe in (("seekdb", preflight), ("PowerContext", ready)):
        try:
            print(f"[ok]   {label}: {probe()}")
        except Exception as error:  # noqa: BLE001 - 环境检查要把原因原样打出来
            failures += 1
            print(f"[fail] {label}: {error}")
    if failures:
        print(f"\n{failures} 项依赖未就绪，按 RUNBOOK.md 处理。", file=sys.stderr)
        return 1
    print("\n依赖齐备，可以运行 python -m minimax_sdd verify")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m minimax_sdd",
        description="阅读列表工程上下文：按任务给出规格、代码和测试，并保存任务状态",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="检查 seekdb 和 PowerContext 是否就绪")
    sub.add_parser("init", help="建库、导入原始规格，并新建空的 PowerContext scope")
    sub.add_parser("reset", help="恢复原始规格，并换一个空 scope")
    context = sub.add_parser("context", help="按任务输出开发上下文")
    context.add_argument("--task", default=TASK)
    import_spec = sub.add_parser("import-spec", help="把一份规格重新导入 seekdb")
    import_spec.add_argument("path")
    sub.add_parser("remember", help="把测试通过写入 PowerContext")
    sub.add_parser("test-before", help="用改前代码跑同一组测试")
    sub.add_parser("test-after", help="用改后代码跑同一组测试")
    sub.add_parser("verify", help="按验收顺序跑完全部检查")
    args = parser.parse_args(argv)

    if args.command == "doctor":
        return doctor()
    if args.command in ("init", "reset"):
        init_database()
        loaded = import_documents("specs/items.md")
        scope = create_scope()
        print(f"imported: {', '.join(loaded)}")
        print(f"scope_id: {scope}")
        return 0
    if args.command == "context":
        sys.stdout.write(_build(args.task))
        return 0
    if args.command == "import-spec":
        print("imported:", ", ".join(import_documents(args.path)))
        return 0
    if args.command == "remember":
        print(remember_pass())
        return 0
    if args.command == "test-before":
        code, log = run_items_tests("snapshots/before")
        path = _log("test_run_before.log", log)
        sys.stdout.write(log)
        print(f"log: {path}")
        return code
    if args.command == "test-after":
        code, log = run_items_tests("src")
        path = _log("test_run_after.log", log)
        sys.stdout.write(log)
        print(f"log: {path}")
        return code
    verify()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
