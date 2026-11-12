"""文本归一化与切分。

关于中文全文检索：seekdb 的全文索引支持 `WITH PARSER ngram`，按字符 n-gram 建索引，
因此不需要额外分词器，中文短词（如「切分」「粒度」）也能命中。我们只做归一化：
小写、去标点、压缩空白，保证写入与查询走同一套规则。
"""

import re

import jieba

from .config import CHUNK_MAX_CHARS, CHUNK_OVERLAP

_NOISE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)
_SPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    text = _NOISE.sub(" ", (text or "").lower())
    return _SPACE.sub(" ", text).strip()


def build_search_text(text: str) -> str:
    """写入 search_text 列的内容，与查询串使用同一套归一化规则。"""
    return normalize(text)


# 口语里的提问词会污染 ngram 全文匹配（「怎么选」会命中一切含「怎么办」的文本），
# 查询侧先做一次轻量分词并去掉这些词，正文侧不受影响。
_QUERY_STOPWORDS = {
    "怎么", "怎样", "如何", "什么", "哪些", "为什么", "为啥", "到底", "请问", "帮我",
    "推荐", "找一下", "找", "想看", "想", "看看", "该", "应该", "可以", "吗", "呢",
    "的", "了", "是", "在", "我", "有", "和", "与", "及", "关于", "一下", "这个", "那个",
}


def build_match_query(query: str) -> str:
    """全文检索的查询串：jieba 轻分词 + 去提问词，避免口语噪声。"""
    words: list[str] = []
    for w in jieba.cut(normalize(query)):
        w = w.strip()
        if not w or w in _QUERY_STOPWORDS:
            continue
        if len(w) == 1 and not w.isdigit() and not w.isascii():
            continue
        words.append(w)
    return " ".join(words) if words else normalize(query)


def split_paragraphs(text: str) -> list[str]:
    parts = re.split(r"\n\s*\n", text.strip())
    return [p.strip() for p in parts if p.strip()]


def chunk_text(text: str, max_chars: int = CHUNK_MAX_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """先按段落聚合，超长再按句子切，保证不把一句话劈开。"""
    paragraphs = split_paragraphs(text)
    if not paragraphs:
        return []

    chunks: list[str] = []
    buf = ""
    for para in paragraphs:
        if len(buf) + len(para) + 2 <= max_chars:
            buf = f"{buf}\n\n{para}".strip()
            continue
        if buf:
            chunks.append(buf)
            buf = ""
        if len(para) <= max_chars:
            buf = para
            continue
        sentences = re.split(r"(?<=[。！？；.!?])\s*", para)
        cur = ""
        for sent in sentences:
            if not sent:
                continue
            if len(cur) + len(sent) <= max_chars:
                cur += sent
            else:
                if cur:
                    chunks.append(cur)
                cur = (cur[-overlap:] if overlap and cur else "") + sent
        if cur:
            chunks.append(cur)
    if buf:
        chunks.append(buf)
    return [c.strip() for c in chunks if len(c.strip()) > 20]


def iter_chunks(title: str, sections) -> list[tuple[str, int, str]]:
    """产 (小节名, 页码, 文本块)，并把所属小节标题拼进块首，避免检索后失去上下文。"""
    out: list[tuple[str, int, str]] = []
    for sec in sections:
        name = sec.get("section") or "正文"
        page = int(sec.get("page") or 0)
        for piece in chunk_text(sec.get("text", "")):
            out.append((name, page, f"【{title} · {name}】{piece}"))
    return out
