# 验证样例

## 任务

为阅读列表增加 `status` 筛选。

**要改什么**：`src/items.py` 的 `list_items` 在收到 `status` 时，只返回该状态的条目。

**哪些原有行为必须保持**：不传 `status` 时，仍按 `updated_at` 从新到旧返回全部条目。

## 上下文里必须出现的来源

| 种类 | 来源 | 内容 |
|---|---|---|
| 规格 | `specs/items.md` | 条目「筛选」。初始取值是 `read` 和 `unread` |
| 代码 | `src/items.py` | 符号 `items.list` |
| 功能测试 | `tests/test_items.py` | `test_filter_by_status` |
| 回归测试 | `tests/test_items.py` | `test_list_sorted_by_time` |

规格换成 `specs/items-updated.md` 并重新导入后，上下文里的规格正文必须变为「`read`、`unread` 和 `archived`」。

## 初始数据

三条阅读记录，测试文件里的数据与此相同。

| id | status | updated_at |
|---|---|---|
| 1 | unread | 2026-11-01 |
| 2 | read | 2026-11-03 |
| 3 | unread | 2026-11-02 |

## 初始状态

- `python -m minimax_sdd init` 已执行。
- seekdb 的 `engineering_docs` 中，规格正文来自 `specs/items.md`，不含 `archived`。
- PowerContext 使用 `init` 新建的 scope，里面没有「测试已通过」。

## 操作步骤

1. `python -m minimax_sdd doctor`，两项依赖都应显示 `[ok]`。
2. `python -m minimax_sdd verify`。
3. 查看 `records/run/test_run_before.log` 与 `test_run_after.log`。
4. 查看 `records/run/context-before.txt` 与 `context-updated.txt`。
5. 查看 `records/run/seekdb-queries.txt`，核对命中的 `doc_key`。
6. 查看 `records/run/powercontext-state.txt`，核对重启后的上下文。
7. 对照 `records/minimax-session.md` 与 `records/code_diff.txt`。

## 预期结果

| 检查 | 预期 |
|---|---|
| 改前测试 | 退出码 1。`test_filter_by_status` 断言失败，返回 `[2, 3, 1]` 而期望 `[3, 1]`。`test_list_sorted_by_time` 通过。版本 `items-before-1` |
| 改后测试 | 退出码 0，两个测试都通过。版本 `items-after-1` |
| 空状态上下文 | 含上表四个来源。规格正文是 `read` 和 `unread`。待办有「实现 status 筛选」。已完成为「（无）」 |
| 规格更新 | 重新导入后上下文含 `archived`，正文是「`read`、`unread` 和 `archived`」 |
| seekdb | 同一查询命中 `spec:items`、`code:items.list`、`test:items`，上下文引用这些返回行 |
| 状态复用 | 新进程读到验证记忆。已完成含 `test_filter_by_status 已通过`。待办不再有「实现 status 筛选」 |
| MiniMax | 开发记录中的任务、来源与 `if status is None` 和 `records/code_diff.txt` 一致 |

`verify` 结束前会把规格重新导入为 `specs/items.md`，因此可以重复执行。

## 重置方法

```bash
python -m minimax_sdd reset
```

规格恢复为 `specs/items.md`，并换一个空 scope。之后可再次执行 `verify`。若连 seekdb 数据也要清空：

```bash
mysql -h 127.0.0.1 -P 2881 -u root -e "DROP DATABASE minimax_sdd"
python -m minimax_sdd init
```
