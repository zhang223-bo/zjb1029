"""按 seekdb 的查询结果和 PowerContext 里的验证状态生成开发上下文。

规格正文、代码符号、测试名都来自 seekdb 的返回行。
这里不保存任何具体项目的答案，因此重新导入规格后输出会跟着变。
"""

from __future__ import annotations

import re

from minimax_sdd.seekdb_store import KINDS

PASS_MARK = "test_filter_by_status 已通过"
TODO_TEXT = "实现 status 筛选"


def test_names(body: str) -> list[str]:
    return re.findall(r"^\s*def (test_\w+)\b", body, re.MULTILINE)


def render_context(task: str, rows: list[dict], memories: list[str], sql: str) -> str:
    """把查询结果渲染成一份可以直接交给开发助手的上下文。

    缺哪一类资料就在对应位置写明「未命中」，不抛异常：
    任务措辞变化时应当降级，而不是让整个模块起不来。
    """
    by_kind: dict[str, dict] = {}
    for row in rows:
        by_kind.setdefault(row["kind"], row)

    missing = [kind for kind in KINDS if kind not in by_kind]
    spec = by_kind.get("spec")
    code = by_kind.get("code")
    test = by_kind.get("test")

    if spec is not None:
        spec_block = (
            f"- 来源：{spec['source_path']}\n"
            f"- 条目：{spec['symbol']}\n"
            "- 正文：\n"
            f"{spec['body'].rstrip()}\n"
        )
    else:
        spec_block = "- 未命中：seekdb 中没有匹配的规格\n"

    if code is not None:
        code_block = f"- 符号：{code['symbol']}\n- 来源：{code['source_path']}\n"
    else:
        code_block = "- 未命中：seekdb 中没有匹配的代码\n"

    if test is not None:
        names = test_names(test["body"])
        listed = "\n".join(f"- {name}" for name in names) if names else "- （测试文件里没有 test_ 函数）"
        test_block = f"{listed}\n- 来源：{test['source_path']}\n"
    else:
        test_block = "- 未命中：seekdb 中没有匹配的测试\n"

    passed = [text.strip() for text in memories if PASS_MARK in text]
    done = "\n".join(f"- {text}" for text in passed) if passed else "- （无）"
    todo = "- （无）" if passed else f"- {TODO_TEXT}"
    hits = "；".join(
        f"{row['doc_key']} {row['source_path']} {row['symbol']}" for row in rows
    ) or "（无）"

    rendered = (
        f"任务：{task}\n"
        "\n"
        "适用规格：\n"
        f"{spec_block}"
        "\n"
        "要改的代码：\n"
        f"{code_block}"
        "\n"
        "测试：\n"
        f"{test_block}"
        "\n"
        "已完成：\n"
        f"{done}\n"
        "\n"
        "待办：\n"
        f"{todo}\n"
        "\n"
        "资料查询：\n"
        f"- SQL：{sql}\n"
        f"- 命中：{hits}\n"
    )
    if missing:
        rendered += f"- 缺失：{', '.join(missing)}（该任务没有命中这几类资料）\n"
    return rendered
