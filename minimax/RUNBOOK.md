# 运行说明

从干净环境按顺序执行。需要 Docker（跑 seekdb）和 Python 3.11+（跑 PowerContext 与模块）。

## 1. 启动 seekdb

```bash
docker run -d --name seekdb -p 2881:2881 oceanbase/seekdb
```

镜像拉不动时换备用源：`quay.io/oceanbase/seekdb` 或 `ghcr.io/oceanbase/seekdb`。

等容器就绪再往下走：

```bash
docker logs -f seekdb          # 等到可以接受连接
mysql -h 127.0.0.1 -P 2881 -u root -e "SELECT VERSION()"
```

默认用户 `root`、空密码。若设了密码，后面所有步骤都要带 `SEEKDB_PASSWORD`。

## 2. 启动 PowerContext

```bash
uv tool install --force "powercontext[cli,server]==1.5.1"
mkdir -p powercontext-config && cd powercontext-config
powercontext config init --language zh --output .env    # 存储选 SQLite 即可
powercontext config validate --env-file .env
powercontext service install --env-file .env
powercontext ready
```

不想装成常驻服务时，前台启动：`powercontext server run --env-file .env`。

`powercontext ready` 输出 `ready` 表示可用。访问控制保持默认关闭；若开了认证，把访问令牌放进 `POWERCONTEXT_TOKEN`。

PowerContext 的存储用 SQLite 不违反题目要求——工程资料在 seekdb，PowerContext 只存任务状态。

## 3. 配置本模块

```bash
export SEEKDB_HOST=127.0.0.1
export SEEKDB_PORT=2881
export SEEKDB_USER=root
export SEEKDB_PASSWORD=
export SEEKDB_DATABASE=minimax_sdd
export POWERCONTEXT_URL=http://127.0.0.1:8000
export POWERCONTEXT_TOKEN=
```

Windows PowerShell 用 `$env:SEEKDB_HOST="127.0.0.1"` 这样的写法。

## 4. 安装依赖并自检

```bash
cd minimax
python -m pip install -r requirements.txt
python -m minimax_sdd doctor
```

`doctor` 逐项打印 seekdb 和 PowerContext 的状态。任一项 `[fail]`，退出码为 1，并给出该做什么。

## 5. 初始化数据

```bash
python -m minimax_sdd init
```

建库 `minimax_sdd`、建表 `engineering_docs`、导入三份资料（规格 / 代码 / 测试），并新建一个空的 PowerContext scope，`scope_id` 写入 `var/scope_id`。

## 6. 跑验收

```bash
python -m minimax_sdd verify
```

成功时最后打印 `verify ok`，退出码 0。当次证据写在 `records/run/`：

| 文件 | 内容 |
|---|---|
| `test_run_before.log` | 改前测试，退出码 1，断言失败 |
| `test_run_after.log` | 改后测试，退出码 0 |
| `context-before.txt` | 空状态下的上下文 |
| `context-updated.txt` | 重新导入更新规格后的上下文 |
| `seekdb-queries.txt` | 查询语句、参数、命中行 |
| `seekdb-queries-updated.txt` | 更新规格后的同一查询 |
| `powercontext-state.txt` | 写入响应、检索结果、重启后的上下文 |

## 7. 调用入口

模块入口是 CLI，输入任务文本，输出开发上下文：

```bash
python -m minimax_sdd context --task "为阅读列表增加 status 筛选"
```

输出包含四段：适用规格（正文来自 seekdb）、要改的代码（符号与来源）、测试（功能与回归，来源 `tests/test_items.py`）、已完成与待办（来自 PowerContext）。

也可以分步执行：

```bash
python -m minimax_sdd test-before
python -m minimax_sdd test-after
python -m minimax_sdd import-spec specs/items-updated.md
python -m minimax_sdd context --task "为阅读列表增加 status 筛选"
python -m minimax_sdd import-spec specs/items.md
python -m minimax_sdd remember
python -m minimax_sdd context --task "为阅读列表增加 status 筛选"
```

`test-before` 退出码是 1，其余是 0。

## 8. 测试命令

```bash
# 只跑不依赖外部服务的部分（10 个用例）
PYTHONPATH=src python -m unittest discover -s tests -p "test_*.py" -v

# 只跑业务功能与回归测试，指定代码版本
PYTHONPATH=snapshots/before python -m unittest discover -s tests -p test_items.py -v
PYTHONPATH=src python -m unittest discover -s tests -p test_items.py -v
```

## 9. 重置

```bash
python -m minimax_sdd reset
```

规格恢复为 `specs/items.md`，并换一个没有验证记忆的新 scope。之后可再次执行 `verify`。

## 已知限制

- seekdb 的嵌入式模式（`pylibseekdb`）只支持 Linux。Windows 上必须用 Docker 起服务端，本模块按 MySQL 协议连接。
- 检索是 `LIKE` 子串匹配，任务措辞与资料用词差异大时可能命中不全；上下文会标出缺失项而不是报错中断。
- 只支持单条任务的上下文构造，未做多任务并发。
- `updated_at` 取 `YYYY-MM-DD`，跨年跨月依赖字符串顺序。
