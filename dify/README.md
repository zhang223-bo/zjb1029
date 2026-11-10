# 临港书店店员

一个用 Dify 搭建的「AI 书店店员」聊天应用：顾客像进店一样向它提问，它会依据书店设定资料回答营业时间、会员规则和本周推荐；资料里没有的问题，它会老实说「这个问题我要问一下店长」，而不是编造答案。

- 基于 [oceanbase/powercontext](https://github.com/oceanbase/powercontext) 仓库 `2026-competition-ecosystem` 分支开发，本项目全部资产位于该仓库的 `dify` 目录中。
- 使用 Dify（必选）+ seekdb（向量存储），未使用 PowerContext。

## 一、作品简介

- **创意与玩法**：以虚构的「临港书店」为背景，顾客可以直接用自然语言提问（营业时间、会员折扣能否叠加、今天推荐什么书等），店员基于知识库中的设定资料作答，并在回答下方附引用来源，能直观看到答案来自哪份资料。
- **体验方式**：在 Dify 工作室打开应用直接「预览」对话，或发布后通过 WebApp 链接体验，无需额外前端。
- **应用入口**：Dify 工作室 → 应用「临港书店店员」→ 预览 / 监控 / WebApp。

## 二、技术分工

| 组件 | 作用 |
|---|---|
| Dify 1.19.0 | 核心流程编排：Chatflow（开始 → 知识检索 → LLM → 直接回复），承担输入处理、检索调用、模型调用与结果输出 |
| seekdb r1.4.0.0 | 承载知识库向量存储：`临港书店.txt` 索引后的向量保存在 seekdb 的 `dify_vector` 库，问答时由知识检索节点读写 |
| 通义插件（阿里云百炼） | 对话模型 qwen3.7-plus；Embedding 模型 text-embedding-v3；不使用 Rerank |

说明：

- Dify 的账号、应用配置等元数据存放在 Dify 自带的 Postgres。Dify 1.19.0 迁移会建临时表，seekdb 不支持，因此 seekdb 只承载知识库向量数据。
- 全部流程节点、提示词、检索配置均在 DSL 中定义，无自定义插件、自定义前后端，未修改 Dify 源码。

## 三、运行环境与依赖

| 项目 | 版本 / 说明 |
|---|---|
| Docker + Docker Compose | 用于部署 Dify |
| Dify | 1.19.0，Docker Compose 部署 |
| seekdb | r1.4.0.0，端口 2881 |
| 插件 | langgenius/tongyi 0.2.27，从 Marketplace 安装 |
| 对话模型 | qwen3.7-plus（阿里云百炼） |
| Embedding 模型 | text-embedding-v3（阿里云百炼） |
| Rerank | 不使用 |
| 知识库数据 | `临港书店.txt`（本目录自带，自编资料） |

网络条件：Dify 所在机器需能访问 seekdb（2881 端口）和阿里云百炼服务。

## 四、部署与导入步骤

按以下顺序执行：

1. 安装 Docker，用 Docker Compose 启动 Dify 1.19.0。
2. 启动 seekdb r1.4.0.0，端口 2881；在 seekdb 中创建 `dify_vector` 库。
3. 修改 Dify 的环境配置，将 `VECTOR_STORE` 指向 seekdb（地址、端口 2881、库名 `dify_vector`），重启 Dify。账号、密码等配置项均使用占位符填写，不要把真实密钥写进仓库。具体配置项名称以所用 Dify 版本的 seekdb 存储文档为准。
4. 在 Dify「集成」中从 Marketplace 安装通义插件 0.2.27，密钥从阿里云百炼控制台获取（占位符配置）。在「设置 → 模型供应商」中把默认对话模型设为 qwen3.7-plus，默认 Embedding 模型设为 text-embedding-v3，不配置 Rerank。
5. 把本目录的 `临港书店店员.ifpkg` 拖进 Dify 工作室完成导入（仅此一个 DSL/应用包文件，无导入顺序问题）。
6. 用本目录的 `临港书店.txt` 创建知识库。分段设置：取消「替换连续换行」，分段标识符 `\n\n`，最大长度 400。
7. 在应用的编排页面，把「知识检索」节点重新绑定到上一步创建的知识库（DSL 中的 dataset_ids 是导出环境的加密标识，导入后无效，必须重新选择），召回条数保持 4。
8. 发布应用。此时索引写入与问答检索都会落在 seekdb 上。

## 五、验证

- 应用功能验证：按 `sample.md` 的验证样例新开三轮对话，核对预期回答与截图证据。
- seekdb 实际读写验证：知识库索引完成后，连接 seekdb（端口 2881）执行：

  ```sql
  USE dify_vector;
  SHOW TABLES;
  ```

  预期看到一张以 `vector_index_` 开头的表（见 `seekdb表.png`）。该表由 Dify 索引 `临港书店.txt` 时写入，问答时由知识检索节点读取，证明 seekdb 在应用运行中实际承担向量存储职责。

## 六、已知功能限制

- seekdb 不承载 Dify 元数据（账号、应用配置等在 Postgres），原因是 Dify 1.19.0 迁移会建临时表而 seekdb 不支持。
- DSL 不包含知识库内容；导入后必须按第四节的步骤 6–7 重建知识库并重新绑定，否则知识检索节点无法命中。
- 店员只依据知识库资料回答，知识库外的问题统一回复「这个问题我要问一下店长」，属于预期行为而非故障。

## 七、提交与复用说明

- 提交分支：基于 oceanbase/powercontext 仓库 `2026-competition-ecosystem` 分支开发，资产位于 `dify` 目录。
- 本目录文件清单：`临港书店店员.ifpkg`（应用包/DSL）、`临港书店.txt`（知识库数据）、`sample.md`（验证样例）、运行证据截图（`今日推荐.png`、`会员规则.png`、`店里有没有咖啡.png`、`seekdb表.png`）。
- 全部材料（应用编排、提示词、书店设定资料）均为自行编写，未复用公开工作流、代码或数据。
- 所有配置示例中的密钥均为占位符，不含真实 API Key、密码或凭据。
