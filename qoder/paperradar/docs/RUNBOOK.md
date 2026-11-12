# 运行说明（RUNBOOK）

从零到跑通的完整步骤。全部组件都在本地运行，不需要任何云服务或 API Key。

## 0. 环境要求

| 组件 | 版本 | 说明 |
| --- | --- | --- |
| Python | 3.11+ | 开发环境为 3.13 |
| seekdb | 1.3+ | 本地安装版 / Docker 均可，默认端口 2881 |
| PowerContext | master 分支 | 需要 `uv` 或 `pip`，基础模板无需模型 API |
| 操作系统 | Windows / macOS / Linux | 开发与验证环境为 Windows 11 |

磁盘：首次运行会下载约 87MB 的本地嵌入模型（ONNX，all-MiniLM-L6-v2），缓存到 `~/.cache/pyseekdb/`。

## 1. 启动 seekdb

任选其一：

```bash
# 方式 A：本地安装版（Windows 安装包 / macOS brew install seekdb）
seekdb --port 2881

# 方式 B：Docker
docker run -d --name seekdb -p 2881:2881 -v ./seekdb_data:/var/lib/oceanbase oceanbase/seekdb:latest
```

验证（MySQL 协议，默认 root 无密码）：

```bash
mysql -h127.0.0.1 -P2881 -uroot -e "SELECT VERSION();"
# 期望输出：5.7.25-OceanBase seekdb-v1.3.x
```

## 2. 启动 PowerContext Server

```bash
# 安装（需要 Python 3.11+）
pip install "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"

# 生成基础配置（SQLite 存储，无认证，不需要 Generation/Embedding 模型 API）
mkdir pc-workdir && cd pc-workdir
powercontext config init --template --output .env

# 启动（前台；Ctrl+C 停止）
powercontext server run --env-file .env
```

验证：

```bash
curl http://127.0.0.1:8000/health/ready
# 期望：{"status":"ready", ...}
```

说明：基础模板下 Topic Memory 等自动抽取能力不可用，但本项目使用的 **atomic-memory 显式读写 / 列表 / 搜索** 均正常工作，这正是项目所需的记忆能力。若要启用自动抽取，可按官方文档配置 Generation / Embedding 模型。

> 注意：服务需在本项目启动前运行；本项目对 PowerContext 不可用的情况有明确提示（界面红点 + 接口 503），但检索与入库不依赖它。

## 3. 安装并初始化本项目

```bash
cd paperradar
pip install -r requirements.txt

# 可选：cp .env.example .env 并按需修改（全部项都有默认值）
python scripts/bootstrap.py
```

`bootstrap.py` 会：
1. 在 seekdb 中创建 `paperradar` 库及 `docs` / `chunks` / `search_log` 三张表；
2. 读取 `data/papers.json`（11 篇论文）与 `data/notes/*.md`（2 篇笔记），切块、向量化、建立 ngram 全文索引与 HNSW 向量索引，写入 50+ 个片段。

期望输出：`完成：13 篇文献 / 54 个片段`。

## 4. 启动后端与前端

```bash
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8099
```

打开 `http://127.0.0.1:8099`。顶栏应显示两个绿色状态点：`seekdb 13 文献 / 54 片段` 与 `PowerContext 已连接`。

## 5. 端到端自检

```bash
python scripts/smoke_test.py
```

10 项检查全部 PASS 即为正常，覆盖：seekdb 连接与数据、PowerContext 连接、三种检索模式、search_log 落库、记忆写入与读回、状态镜像同步、每日雷达、记忆检索。

## 6. 配置项

全部通过环境变量或根目录 `.env` 配置（见 `.env.example`）：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `SEEKDB_HOST/PORT/USER/PASSWORD` | 127.0.0.1 / 2881 / root / 空 | seekdb 连接 |
| `SEEKDB_DB` | paperradar | 库名 |
| `POWERCONTEXT_BASE_URL` | http://127.0.0.1:8000 | PowerContext Server 地址 |
| `POWERCONTEXT_TOKEN` | 空 | 启用认证时填 Server Token |
| `POWERCONTEXT_SCOPE` | paperradar | Scope 名（留空自动创建；填 `scp_` 开头 ID 可指定既有 Scope） |
| `PAPERRADAR_USER` | u_demo | 写入阅读状态记忆的用户标识 |
| `CHUNK_MAX_CHARS / CHUNK_OVERLAP / TOP_K` | 420 / 80 / 8 | 切块与返回条数 |

## 7. 常见问题

**Q：首次检索报嵌入模型下载失败？**
模型从 Hugging Face 镜像下载，若网络受限可设置 `HF_ENDPOINT=https://hf-mirror.com` 后重试，或手动把模型缓存拷贝到 `~/.cache/pyseekdb/onnx_models/all-MiniLM-L6-v2`。

**Q：全文检索中文没有结果？**
确认 `chunks` 表的全文索引用了 `WITH PARSER ngram`（`SHOW INDEX FROM chunks;`）。中文短词依赖 ngram 分词器，这是本项目对 seekdb 的关键用法之一。

**Q：顶栏 PowerContext 显示红点？**
Server 未启动或地址不对；`curl http://127.0.0.1:8000/health/ready` 排查。检索功能仍可用，但画像/进度/推荐理由会缺失。

**Q：想换成自己的文献？**
保持 `data/papers.json` 结构（或直接放 Markdown 到 `data/notes/`），然后重新执行 `python scripts/bootstrap.py`（会重建表并全量入库）。
