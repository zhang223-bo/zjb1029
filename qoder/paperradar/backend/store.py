"""seekdb 访问层：建表、写入、以及带耗时统计的查询封装。

设计要点（对应赛题的「技术结合」）：
- 向量、全文、结构化条件放在同一张 chunks 表里，一条 SQL 完成混合检索，
  不在应用层拼结果；
- 每次检索把真实执行的 SQL、参数、耗时写进 search_log，评审可回放核对。
"""

import json
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Iterable

import pymysql

from .config import (
    EMBED_DIM,
    SEEKDB_DB,
    SEEKDB_HOST,
    SEEKDB_PASSWORD,
    SEEKDB_PORT,
    SEEKDB_USER,
)

_local = threading.local()

SCHEMA = [
    f"""
    CREATE TABLE IF NOT EXISTS docs (
        id          VARCHAR(48) PRIMARY KEY,
        kind        VARCHAR(16)  NOT NULL,
        title       VARCHAR(512) NOT NULL,
        authors     VARCHAR(512) DEFAULT '',
        venue       VARCHAR(128) DEFAULT '',
        year        INT DEFAULT 0,
        tags        VARCHAR(256) DEFAULT '',
        abstract    TEXT,
        url         VARCHAR(512) DEFAULT '',
        status      VARCHAR(16) DEFAULT 'unread',
        added_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
        KEY idx_kind (kind),
        KEY idx_year (year),
        KEY idx_status (status)
    ) DEFAULT CHARSET = utf8mb4
    """,
    f"""
    CREATE TABLE IF NOT EXISTS chunks (
        id          VARCHAR(96) PRIMARY KEY,
        doc_id      VARCHAR(48) NOT NULL,
        seq         INT NOT NULL DEFAULT 0,
        section     VARCHAR(128) DEFAULT '',
        page        INT DEFAULT 0,
        content     TEXT NOT NULL,
        search_text TEXT NOT NULL,
        emb         VECTOR({EMBED_DIM}),
        FULLTEXT INDEX ft_search (search_text) WITH PARSER ngram,
        VECTOR INDEX vidx_emb (emb) WITH (DISTANCE=cosine, TYPE=hnsw, LIB=vsag),
        KEY idx_doc (doc_id)
    ) DEFAULT CHARSET = utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS search_log (
        id          VARCHAR(48) PRIMARY KEY,
        ts          DATETIME DEFAULT CURRENT_TIMESTAMP,
        query       VARCHAR(512) NOT NULL,
        mode        VARCHAR(16) NOT NULL,
        filters     VARCHAR(512) DEFAULT '',
        sql_text    TEXT,
        top_docs    VARCHAR(512) DEFAULT '',
        candidates  INT DEFAULT 0,
        elapsed_ms  FLOAT DEFAULT 0,
        memory_used VARCHAR(512) DEFAULT ''
    ) DEFAULT CHARSET = utf8mb4
    """,
]


