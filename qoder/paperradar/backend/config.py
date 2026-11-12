"""集中配置：全部通过环境变量覆盖，默认值可直接跑通本地演示。"""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
WEB_DIR = PROJECT_ROOT / "web"
LOG_DIR = PROJECT_ROOT / "logs"

# --- seekdb（向量 + 全文 + 结构化混合检索） ---
SEEKDB_HOST = os.getenv("SEEKDB_HOST", "127.0.0.1")
SEEKDB_PORT = int(os.getenv("SEEKDB_PORT", "2881"))
SEEKDB_USER = os.getenv("SEEKDB_USER", "root")
SEEKDB_PASSWORD = os.getenv("SEEKDB_PASSWORD", "")
SEEKDB_DB = os.getenv("SEEKDB_DB", "paperradar")

# --- PowerContext（长期记忆 / 上下文服务） ---
POWERCONTEXT_BASE_URL = os.getenv("POWERCONTEXT_BASE_URL", "http://127.0.0.1:8000")
POWERCONTEXT_TOKEN = os.getenv("POWERCONTEXT_TOKEN", "")
POWERCONTEXT_SCOPE = os.getenv("POWERCONTEXT_SCOPE", "paperradar")
POWERCONTEXT_TIMEOUT = float(os.getenv("POWERCONTEXT_TIMEOUT", "20"))

# --- 用户与模型 ---
USER_ID = os.getenv("PAPERRADAR_USER", "u_demo")
EMBED_DIM = 384  # all-MiniLM-L6-v2 输出维度，与 seekdb 表结构一致

CHUNK_MAX_CHARS = int(os.getenv("CHUNK_MAX_CHARS", "420"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "80"))
TOP_K = int(os.getenv("TOP_K", "8"))
