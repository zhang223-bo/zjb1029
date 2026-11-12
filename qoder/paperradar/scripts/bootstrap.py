"""一键初始化：建表 + 把 data/ 里的论文与笔记入库（切块、向量化、建索引）。

用法：
    python scripts/bootstrap.py            # 使用 .env（如有）与默认配置
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 读取项目根目录的 .env（不引入额外依赖）
env_file = ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from backend.ingest import index_all  # noqa: E402
from backend.store import SeekDB  # noqa: E402


def main() -> int:
    db = SeekDB()
    print("== 连接 seekdb ==", f"{db.database}")
    created = db.init_schema()
    print("表状态：", {k: ("新建" if v else "已存在") for k, v in created.items()})
    print("== 入库（首次运行会下载 ~87MB 嵌入模型并缓存到本地）==")
    result = index_all(db)
    print(f"完成：{result['docs']} 篇文献 / {result['chunks']} 个片段")
    print(db.stats())
    return 0


if __name__ == "__main__":
    sys.exit(main())