class SeekDB:
    def __init__(self, database: str = SEEKDB_DB):
        self.database = database

    # ---------- 连接 ----------
    def _connect(self, with_db: bool = True):
        kwargs: dict[str, Any] = dict(
            host=SEEKDB_HOST,
            port=SEEKDB_PORT,
            user=SEEKDB_USER,
            password=SEEKDB_PASSWORD,
            charset="utf8mb4",
            autocommit=True,
            connect_timeout=10,
        )
        if with_db:
            kwargs["database"] = self.database
        return pymysql.connect(**kwargs)

    def conn(self):
        conn = getattr(_local, "conn", None)
        if conn is None:
            conn = self._connect()
            _local.conn = conn
        else:
            try:
                conn.ping(reconnect=True)
            except Exception:
                conn = self._connect()
                _local.conn = conn
        return conn

    # ---------- 基础查询 ----------
    def query(self, sql: str, args: Iterable | None = None) -> tuple[list[dict], float]:
        start = time.perf_counter()
        with self.conn().cursor() as cur:
            cur.execute(sql, tuple(args) if args else None)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description] if cur.description else []
        elapsed = (time.perf_counter() - start) * 1000
        return [dict(zip(cols, r)) for r in rows], elapsed

    def execute(self, sql: str, args: Iterable | None = None) -> tuple[int, float]:
        start = time.perf_counter()
        with self.conn().cursor() as cur:
            affected = cur.execute(sql, tuple(args) if args else None)
        return affected, (time.perf_counter() - start) * 1000

    def executemany(self, sql: str, rows: list[tuple]) -> float:
        start = time.perf_counter()
        with self.conn().cursor() as cur:
            cur.executemany(sql, rows)
        return (time.perf_counter() - start) * 1000

    # ---------- 初始化 ----------
    def reset(self) -> None:
        """重建表结构（演示数据可随时重建）。"""
        for table in ("chunks", "search_log", "docs"):
            self.execute(f"DROP TABLE IF EXISTS {table}")

    def init_schema(self) -> dict:
        """建库建表；已存在则跳过。返回各步骤是否新建。"""
        created: dict[str, bool] = {}
        admin = self._connect(with_db=False)
        try:
            with admin.cursor() as cur:
                cur.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{self.database}` "
                    "DEFAULT CHARACTER SET utf8mb4"
                )
        finally:
            admin.close()
        for idx, ddl in enumerate(SCHEMA):
            name = ["docs", "chunks", "search_log"][idx]
            rows, _ = self.query(
                "SELECT COUNT(*) AS c FROM information_schema.tables "
                "WHERE table_schema = %s AND table_name = %s",
                [self.database, name],
            )
            existed = bool(rows and rows[0]["c"])
            self.execute(ddl)
            created[name] = not existed
        return created

    # ---------- 写入 ----------
    def upsert_doc(self, doc: dict) -> None:
        self.execute(
            """
            INSERT INTO docs (id, kind, title, authors, venue, year, tags, abstract, url, status, added_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
            ON DUPLICATE KEY UPDATE
                title=VALUES(title), authors=VALUES(authors), venue=VALUES(venue),
                year=VALUES(year), tags=VALUES(tags), abstract=VALUES(abstract),
                url=VALUES(url)
            """,
            [
                doc["id"], doc["kind"], doc["title"], doc.get("authors", ""),
                doc.get("venue", ""), int(doc.get("year") or 0), doc.get("tags", ""),
                doc.get("abstract", ""), doc.get("url", ""), doc.get("status", "unread"),
            ],
        )

    def replace_chunks(self, doc_id: str, rows: list[tuple]) -> None:
        self.execute("DELETE FROM chunks WHERE doc_id = %s", [doc_id])
        if rows:
            self.executemany(
                "INSERT INTO chunks (id, doc_id, seq, section, page, content, search_text, emb) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                rows,
            )

    def set_status(self, doc_id: str, status: str) -> None:
        self.execute("UPDATE docs SET status = %s WHERE id = %s", [status, doc_id])

    def log_search(
        self,
        query_text: str,
        mode: str,
        filters: dict,
        sql_text: str,
        top_docs: list[str],
        candidates: int,
        elapsed_ms: float,
        memory_used: str,
    ) -> str:
        log_id = uuid.uuid4().hex[:16]
        self.execute(
            "INSERT INTO search_log (id, query, mode, filters, sql_text, top_docs, candidates, elapsed_ms, memory_used) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            [
                log_id, query_text[:500], mode, json.dumps(filters, ensure_ascii=False)[:500],
                sql_text[:4000], ",".join(top_docs)[:500], candidates, round(elapsed_ms, 3),
                memory_used[:500],
            ],
        )
        return log_id

    def recent_searches(self, limit: int = 20) -> list[dict]:
        rows, _ = self.query(
            "SELECT id, ts, query, mode, filters, candidates, elapsed_ms, top_docs, memory_used "
            "FROM search_log ORDER BY ts DESC LIMIT %s",
            [int(limit)],
        )
        return rows

    def stats(self) -> dict:
        rows, _ = self.query(
            "SELECT (SELECT COUNT(*) FROM docs) AS docs, (SELECT COUNT(*) FROM chunks) AS chunks, "
            "(SELECT COUNT(*) FROM search_log) AS searches"
        )
        return rows[0] if rows else {}


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
