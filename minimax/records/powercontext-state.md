# PowerContext 状态写入与复用

PowerContext 只保存这次修改的验证状态。工程规格和代码不放在这里，也不拿 PowerContext 的库顶替 seekdb。

模块用到四个接口，均为 PowerContext 1.5.1 的公开 HTTP 接口：

| 用途 | 接口 |
|---|---|
| 建 scope | `POST /v1/scopes` |
| 写入验证结果 | `POST /v1/memory/remember` |
| 列出记忆（复用的主通道） | `POST /v1/memory/entries/list` |
| 全文检索（补充证据） | `POST /v1/memory/search` |
| 就绪检查 | `GET /health/ready` |

## 从空 scope 开始

`python -m minimax_sdd init` 调用 `POST /v1/scopes` 新建一个没有记忆的 scope，把返回的 `scope_id` 写进 `var/scope_id`（该目录被 `.gitignore` 忽略，不进仓库）：

```http
POST /v1/scopes
Content-Type: application/json

{"title": "阅读列表", "summary": "status 筛选这一次修改的验证状态", "idempotency_key": "reading-list-<uuid>"}
```

## 写入

测试通过后写入一条记忆：

```http
POST /v1/memory/remember
Content-Type: application/json

{
  "scope_id": "<var/scope_id>",
  "kind": "verification",
  "text": "验证结果：test_filter_by_status 已通过，test_list_sorted_by_time 已通过。status 筛选已完成，后续上下文不要再把「实现 status 筛选」放进待办。"
}
```

返回里带 `artifact`（`family: atomic-memory`、`artifact_id`、`revision`）和 `state: active`。

## 重启后复用

「重启」是新开一个 Python 进程，再执行 `python -m minimax_sdd context`。新进程调用：

```http
POST /v1/memory/entries/list
Content-Type: application/json

{"scope_id": "<var/scope_id>", "limit": 50}
```

列表里读到的 `text` 放进上下文的「已完成」，待办里不再出现单独一行「实现 status 筛选」。`verify` 用 `subprocess` 另起进程完成这一步，因此确实是跨进程读取，不是同进程内的变量。

同时调用 `POST /v1/memory/search`（`mode: fts`，查询词 `test_filter_by_status`）作为补充证据。检索不可用或返回错误时，以列表结果为准，错误原文写进当次记录——复用不依赖全文检索。

## 当次记录

`python -m minimax_sdd verify` 把 remember 的响应、search 的响应、list 的正文和重启后的上下文写到 `records/run/powercontext-state.txt`。
