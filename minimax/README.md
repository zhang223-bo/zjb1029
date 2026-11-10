# 规有所依：阅读列表 status 筛选

「规有所依」赛题提交目录。一个可以运行的工程上下文模块：给出开发任务，它从 seekdb 取出适用的规格、要改的代码和验证用的测试，拼成一份开发上下文；把任务进展写进 PowerContext，进程重启后继续使用。

业务工程是一个阅读列表的查询接口 `items.list`。本次任务是给它加 `status` 筛选，同时保住原有的排序行为。

## 六项必做与对应证据

| 验收项 | 在这个目录里怎么落地 | 可核对的文件 |
|---|---|---|
| 能够运行 | `python -m minimax_sdd doctor` 检查依赖，`verify` 一条命令跑完 | `RUNBOOK.md` |
| MiniMax 实际参与 | 上下文由 `context` 命令产出后交给 MiniMax，改动与差异逐行对应 | `records/minimax-session.md`、`records/code_diff.txt` |
| 上下文正确 | 规格正文取自 seekdb；重新导入 `items-updated.md` 后输出换成新内容 | `records/run/context-before.txt`、`context-updated.txt` |
| 数据库实际参与 | 规格/代码/测试三行存在 `engineering_docs`，查询结果直接进上下文 | `records/run/seekdb-queries.txt`、`records/seekdb-queries.md` |
| 状态能够复用 | `remember` 写入后另起进程读回，待办不再重复已完成项 | `records/run/powercontext-state.txt`、`records/powercontext-state.md` |
| 代码修改有效 | 同一组测试改前断言失败、改后全绿；回归测试两次都过 | `records/run/test_run_before.log`、`test_run_after.log` |

`python -m minimax_sdd verify` 会把上表右侧的证据按当次运行重新生成，输出 `verify ok`。

## 三项技术的分工

| 技术 | 承担的作用 | 接入方式 |
|---|---|---|
| seekdb | 存规格、`items.list` 实现和测试；查询结果用于构造上下文 | MySQL 协议，PyMySQL 1.1.1，端口 2881 |
| MiniMax | 按上下文修改 `src/items.py` 的 `list_items` | MiniMax Code 0.7.6，模型 `minimax/MiniMax-M3.1-Flash` |
| PowerContext | 保存「测试已通过」，重启后读回并调整待办 | HTTP API，PowerContext 1.5.1 |

工程资料只存在 seekdb，不放进 PowerContext，也不用 SQLite 顶替 seekdb。PowerContext 自己的库可以是 SQLite。

## 目录

| 路径 | 用途 |
|---|---|
| `minimax_sdd/` | 上下文模块：导入、查询、渲染、状态、测试驱动、验收 |
| `src/items.py` | 改后的 `list_items`，版本 `items-after-1` |
| `snapshots/before/items.py` | 改前代码，版本 `items-before-1` |
| `snapshots/after/items.py` | 与 `src/items.py` 逐字节相同的改后快照 |
| `specs/items.md` | 当前规格，`status` 取 `read` 或 `unread` |
| `specs/items-updated.md` | 更新后的规格，增加 `archived` |
| `tests/test_items.py` | 功能测试与回归测试，改前改后用同一份 |
| `tests/test_context_rules.py` | 上下文渲染规则，不连数据库 |
| `tests/test_submission.py` | 快照一致性、差异一致性、改前失败改后通过 |
| `sample.md` | 验证样例：要改什么、哪些行为必须保持 |
| `tech-note.md` | 一页技术说明 |
| `RUNBOOK.md` | 依赖启动、初始化、验收命令、已知限制 |
| `records/` | 开发记录、查询记录、状态记录、测试日志 |

## 快速开始

```bash
cd minimax
python -m pip install -r requirements.txt
python -m minimax_sdd doctor      # 依赖没齐会指出差哪一项
python -m minimax_sdd init
python -m minimax_sdd verify
```

前提是 seekdb 和 PowerContext 已按 `RUNBOOK.md` 启动。

只跑不依赖外部服务的部分：

```bash
PYTHONPATH=src python -m unittest discover -s tests -p "test_*.py" -v
```

## 已知限制

- 检索是 seekdb 上的 `LIKE` 查询，中文按连续汉字切词。向量混合检索不属于本题范围。
- `updated_at` 用 `YYYY-MM-DD`，字符串顺序与时间顺序一致。
- 上下文里哪一类资料没命中会标「未命中」并附缺失说明，不会中断整个模块。
- 记忆复用以 `POST /v1/memory/entries/list` 为准；`/v1/memory/search` 只作为补充证据，失败不影响复用。
- 仓库不含真实密码、令牌和模型思维链。`var/` 被忽略，`init` 会重新生成。
