# 数据库查询记录

工程资料存在 seekdb 库 `minimax_sdd` 的表 `engineering_docs`。上下文里的规格正文、代码符号和测试名都来自下面这条查询的返回行，不是写死在程序里的答案。

## 表结构

```sql
CREATE TABLE engineering_docs (
    doc_key     VARCHAR(128) PRIMARY KEY,
    kind        VARCHAR(32)  NOT NULL,   -- spec / code / test
    source_path VARCHAR(256) NOT NULL,
    symbol      VARCHAR(128) NOT NULL,
    title       VARCHAR(256) NOT NULL,
    body        TEXT         NOT NULL,
    imported_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

三行初始数据：

| doc_key | kind | source_path | symbol |
|---|---|---|---|
| `spec:items` | spec | `specs/items.md` | 筛选 |
| `code:items.list` | code | `src/items.py` | items.list |
| `test:items` | test | `tests/test_items.py` | test_filter_by_status |

导入用 `INSERT ... ON DUPLICATE KEY UPDATE`，所以重新导入同一 `doc_key` 会整体覆盖 `body`，`imported_at` 也随之刷新。

## 查询

任务 `为阅读列表增加 status 筛选` 被拆成 `status`、`为阅读列表增加`、`筛选` 三个词，每个词匹配四列：

```sql
SELECT doc_key, kind, source_path, symbol, title, body
FROM engineering_docs
WHERE body LIKE '%status%'  OR title LIKE '%status%'  OR symbol LIKE '%status%'  OR source_path LIKE '%status%'
   OR body LIKE '%为阅读列表增加%' OR title LIKE '%为阅读列表增加%' OR symbol LIKE '%为阅读列表增加%' OR source_path LIKE '%为阅读列表增加%'
   OR body LIKE '%筛选%'     OR title LIKE '%筛选%'     OR symbol LIKE '%筛选%'     OR source_path LIKE '%筛选%';
```

词里的 `%`、`_`、`\` 会先转义，避免任务文本里的通配符把查询放大。实际执行的语句和参数由 `python -m minimax_sdd verify` 写到：

- `records/run/seekdb-queries.txt`
- `records/run/seekdb-queries-updated.txt`

参数值形如 `['%status%', '%status%', '%status%', '%status%', '%为阅读列表增加%', ...]`，四个一组对应四个列。

## 重新导入后的变化

把 `specs/items-updated.md` 重新导入 `spec:items` 后，同一条查询的命中行不变，但规格那一行的 `source_path` 变为 `specs/items-updated.md`，`body` 变为「允许的取值是 read、unread 和 archived。」。上下文据此输出新内容——这是「不能始终返回固定答案」的直接证据。

`verify` 会在第五步之前把规格重导入回 `specs/items.md`，保证可重复执行。
