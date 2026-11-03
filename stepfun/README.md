# 迷你知识库 · 端侧向量化 + seekdb 混合检索

seekdb × 阶跃星辰 赛题：构建一个在浏览器本地完成向量化、使用 seekdb 进行语义与全文混合检索的迷你知识库应用。

## 架构

```
┌─────────────────── 浏览器（用户设备）───────────────────┐
│  Transformers.js v4 + ONNX Runtime Web                  │
│  文档/查询向量化全部本地完成（WebGPU FP16 → WASM INT8 降级）│
└──────────────┬──────────────────────────────────────────┘
               │ REST（仅传向量与文本，不做推理）
┌──────────────▼──────────────────────────────────────────┐
│  Node API（server.mjs, 127.0.0.1:8787）                  │
│  seekdb-js → seekdb（MySQL 兼容协议 / 嵌入式模式）         │
│  向量、原文、元数据存储；hybridSearch 库内 RRF 混合检索     │
└─────────────────────────────────────────────────────────┘
```

- **模型**：`Xenova/bge-small-zh-v1.5`（BAAI bge-small-zh-v1.5 的 ONNX 移植，512 维），从魔搭社区下载，浏览器缓存复用
- **推理**：全部在浏览器端侧完成，服务端不调用任何云端推理 API
- **检索**：向量 + 全文混合检索由 seekdb 在库内完成（RRF 融合），客户端不做两路结果拼合

## 启动步骤

依赖：Node.js 20+，主流现代浏览器 Chrome / Edge 最新版。

```bash
# 1. 安装依赖
npm install

# 2. 启动 API 服务（连接 seekdb）
npm run api

# 3. 启动前端（另开一个终端）
npm run web

# 4. 浏览器打开 http://127.0.0.1:5173/
```

首次打开页面会从魔搭下载模型（INT8 约 23MB 或 FP16 约 45MB，视推理后端而定），之后走浏览器缓存。

### seekdb 连接配置（环境变量，可选）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `SEEKDB_MODE` | `server` | `server` 或 `embedded` |
| `SEEKDB_HOST` | `192.168.10.128` | server 模式地址 |
| `SEEKDB_PORT` | `2881` | server 模式端口 |
| `SEEKDB_USER` / `SEEKDB_PASSWORD` | `root` / 空 | 连接凭据 |
| `SEEKDB_DATABASE` | `test` | 数据库名 |
| `SEEKDB_PATH` | `./data/seekdb.db` | 嵌入式模式数据库文件 |
| `EMBED_DIM` | `512` | 向量维度 |

嵌入式模式示例：`SEEKDB_MODE=embedded npm run api`（依赖 `@seekdb/js-bindings` 原生绑定，支持 win32-x64 / linux-x64 / linux-arm64 / darwin-arm64）。

## 功能

- 知识库集合管理：新建 / 切换 / 删除，当前集合状态持久化（localStorage）
- 文档导入：粘贴文本（空行分段）或上传 .txt / .md 文件，长文本自动切分（400 字符，50 重叠）
- 端侧向量化：分批推理（批大小 8–64 可选），实时进度与吞吐显示；同文本 FNV-1a 稳定 id，增量导入自动去重（upsert）
- 混合检索：查询向量本地计算 → seekdb 库内全文 + 向量 RRF 融合，结果高亮关键词
- 性能面板：模型加载、向量化吞吐、查询向量化、seekdb 检索、端到端延迟的分阶段计时与中位数统计

## 检索质量评测

```bash
npm run eval
```

在 Node 环境分别用 FP32 与 INT8（q8）模型在固定语料/查询集上计算 Recall@5 与 MRR@5，输出量化前后对比。结果与详细方法见 `docs/TECHNICAL_NOTES.md`。

## 目录结构

```
stepfun/
├── index.html            # 前端页面
├── main.js               # 端侧推理 + 应用逻辑
├── style.css
├── server.mjs            # Node API + seekdb-js 接入
├── vite.config.js        # 前端构建 / /ort/ 本地 WASM 托管
├── ort/                  # ONNX Runtime Web WASM 文件（本地托管）
├── scripts/
│   └── eval-quality.mjs  # 量化前后检索质量评测（Recall@K / MRR@K）
└── docs/
    └── TECHNICAL_NOTES.md # 一页技术说明
```
