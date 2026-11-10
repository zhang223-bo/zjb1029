"""把规格、代码和测试放进 seekdb，再按任务把查询结果取出来。

seekdb 走 MySQL 协议（默认 2881）。工程资料只存在这里，
PowerContext 不承担工程资料的存储。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pymysql

ROOT = Path(__file__).resolve().parent.parent

DOCUMENTS = (
    {
        "doc_key": "spec:items",
        "kind": "spec",
        "source_path": "specs/items.md",
        "symbol": "筛选",
        "title": "阅读列表规格",
    },
    {
        "doc_key": "code:items.list",
        "kind": "code",
        "source_path": "src/items.py",
        "symbol": "items.list",
        "title": "阅读列表实现",
    },
    {
        "doc_key": "test:items",
        "kind": "test",
        "source_path": "tests/test_items.py",
        "symbol": "test_filter_by_status",
        "title": "阅读列表测试",
    },
)

_COLUMNS = ("body", "title", "symbol", "source_path")
KINDS = ("spec", "code", "test")


def tokenize(task: str) -> list[str]:
    """任务拆成英文词和连续中文，去掉重复。"""
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", task)
    phrases = re.findall(r"[\u4e00-\u9fff]{2,}", task)
    tokens: list[str] = []
    for token in words + phrases:
        if token not in tokens:
            tokens.append(token)
    return tokens


def escape_like(token: str) -> str:
    """转义 LIKE 通配符，避免任务里的 % 和 _ 被当成通配。"""
    return token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def connect(database: str | None = None):
    name = database_name() if database is None else database
    return pymysql.connect(
        host=os.environ.get("SEEKDB_HOST", "127.0.0.1"),
        port=int(os.environ.get("SEEKDB_PORT", "2881")),
        user=os.environ.get("SEEKDB_USER", "root"),
        password=os.environ.get("SEEKDB_PASSWORD", ""),
        database=name or None,
        charset="utf8mb4",
        autocommit=True,
        connect_timeout=8,
        cursorclass=pymysql.cursors.DictCursor,
    )


def database_name() -> str:
    name = os.environ.get("SEEKDB_DATABASE", "minimax_sdd")
    if not re.fullmatch(r"[A-Za-z0-9_]+", name):
        raise RuntimeError("SEEKDB_DATABASE 只允许字母、数字和下划线")
    return name


def preflight() -> str:
    """连一次库并读版本。连不上时抛出带处置建议的 RuntimeError。"""
    host = os.environ.get("SEEKDB_HOST", "127.0.0.1")
    port = os.environ.get("SEEKDB_PORT", "2881")
    try:
        db = connect("")
    except pymysql.MySQLError as error:
        raise RuntimeError(
            f"连不上 seekdb {host}:{port}：{error}。先按 RUNBOOK.md 启动 seekdb。"
        ) from error
    try:
        with db.cursor() as cursor:
            cursor.execute("SELECT VERSION() AS v")
            version = cursor.fetchone()["v"]
    finally:
        db.close()
    return f"seekdb {host}:{port} 可用，版本 {version}"


def init_database() -> None:
    admin = connect("")
    try:
        name = database_name()
        with admin.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{name}`")
    finally:
        admin.close()
    db = connect()
    try:
        with db.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS engineering_docs (
                    doc_key VARCHAR(128) PRIMARY KEY,
                    kind VARCHAR(32) NOT NULL,
                    source_path VARCHAR(256) NOT NULL,
                    symbol VARCHAR(128) NOT NULL,
                    title VARCHAR(256) NOT NULL,
                    body TEXT NOT NULL,
                    imported_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
    finally:
        db.close()


def import_documents(spec_path: str = "specs/items.md") -> list[str]:
    """把三份资料写进 seekdb。规格可换另一份文件，正文整体覆盖。"""
    db = connect()
    loaded: list[str] = []
    try:
        with db.cursor() as cursor:
            for document in DOCUMENTS:
                path = document["source_path"]
                if document["doc_key"] == "spec:items":
                    path = spec_path
                body = (ROOT / path).read_text(encoding="utf-8")
                cursor.execute(
                    """
                    INSERT INTO engineering_docs
                        (doc_key, kind, source_path, symbol, title, body)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        kind = VALUES(kind),
                        source_path = VALUES(source_path),
                        symbol = VALUES(symbol),
                        title = VALUES(title),
                        body = VALUES(body),
                        imported_at = CURRENT_TIMESTAMP
                    """,
                    (
                        document["doc_key"],
                        document["kind"],
                        path,
                        document["symbol"],
                        document["title"],
                        body,
                    ),
                )
                loaded.append(document["doc_key"])
    finally:
        db.close()
    return loaded


def search(task: str) -> tuple[str, list[str], list[dict]]:
    """按任务里的词查工程资料，返回 SQL、参数和命中行。"""
    tokens = tokenize(task)
    if not tokens:
        raise RuntimeError("任务里没有可检索的词")
    clauses = []
    params: list[str] = []
    for token in tokens:
        pattern = f"%{escape_like(token)}%"
        for column in _COLUMNS:
            clauses.append(f"{column} LIKE %s")
            params.append(pattern)
    sql = (
        "SELECT doc_key, kind, source_path, symbol, title, body "
        "FROM engineering_docs WHERE " + " OR ".join(clauses)
    )
    db = connect()
    try:
        with db.cursor() as cursor:
            cursor.execute(sql, params)
            rows = list(cursor.fetchall())
    finally:
        db.close()
    rank = {kind: index for index, kind in enumerate(KINDS)}
    rows.sort(key=lambda row: (rank.get(row["kind"], 9), row["doc_key"]))
    return sql, params, rows
