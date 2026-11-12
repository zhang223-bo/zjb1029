# 验证样例（VERIFY）

三组可直接执行的样例，覆盖「seekdb 检索生效」与「PowerContext 记忆生效」两类核对点。
执行前请按 [RUNBOOK.md](RUNBOOK.md) 完成启动（seekdb、PowerContext Server、后端）。

---

## 样例 1：混合检索 vs 单一检索（seekdb 实际作用）

**初始数据**：`scripts/bootstrap.py` 已写入 13 篇文献（11 篇论文 + 2 篇笔记）、54 个片段。

**操作步骤**：
1. 打开 `http://127.0.0.1:8099`，确认检索框默认问题「长文档切分粒度到底怎么选？」；
2. 在「混合检索 / 仅向量 / 仅全文」三种模式下分别点击「检索」，对比结果与提示条。

**预期结果**：
- 混合检索返回 5~10 个文献卡片，命中片段来自 `Dense X Retrieval`（命题粒度）、`RAPTOR`（层次索引）、个人笔记《读论文笔记：RAG 的切分问题》等切分主题内容；
- 仅全文模式：召回「切分 / 粒度」字面命中的片段，但会漏掉只换了说法（「chunk 太长稀释向量」）的内容；
- 仅向量模式：召回语义相近的综述类内容，但漏掉专有名词强匹配（如 `chunk`、`BEIR`）；
- 提示条显示 `候选 N 条片段 · seekdb 耗时 X ms`。

**核对技术作用（任选其一）**：
- 「技术证据」标签页：展开任意一条检索记录，可以看到真实下发给 seekdb 的 SQL（含 `MATCH ... AGAINST`、`cosine_distance`、`ORDER BY ... APPROXIMATE`）与耗时；
- 用 MySQL 客户端直接核对：

```sql
-- 检索记录可回放
SELECT ts, query, mode, candidates, elapsed_ms FROM paperradar.search_log ORDER BY ts DESC LIMIT 5;

-- 复现一次混合检索（向量串以界面 SQL 为准）
SELECT d.title, MATCH(c.search_text) AGAINST('长文档 切分 粒度') AS ft_score,
       cosine_distance(c.emb, '[...]') AS vec_dist
FROM paperradar.chunks c JOIN paperradar.docs d ON d.id = c.doc_id
WHERE MATCH(c.search_text) AGAINST('长文档 切分 粒度') AND d.kind = 'paper'
ORDER BY vec_dist APPROXIMATE LIMIT 5;
```

---

## 样例 2：阅读进度写入记忆并在后续复用（PowerContext 实际作用）

**初始数据**：PowerContext Server 已启动，首次访问会自动创建名为 `paperradar` 的 Scope。

**操作步骤**：
1. 检索任意问题，在结果卡片上点击「标记已读」（或「在读」）；
2. 切到「文献库」标签页，确认该文献状态下拉框已变化；
3. 刷新页面（相当于一次新会话），重新检索同一问题——该文献仍显示「已读」；
4. 在「技术证据」标签页查看 `memory.create` / `memory.replace` 记录，带 `revision`。

**预期结果**：
- 状态跨会话保持：因为真源在 PowerContext 的 atomic-memory（`kind=reading-status`），seekdb 中的 `docs.status` 只是镜像列，服务启动后每次都会从记忆同步；
- 每次修改生成新的 revision（历史可追溯）。

**核对技术作用**：

```bash
# 列出所有阅读状态记忆（scope_id 以 /api/health 返回为准）
curl -s http://127.0.0.1:8000/v1/scopes -o /dev/null -w "%{http_code}\n"
curl -s -X POST http://127.0.0.1:8000/v1/atomic-memory/list \
  -H "Content-Type: application/json" \
  -d '{"scope_id":"<scope_id>","states":["active"],"kind":"reading-status","limit":50}'
```

期望返回包含 `{"doc_id":"p_rag_2020","status":"reading",...}` 等条目。

---

## 样例 3：研究画像影响排序与每日雷达

**操作步骤**：
1. 右侧「研究画像」中，把「关注点」改为 `评测, 位置偏差`，点击「保存到记忆」；
2. 重新检索「长文档切分粒度到底怎么选？」；
3. 切到「每日雷达」标签页。

**预期结果**：
- 结果卡片的「为什么排在这」出现 `符合你的关注点：评测`；含评测主题的片段（BEIR、Lost in the Middle）排序上升；
- 每日雷达按画像召回**未读**文献，推荐理由包含「命中关注点：评测」等字样；
- 关闭「推荐时优先未读」并保存后，雷达不再把「你还没读过」作为理由。

**核对技术作用**：画像是一条 `kind=research-profile` 的原子记忆，修改会推进 revision：

```bash
curl -s -X POST http://127.0.0.1:8000/v1/atomic-memory/list \
  -H "Content-Type: application/json" \
  -d '{"scope_id":"<scope_id>","states":["active"],"kind":"research-profile","limit":5}'
```

---

## 自动化自检

```bash
python scripts/smoke_test.py
```

覆盖 10 项断言（seekdb 连接与数据量、PowerContext 连接、三种模式检索、search_log 落库、记忆写入读回、状态镜像同步、每日雷达、记忆检索），全部 PASS 为通过。

> 提示：接口返回中的候选数、耗时与排序会随语料与机器略有差异，以「逻辑正确、可复现」为准。
