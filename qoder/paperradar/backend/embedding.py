"""向量化：使用 pyseekdb 自带的默认嵌入函数（all-MiniLM-L6-v2, 384 维）。

首次调用会下载约 90MB 的 ONNX 模型并缓存到本地，之后可离线运行。
模型只在本地 CPU 上跑，不依赖任何云端 API Key。
"""

from functools import lru_cache
from typing import Sequence

from .config import EMBED_DIM


@lru_cache(maxsize=1)
def _embedding_function():
    from pyseekdb import DefaultEmbeddingFunction

    return DefaultEmbeddingFunction()


def embed_texts(texts: Sequence[str]) -> list[list[float]]:
    if not texts:
        return []
    vectors = _embedding_function()(list(texts))
    out = []
    for v in vectors:
        v = list(v)
        if len(v) != EMBED_DIM:
            raise ValueError(f"嵌入维度不一致：期望 {EMBED_DIM}，实际 {len(v)}")
        out.append(v)
    return out


def embed_query(text: str) -> list[float]:
    return embed_texts([text])[0]


def to_vec_literal(vector: Sequence[float]) -> str:
    """seekdb 接受 '[0.1,0.2,...]' 形式的字符串字面量作为 VECTOR。"""
    return "[" + ",".join(f"{x:.6f}" for x in vector) + "]"
