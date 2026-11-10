# 技术说明

## 模块结构

业务代码只有 `src/items.py` 里的 `list_items`。`tests/test_items.py` 是固定的测试，改前改后用同一份。`specs/` 放规格。

`minimax_sdd` 是上下文模块，入口 `python -m minimax_sdd`：

| 模块 | 职责 |
|---|---|
| `seekdb_store` | 建库建表、导入三份资料、按任务查询 |
| `context` | 把查询行和记忆渲染成一份上下文 |
| `state` | 建 scope、写入验证结果、列出记忆 |
| `tests_runner` | 用同一组测试分别跑改前和改后代码 |
| `verify` | 按验收顺序串起上面几步，逐项断言 |
| `__main__` | CLI，含 `doctor` 环境自检 |

改前代码在 `snapshots/before/items.py`：它接收 `status` 却不筛选，因此功能测试是**断言失败**，不是缺模块或起不来。这一点由 `verify` 显式检查（要求日志里出现 `AssertionError`，且不得出现 `ImportError`）。

## 上下文怎么选

任务先拆成英文词和连续中文。`为阅读列表增加 status 筛选` 得到 `status`、`为阅读列表增加`、`筛选`。

这些词转义 `%` 和 `_` 后拼进 seekdb 的 `LIKE` 条件，匹配 `body`、`title`、`symbol`、`source_path` 四列。返回行按规格、代码、测试排序后**原样**进入输出：

- 规格打印库里的 `body` 全文，不是程序里的常量；
- 代码打印库里的 `symbol` 和 `source_path`；
- 测试名从测试 `body` 里用 `def test_` 提取。

因为正文来自库，重新导入 `specs/items-updated.md` 会覆盖 `spec:items` 的 `body`，下一次输出就是新内容。程序里没有写死 `archived`——`verify` 分别断言更新前不含、更新后必须含。

某一类资料没命中时，上下文标注「未命中」并在末尾列出缺失项，不抛异常。任务措辞变化时应降级而不是让模块起不来。

## 三项技术的分工

**seekdb** 保存并可查询工程资料。上下文里的规格正文、代码符号、测试名全部来自查询返回行，而不是代码里的常量。这是「数据库实际参与」的落点。

**MiniMax** 接收这份上下文，只修改 `list_items` 的筛选分支。改动与 `records/code_diff.txt` 逐行对应。

**PowerContext** 保存验证状态。`remember` 写入后，另起一个进程跑 `context`，仍能从 `POST /v1/memory/entries/list` 读回这条记忆，据此把「实现 status 筛选」从待办移到已完成。这就是题目要求的「重启后读取并使用」。

工程资料与任务状态分开放：seekdb 存资料，PowerContext 存进展，互不替代。

## 状态怎么复用

`verify` 的第五步：

1. `remember` 写入验证结果；
2. `subprocess` 另起一个 Python 进程执行 `context`，等价于「重启模块」；
3. 子进程读 `entries/list`，把记忆渲染进「已完成」；
4. 断言待办里不再有「实现 status 筛选」。

`/v1/memory/search` 的全文检索结果只作为补充证据写入记录。检索不可用时复用以列表结果为准，不影响验收。
