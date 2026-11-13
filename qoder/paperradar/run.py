"""PaperRadar 一键启动：环境体检 -> 必要时拉起 seekdb -> 建表入库 -> 起服务。

用法：
    python run.py                 # 体检 + 自动初始化 + 启动，默认 http://127.0.0.1:8099
    python run.py --check         # 只做体检，不启动服务（评审复现前先跑这个）
    python run.py --port 8100     # 换端口
    python run.py --reingest      # 强制重新切块入库

设计目标：评审从零拿到仓库后，只需要 `python run.py` 一条命令就能看到界面。
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# 读取项目根目录的 .env（不引入额外依赖）
_env_file = ROOT / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

OK = "[ ok ]"
WARN = "[warn]"
FAIL = "[fail]"

SEEKDB_CANDIDATES = [
    os.getenv("SEEKDB_BIN", ""),
    r"C:\Program Files\seekdb\bin\seekdb.exe",
    "/usr/local/bin/seekdb",
    "/opt/seekdb/bin/seekdb",
]


def port_open(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def find_seekdb() -> str | None:
    for path in SEEKDB_CANDIDATES:
        if path and Path(path).exists():
            return path
    return shutil.which("seekdb")


def check_python_deps() -> bool:
    missing = []
    for mod in ("fastapi", "uvicorn", "pymysql", "jieba", "httpx"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        print(f"{FAIL} 缺少 Python 依赖：{', '.join(missing)}")
        print("       修复：pip install -r requirements.txt")
        return False
    print(f"{OK} Python 依赖齐全（fastapi/uvicorn/pymysql/jieba/httpx）")
    return True


def start_seekdb(host: str, port: int) -> bool:
    """本机装了 seekdb 且端口没起来时，帮评审起一个本地实例。"""
    binary = find_seekdb()
    if not binary:
        print(f"{FAIL} seekdb 端口 {host}:{port} 未监听，且本机找不到 seekdb 可执行文件")
        print("       方案 A（推荐，本机安装）：https://github.com/oceanbase/seekdb 下载后重跑本脚本")
        print("       方案 B（Docker）：docker run -d --name seekdb -p 2881:2881 oceanbase/seekdb:latest")
        print("       方案 C（连远程）：在 .env 里设置 SEEKDB_HOST / SEEKDB_PORT")
        return False

    base_dir = ROOT / "data" / "seekdb"
    base_dir.mkdir(parents=True, exist_ok=True)
    print(f"{WARN} seekdb 未运行，尝试用 {binary} 在本机拉起（数据目录 {base_dir}）")
    kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008  # DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(
        [binary, "--base-dir", str(base_dir), "--port", str(port), "--nodaemon"],
        **kwargs,
    )
    for _ in range(40):
        time.sleep(1)
        if port_open(host, port):
            print(f"{OK} seekdb 已启动（{host}:{port}）")
            return True
    print(f"{FAIL} seekdb 启动超时，请检查 {base_dir} 下的日志")
    return False


def check_powercontext() -> bool:
    from backend.config import POWERCONTEXT_BASE_URL

    try:
        import httpx

        r = httpx.get(f"{POWERCONTEXT_BASE_URL}/health/ready", timeout=3)
        if r.status_code < 400:
            print(f"{OK} PowerContext 可用（{POWERCONTEXT_BASE_URL}）")
            return True
    except Exception:
        pass
    print(f"{WARN} PowerContext 未连上（{POWERCONTEXT_BASE_URL}）——检索仍可用，记忆相关功能会降级")
    print("       启动方式：powercontext server run --env-file .env  （见 docs/RUNBOOK.md）")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="PaperRadar 一键启动")
    parser.add_argument("--host", default=os.getenv("APP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("APP_PORT", "8099")))
    parser.add_argument("--check", action="store_true", help="只做环境体检")
    parser.add_argument("--reingest", action="store_true", help="强制重新切块入库")
    args = parser.parse_args()

    from backend.config import SEEKDB_HOST, SEEKDB_PORT

    print("=" * 62)
    print("PaperRadar 环境检查")
    print("=" * 62)
    deps_ok = check_python_deps()
    if not deps_ok:
        return 2

    if not port_open(SEEKDB_HOST, SEEKDB_PORT):
        if not start_seekdb(SEEKDB_HOST, SEEKDB_PORT):
            return 3
    else:
        print(f"{OK} seekdb 端口已监听（{SEEKDB_HOST}:{SEEKDB_PORT}）")

    from backend.ingest import index_all
    from backend.store import SeekDB

    db = SeekDB()
    try:
        db.init_schema()
        print(f"{OK} 表结构就绪（库 {db.database}）")
    except Exception as exc:
        print(f"{FAIL} 无法连接 seekdb 或建表失败：{exc}")
        return 4

    stats = db.stats()
    chunks = (stats.get("chunks") or {}).get("chunks", 0) if isinstance(stats.get("chunks"), dict) else 0
    if args.reingest or not chunks:
        print(f"{WARN} 索引为空，开始入库（首次会下载约 87MB 嵌入模型并缓存到本地）")
        result = index_all(db)
        print(f"{OK} 入库完成：{result['docs']} 篇文献 / {result['chunks']} 个片段")
        stats = db.stats()
    else:
        print(f"{OK} 已入库：{chunks} 个片段，跳过入库（需要重建用 --reingest）")

    check_powercontext()

    print("-" * 62)
    print("数据概况：", stats)
    if args.check:
        print(f"{OK} 体检通过，可执行 `python run.py` 启动服务")
        return 0

    import uvicorn

    print(f"{OK} 启动服务：http://{args.host}:{args.port}")
    print("-" * 62)
    uvicorn.run("backend.main:app", host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
