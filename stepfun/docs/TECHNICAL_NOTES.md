# 一页技术说明 — 端侧 AI 推理性能优化与 seekdb 混合检索

项目：迷你知识库（浏览器端侧向量化 + seekdb 向量与全文混合检索）

## 1. 模型与量化

| 项目 | 内容 |
|---|---|
| 模型 | `Xenova/bge-small-zh-v1.5`（BAAI `bge-small-zh-v1.5` 的 ONNX 社区移植版，512 维，约 24M 参数） |
| 来源与版本 | ModelScope：`https://www.modelscope.cn/models/Xenova/bge-small-zh-v1.5`，ONNX 权重随仓库分发，浏览器经 Cache API 缓存复用 |
| 运行时 | Transformers.js v4.3.2 + ONNX Runtime Web（WASM/JSEP 文件本地托管于 `/ort/`，无外网依赖） |
| 量化方式 | ONNX 官方权重量化：WASM 路径用 INT8（`onnx/model_quantized.onnx`，q8 动态量化）；WebGPU 路径用 FP16（`onnx/model_fp16.onnx`）。均由 ONNX 社区预生成，应用按后端自动选择对应精度 |

**模型体积实测（同一口径：ONNX 单文件大小，来源魔搭仓库元数据）：**

| 精度 | 文件 | 体积 | 压缩比（相对 FP32） |
|---|---|---|---|
| FP32 基线 | `onnx/model.onnx` | 90.46 MB | 1.00x |
| FP16 | `onnx/model_fp16.onnx` | 45.30 MB | 2.00x |
| INT8（q8） | `onnx/model_quantized.onnx` | 22.90 MB | **3.95x** |

**检索质量变化（`npm run eval` 实测）：** 固定语料 20 条 + 查询 13 条（每查询标注 1 篇相关文档），CPU 环境分别用 FP32 与 INT8 做向量检索：

| 指标 | FP32 | INT8 (q8) | 变化 |
|---|---|---|---|
| Recall@5 | 0.923 | 0.923 | 0.0pp |
| MRR@5 | 0.923 | 0.923 | 0.0pp |

13 条查询中 12 条两者均命中同一文档且排名一致，1 条两者均未命中（该查询与语料表述差异较大，非量化导致）。**INT8 量化在本评测集上零质量损失。**

## 2. 端侧运行

- 文档与查询的 embedding 全部在浏览器内由 Transformers.js 执行（ONNX Runtime Web），Node 服务只接收向量与文本做 seekdb 读写代理，**不调用任何云端推理 API**。
- **WebGPU 优先**：启动时 `navigator.gpu.requestAdapter()` 探测，可用则以 `device: "webgpu", dtype: "fp16"` 创建推理管线；WebGPU 管线创建失败再尝试 `webgpu + q8`；最终兜底 `wasm + q8`。
- **自动降级**：探测不到适配器或 WebGPU 初始化抛错即降级 WASM，页面顶栏徽章明确显示当前后端（`WebGPU (FP16)` / `WASM (INT8 q8)`）与降级原因，降级后核心流程（导入/向量化/检索）完全不变。
- WASM 路径使用 SIMD + 多线程运行时（本地 `/ort/` 托管），并设置跨线程隔离头由 Vite 开发服务提供。

## 3. seekdb 使用

- **部署**：默认 server 模式（MySQL 兼容协议，`SEEKDB_HOST:2881`，`root` 用户），可 `SEEKDB_MODE=embedded` 一键切换嵌入式模式（`@seekdb/js-bindings`，本地文件 `./data/seekdb.db`），接口层零改动。
- **集合结构**：`Schema = VectorIndexConfig(HNSW, 512 维, cosine 距离, embeddingFunction=null) + FulltextIndexConfig("ik", ik_mode: smart)`。`embeddingFunction` 置空表示向量一律由浏览器端侧产生后传入，数据库不承担向量化。
- **混合检索**：`collection.hybridSearch({ query: { whereDocument: { $contains: 关键词 } }, knn: { queryEmbeddings: [查询向量] }, nResults })`——全文路与向量路都在 seekdb 库内执行并以 RRF 融合排序，客户端只提交一次请求，不获取两路结果自行拼合。查询无可用关键词时退化为库内纯向量检索（仍由 seekdb 完成）。
- **写入**：`upsert` 批量写入（64 条/批）；文档 id = 文本 FNV-1a 哈希，重复导入同文本自动去重，实现增量写入。
- **索引刷新**：seekdb 写入为事务性提交，向量与全文索引随写入同步可见；应用在每次导入后调用 `/api/refresh` 以 count 验证可见性并计时，保证"新导入即可检索"。

## 4. 状态与优化

- **集合/会话状态**：服务端缓存集合连接；前端 localStorage 持久化当前集合名，刷新页面后自动恢复并校验存在性；支持新建/切换/删除集合，检索结果始终与当前集合一致。
- **批处理优化**：文档按批（8–64 可调）送入模型，摊薄单条推理固定开销、提高 GPU 并行利用率；分批执行并实时展示进度与吞吐（docs/s）。
- **模型加载优化**：浏览器 Cache 缓存模型文件，二次加载无网络流量；WASM 运行时本地托管。
- **切分策略**：长文本按段落 + 400 字符滑窗（50 重叠）切块，兼顾语义完整与检索粒度。
- **分阶段计时**：性能面板分别统计模型加载、向量化吞吐、查询向量化、seekdb 写入、seekdb 混合检索、端到端查询延迟，保留多次样本并取中位数（区分首次与预热后）。实测示例（Windows + Node 22 服务端，seekdb server 模式）：写入（含建连预热）约 1.2s，预热后混合检索 18–150ms，刷新/可见性验证约 5ms。浏览器端向量化吞吐由应用性能面板在目标设备实测展示（随设备与后端而异，附测试日志）。

## 5. 复现

见 `README.md` 启动步骤；评测复现：`npm run eval`。
