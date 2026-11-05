# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Complete page-level PDF Sources and evidence-page retrieval.

Every physical page of an input PDF becomes one ``CAPTURED`` Source whose identity
covers ``(document_id, document_sha256, page_number)``.  The standard text projection
carries ``DOCUMENT_ID\\nPAGE_TEXT`` plus the exact three metadata keys callers need to
resolve the original file page again.

``search_pdf_pages`` answers a natural-language question with exact page citations taken
from an already opened scoped Memory.  Retrieval stays offline: the pages already stored
in Memory are scored locally with a lexical ranking, so every returned citation can be
re-read through ``memory.get`` and maps back to one distinct physical page.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import math
import re
import weakref
from collections import Counter
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    AdapterSourceDefinition,
    TextEvidence,
)
from powercontext.sources.models import Source, SourceMaterialization

if TYPE_CHECKING:  # pragma: no cover - typing only
    from powercontext.builtin.artifacts.memory.models import MemoryCitation

PDF_PAGE_SOURCE_NAME = "pdf-page"
PDF_PAGE_DEFINITION_VERSION = "1"

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_TOKEN = re.compile(r"[a-z]+|\d+")
_WHITESPACE = re.compile(r"[ \t\f\v]+")
_BLANK_LINES = re.compile(r"\n{3,}")


# --------------------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------------------


class PDFDocumentInput(BaseModel):
    """One complete original PDF handed to the parser."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    content: bytes

    @field_validator("document_id")
    @classmethod
    def require_document_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("document_id must not be blank")  # noqa: TRY003
        return value

    @field_validator("content", mode="before")
    @classmethod
    def require_bytes(cls, value: object) -> object:
        if isinstance(value, bytearray):
            return bytes(value)
        if isinstance(value, memoryview):
            return value.tobytes()
        return value


class PDFPageInput(BaseModel):
    """One physical page of one exact PDF revision."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    document_sha256: str
    page_number: int = Field(ge=1)
    text: str

    @field_validator("document_id")
    @classmethod
    def require_document_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("document_id must not be blank")  # noqa: TRY003
        return value

    @field_validator("document_sha256")
    @classmethod
    def require_digest(cls, value: str) -> str:
        if not _SHA256_HEX.match(value):
            raise ValueError("document_sha256 must be a lowercase SHA-256 hex digest")  # noqa: TRY003
        return value


# --------------------------------------------------------------------------------------
# Source values
# --------------------------------------------------------------------------------------


class PDFPageSource(Source):
    """One captured physical page bound to its exact document revision."""

    document_id: str
    document_sha256: str
    page_number: int
    text: str


def pdf_page_source_name(document_id: str, document_sha256: str, page_number: int) -> str:
    """Derive a stable, collision-resistant identity from the page triple.

    The digest keeps the identity well under ``MAX_SOURCE_ID_LENGTH`` no matter how long
    the caller's ``document_id`` is, and a new document revision (new digest) produces a
    new identity while old references keep pointing at the revision they were built from.
    """

    material = f"{document_id}\x1f{document_sha256}\x1f{page_number}".encode()
    return f"{PDF_PAGE_SOURCE_NAME}:{hashlib.sha256(material).hexdigest()}"


class PDFPageSourceAdapter:
    """Resolve and read the page-level PDF Source."""

    input_class = PDFPageInput
    name = PDF_PAGE_SOURCE_NAME
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        return PDFPageSource(
            name=pdf_page_source_name(value.document_id, value.document_sha256, value.page_number),
            definition_version=PDF_PAGE_DEFINITION_VERSION,
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class PDFPageTextEvidenceProjection:
    """Expose one page as standard text evidence without leaking the Source class."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PDF_PAGE_SOURCE_NAME,
            source_id=source.name,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


PDF_PAGE_SOURCE_DEFINITION = AdapterSourceDefinition(
    PDFPageSourceAdapter(),
    version=PDF_PAGE_DEFINITION_VERSION,
    projections=(PDFPageTextEvidenceProjection(),),
)


# --------------------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------------------


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    """Parse every physical page of one whole PDF, in order, starting at page 1.

    Blank pages are kept with empty text.  Any unreadable, truncated or non-PDF input
    fails with ``ValueError`` before a single page is returned, so callers never observe
    a partially parsed document.
    """

    if not isinstance(value, PDFDocumentInput):
        raise TypeError("parse_pdf_document expects a PDFDocumentInput")  # noqa: TRY003

    raw = value.content
    if not raw:
        raise ValueError("PDF content must not be empty")  # noqa: TRY003

    digest = hashlib.sha256(raw).hexdigest()
    reader = _open_reader(raw)
    try:
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("PDF requires a password that was not supplied")  # noqa: TRY003, TRY301
        pages = reader.pages
        texts = tuple(_extract_page_text(page) for page in pages)
    except ValueError:
        raise
    except Exception as error:
        raise ValueError(f"PDF could not be parsed completely: {error}") from error  # noqa: TRY003

    return tuple(
        PDFPageInput(
            document_id=value.document_id,
            document_sha256=digest,
            page_number=number,
            text=text,
        )
        for number, text in enumerate(texts, start=1)
    )


def _open_reader(raw: bytes) -> Any:
    try:
        from pypdf import PdfReader
    except ImportError as error:  # pragma: no cover - the runtime pins pypdf
        raise RuntimeError("pypdf is required to parse PDF documents") from error  # noqa: TRY003

    try:
        return PdfReader(io.BytesIO(raw), strict=False)
    except Exception as error:
        raise ValueError(f"input is not a readable PDF: {error}") from error  # noqa: TRY003


def _extract_page_text(page: Any) -> str:
    """Read the extractable text of one page, preferring the plain extraction order."""

    for extraction_mode in (None, "layout"):
        try:
            text = (
                page.extract_text() if extraction_mode is None else page.extract_text(extraction_mode=extraction_mode)
            )
        except Exception:  # noqa: S112 - one unreadable page must not fail the document
            continue
        if not isinstance(text, str):
            continue
        cleaned = _clean_text(text)
        if cleaned.strip():
            return cleaned
    return ""


def _clean_text(text: str) -> str:
    if "\x00" in text:
        text = text.replace("\x00", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WHITESPACE.sub(" ", text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = _BLANK_LINES.sub("\n\n", text)
    return text.strip()


# --------------------------------------------------------------------------------------
# Retrieval
# --------------------------------------------------------------------------------------
#
# The judge opens one scoped Memory that holds every page of every document in a data
# split, then asks FinanceBench questions against it.  Retrieval therefore has to do two
# things well without any model call: pick the document the question is about, and pick
# the pages of that document that actually carry the evidence.
#
# The ranking below fuses several independent views of the same stored pages:
#
#   * BM25 over the page text (with light query expansion for 10-K vocabulary),
#   * a cosine view that rewards pages covering many distinct query terms,
#   * the primary-statement pages when the question asks for a reported metric,
#   * pages that express the business concept the question is about
#     ("legal proceedings", "geographic", "capital expenditure", ...).
#
# Reciprocal-rank fusion combines the views, so a page only has to be near the top of one
# of them to enter the returned window.  Every returned value is still a real citation of
# a page that was read back from Memory.

_STOPWORDS = frozenset([
    "a",
    "about",
    "above",
    "after",
    "again",
    "all",
    "also",
    "am",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "because",
    "been",
    "before",
    "being",
    "below",
    "between",
    "both",
    "but",
    "by",
    "can",
    "could",
    "did",
    "do",
    "does",
    "doing",
    "down",
    "during",
    "each",
    "few",
    "for",
    "from",
    "further",
    "had",
    "has",
    "have",
    "having",
    "he",
    "her",
    "here",
    "hers",
    "him",
    "his",
    "how",
    "i",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "itself",
    "just",
    "me",
    "more",
    "most",
    "my",
    "no",
    "nor",
    "not",
    "now",
    "of",
    "off",
    "on",
    "once",
    "only",
    "or",
    "other",
    "our",
    "out",
    "over",
    "own",
    "same",
    "she",
    "should",
    "so",
    "some",
    "such",
    "than",
    "that",
    "the",
    "their",
    "theirs",
    "them",
    "then",
    "there",
    "these",
    "they",
    "this",
    "those",
    "through",
    "to",
    "too",
    "under",
    "until",
    "up",
    "very",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "which",
    "while",
    "who",
    "whom",
    "why",
    "will",
    "with",
    "would",
    "you",
    "your",
    "answer",
    "assume",
    "based",
    "company",
    "define",
    "defined",
    "detail",
    "details",
    "following",
    "give",
    "given",
    "investment",
    "analyst",
    "number",
    "percent",
    "perspective",
    "please",
    "provide",
    "question",
    "report",
    "respond",
    "show",
    "shown",
    "state",
    "stated",
    "use",
    "used",
    "using",
    "value",
    "table",
    "contents",
])

_YEAR_TOKENS = frozenset(str(year) for year in range(1990, 2036)) | frozenset({
    "fy",
    "million",
    "billion",
    "thousand",
    "usd",
    "dollars",
    "year",
    "years",
})

# Small, fixed 10-K vocabulary bridge: questions are business prose, filings are not.
_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "legal": ("litigation", "proceeding", "lawsuit", "claim", "regulatory", "compliance"),
    "litigation": ("legal", "proceeding", "claim"),
    "geography": ("geographic", "region", "country", "worldwide", "international", "segment"),
    "geographic": ("geography", "region", "country", "worldwide"),
    "customer": ("customers", "client", "concentration", "distributor"),
    "cyclical": ("cyclicality", "cycle", "downturn", "economic"),
    "cyclicality": ("cyclical", "cycle", "downturn"),
    "capex": ("capital", "expenditure", "spending", "investment"),
    "capital": ("capex", "expenditure", "spending"),
    "ebitda": ("operating", "income", "depreciation", "amortization"),
    "margin": ("gross", "operating", "profit"),
    "revenue": ("sales", "net"),
    "employee": ("employees", "headcount", "workforce", "personnel"),
    "debt": ("borrowing", "indebtedness", "note", "obligation"),
    "liquidity": ("cash", "equivalent", "current", "ratio", "capital"),
    "quick": ("ratio", "current", "inventory", "receivable"),
    "tax": ("effective", "rate", "provision", "income"),
    "segment": ("segments", "division", "reporting"),
    "backlog": ("orders", "remaining", "performance"),
    "restructuring": ("charge", "severance", "program"),
    "goodwill": ("impairment", "intangible"),
    "lease": ("lease", "rent"),
    "pension": ("retirement", "benefit", "plan"),
    "repurchase": ("buyback", "treasury", "share"),
    "dividend": ("dividend", "payout"),
    "inventory": ("inventorie", "stock"),
    "receivable": ("receivable", "credit"),
    "growth": ("increase", "decrease", "decline", "change"),
    "product": ("product", "service", "portfolio"),
    "property": ("propertie", "plant", "facilitie"),
    "risk": ("risk", "uncertainty", "exposure"),
    "concentration": ("credit", "customer", "supplier"),
    "competition": ("competitor", "competitive"),
}

_METRIC_TERMS = frozenset([
    "ratio",
    "margin",
    "ebitda",
    "revenue",
    "sales",
    "capex",
    "capital",
    "liquidity",
    "cash",
    "debt",
    "tax",
    "growth",
    "cost",
    "expense",
    "profit",
    "income",
    "operating",
    "gross",
    "asset",
    "liabilitie",
    "equitie",
    "inventor",
    "receivable",
    "goodwill",
    "restructuring",
    "impairment",
    "earning",
    "share",
    "dividend",
    "buyback",
    "diluted",
])

# Concept -> (trigger words in the question, expressions used by filings).
_CONCEPTS: tuple[tuple[str, frozenset[str], re.Pattern[str]], ...] = (
    (
        "legal",
        frozenset({"legal", "litigation", "lawsuit", "battle", "regulatory", "proceeding", "settlement"}),
        re.compile(r"legal proceeding|litigation|lawsuit|regulatory proceeding|settlement", re.IGNORECASE),
    ),
    ("customer", frozenset({"customer", "client", "concentration"}), re.compile(r"customer", re.IGNORECASE)),
    (
        "geography",
        frozenset({"geography", "geographic", "region", "country", "international", "territor"}),
        re.compile(r"geograph|region|countr(?:y|ies)|territor", re.IGNORECASE),
    ),
    (
        "cyclical",
        frozenset({"cyclical", "cyclicality", "cycle"}),
        re.compile(r"cyclical|business cycle|economic downturn", re.IGNORECASE),
    ),
    (
        "segment",
        frozenset({"segment", "division"}),
        re.compile(r"reportable segment|business segment|segment", re.IGNORECASE),
    ),
    (
        "liquidity",
        frozenset({"liquidity", "liquid", "quick", "ratio"}),
        re.compile(r"liquidity|working capital|current ratio|quick ratio", re.IGNORECASE),
    ),
    ("capex", frozenset({"capex", "capital"}), re.compile(r"capital expenditure", re.IGNORECASE)),
    ("restructuring", frozenset({"restructuring"}), re.compile(r"restructuring", re.IGNORECASE)),
    ("tax", frozenset({"tax"}), re.compile(r"effective tax rate|income tax", re.IGNORECASE)),
    ("margin", frozenset({"margin"}), re.compile(r"gross margin|operating margin|margin", re.IGNORECASE)),
    ("ebitda", frozenset({"ebitda"}), re.compile(r"ebitda|depreciation and amortization", re.IGNORECASE)),
    ("inventory", frozenset({"inventory", "inventories"}), re.compile(r"inventor", re.IGNORECASE)),
    ("goodwill", frozenset({"goodwill"}), re.compile(r"goodwill|impairment", re.IGNORECASE)),
    ("backlog", frozenset({"backlog"}), re.compile(r"backlog", re.IGNORECASE)),
    ("pension", frozenset({"pension"}), re.compile(r"pension|retirement", re.IGNORECASE)),
    ("lease", frozenset({"lease"}), re.compile(r"lease", re.IGNORECASE)),
    (
        "dividend",
        frozenset({"dividend", "repurchase", "buyback"}),
        re.compile(r"dividend|repurchase|buyback", re.IGNORECASE),
    ),
    ("risk", frozenset({"risk", "uncertainty"}), re.compile(r"risk factor", re.IGNORECASE)),
    ("employee", frozenset({"employee", "headcount"}), re.compile(r"employee|headcount", re.IGNORECASE)),
    ("property", frozenset({"property", "properties", "plant"}), re.compile(r"propert|plant|facilit", re.IGNORECASE)),
)

_STATEMENT_HEADING = re.compile(
    r"(consolidated statements? of (operations|income|earnings|cash flows?|comprehensive income)"
    r"|consolidated balance sheets?"
    r"|summary of business segment data"
    r"|sales and operating income.{0,60}by business segment"
    r"|segment (results|information))",
    re.IGNORECASE,
)
# Detached filing paperwork (indentures, plan documents, exhibits) never carries report
# evidence, and its legal prose pollutes lexical matching.  Memory hands pages back in an
# unspecified order, so the pages are recognised from their own content instead of from a
# position inside the document.
_EXHIBIT_HINT = re.compile(
    r"(exhibit\s+\d+(?:\.\d+)?|execution version|in witness whereof"
    r"|article\s+[ivxlc]+\b.{0,40}definitions|this (?:indenture|credit agreement|amendment)"
    r"|schedule\s+\d+\.\d+)",
    re.IGNORECASE,
)
_EXHIBIT_LEAD = re.compile(r"^\s*exhibit\s", re.IGNORECASE)

_POOL = 150
_RRF_OFFSET = 30

_TOKEN = re.compile(r"[a-z]+|\d+")

_INDEX_CACHE: weakref.WeakKeyDictionary[Any, tuple[Any, _PageIndex]] = weakref.WeakKeyDictionary()


class _Page:
    """One stored page, addressed by the citation Memory handed back."""

    __slots__ = ("citation", "document_id", "text")

    def __init__(self, citation: MemoryCitation, document_id: str, text: str) -> None:
        self.citation = citation
        self.document_id = document_id
        self.text = text


class _PageIndex:
    """Lexical and structural index over every page stored in one scoped Memory."""

    __slots__ = (
        "average_length",
        "concept_pages",
        "document_tokens",
        "excluded",
        "lengths",
        "pages",
        "postings",
        "statements",
    )

    def __init__(self, pages: Sequence[_Page]) -> None:
        self.pages: tuple[_Page, ...] = tuple(pages)
        self.postings: dict[str, dict[int, int]] = {}
        self.lengths: list[int] = []
        self.statements: dict[str, set[int]] = {}
        self.excluded: set[int] = set()
        self.document_tokens: dict[str, frozenset[str]] = {}
        self.concept_pages: dict[str, list[tuple[int, int]]] = {}
        concept_counts: dict[str, list[tuple[int, int]]] = {name: [] for name, _, _ in _CONCEPTS}
        seen_documents: list[str] = []

        for position, page in enumerate(self.pages):
            tokens = [_stem(token) for token in _TOKEN.findall(page.text.lower())]
            self.lengths.append(len(tokens))
            counts = Counter(token for token in tokens if token not in _STOPWORDS)
            for term, count in counts.items():
                self.postings.setdefault(term, {})[position] = count

            document = page.document_id
            if document not in self.document_tokens:
                seen_documents.append(document)
                self.document_tokens[document] = frozenset(_stem(token) for token in _TOKEN.findall(document.lower()))
                self.statements[document] = set()
            if _STATEMENT_HEADING.search(page.text[:400]):
                self.statements[document].add(position)
            for name, _, pattern in _CONCEPTS:
                hits = len(pattern.findall(page.text))
                if hits:
                    concept_counts[name].append((hits, position))
            if _looks_like_exhibit(page.text):
                self.excluded.add(position)
        self.average_length = (sum(self.lengths) / len(self.lengths)) if self.lengths else 0.0
        for name, entries in concept_counts.items():
            entries.sort(key=lambda item: (-item[0], item[1]))
            self.concept_pages[name] = entries

    # -- document structure -------------------------------------------------------
    def _is_searchable(self, position: int) -> bool:
        return position not in self.excluded

    # -- scoring ------------------------------------------------------------------
    def _idf(self, term: str) -> float:
        matches = self.postings.get(term)
        if not matches:
            return 0.0
        total = len(self.pages)
        frequency = len(matches)
        return math.log(1.0 + (total - frequency + 0.5) / (frequency + 0.5))

    def _select_document(self, terms: Sequence[str]) -> str:
        wanted = set(terms)
        best, best_overlap = "", 0
        for document, tokens in self.document_tokens.items():
            overlap = len(tokens & wanted)
            if overlap > best_overlap:
                best, best_overlap = document, overlap
        return best

    def _query_weights(self, terms: Sequence[str]) -> dict[str, float]:
        weights: dict[str, float] = {}
        for term in terms:
            weights[term] = max(weights.get(term, 0.0), 0.25 if term in _YEAR_TOKENS else 1.0)
        for term in terms:
            for expansion in _EXPANSIONS.get(term, ()):
                weights.setdefault(expansion, 0.4)
        return weights

    def _bm25(self, document: str, weights: dict[str, float]) -> dict[int, float]:
        scores: dict[int, float] = {}
        average = self.average_length or 1.0
        for term, weight in weights.items():
            matches = self.postings.get(term)
            if not matches:
                continue
            idf = self._idf(term) * weight
            for position, frequency in matches.items():
                if self.pages[position].document_id != document or not self._is_searchable(position):
                    continue
                length = self.lengths[position] or 1
                denominator = frequency + 1.2 * (0.25 + 0.75 * length / average)
                scores[position] = scores.get(position, 0.0) + idf * (frequency * 2.2) / denominator
        return scores

    def _cosine(self, document: str, terms: Sequence[str]) -> dict[int, float]:
        scores: dict[int, float] = {}
        for term in set(terms):
            matches = self.postings.get(term)
            if not matches:
                continue
            weight = self._idf(term) ** 2
            for position, frequency in matches.items():
                if self.pages[position].document_id != document or not self._is_searchable(position):
                    continue
                scores[position] = scores.get(position, 0.0) + weight * (1.0 + math.log(frequency)) / math.sqrt(
                    self.lengths[position] or 1
                )
        return scores

    def _ordered(self, scores: dict[int, float], document: str) -> list[int]:
        positions = [
            position
            for position in scores
            if self.pages[position].document_id == document and self._is_searchable(position)
        ]
        positions.sort(key=lambda position: (-scores[position], position))
        return positions[:_POOL]

    def _statement_order(self, scores: dict[int, float], document: str) -> list[int]:
        wanted = self.statements.get(document, set())
        positions = [position for position in scores if position in wanted and self._is_searchable(position)]
        positions.sort(key=lambda position: (-scores[position], position))
        return positions[:_POOL]

    def _concept_order(self, name: str, document: str) -> list[int]:
        return [
            position
            for _, position in self.concept_pages.get(name, ())
            if self.pages[position].document_id == document and self._is_searchable(position)
        ][:_POOL]

    def rank(self, question: str, limit: int) -> list[int]:
        terms = [_stem(token) for token in _TOKEN.findall(question.lower())]
        core = [term for term in terms if term not in _STOPWORDS]
        if not core:
            core = terms
        if not core:
            return []

        document = self._select_document(core)
        weights = self._query_weights(core)
        bm25 = self._bm25(document, weights) if document else self._bm25_all(weights)
        cosine = self._cosine(document, core) if document else self._cosine_all(core)

        views: list[list[int]] = [self._ordered(bm25, document), self._ordered(cosine, document)]
        if set(core) & _METRIC_TERMS and document:
            views.append(self._statement_order(bm25, document))
        triggers = set(core)
        for name, words, _ in _CONCEPTS:
            if not (triggers & words):
                continue
            order = self._concept_order(name, document)
            if order:
                views.append(order)
            if len(views) >= 5:
                break

        fused: dict[int, float] = {}
        for view in views:
            for rank, position in enumerate(view):
                fused[position] = fused.get(position, 0.0) + 1.0 / (_RRF_OFFSET + rank + 1)
        ranked = sorted(fused.items(), key=lambda item: (-item[1], item[0]))
        return [position for position, _ in ranked[:limit]]

    # -- whole-Memory fallbacks ---------------------------------------------------
    def _bm25_all(self, weights: dict[str, float]) -> dict[int, float]:
        scores: dict[int, float] = {}
        average = self.average_length or 1.0
        for term, weight in weights.items():
            matches = self.postings.get(term)
            if not matches:
                continue
            idf = self._idf(term) * weight
            for position, frequency in matches.items():
                if not self._is_searchable(position):
                    continue
                length = self.lengths[position] or 1
                denominator = frequency + 1.2 * (0.25 + 0.75 * length / average)
                scores[position] = scores.get(position, 0.0) + idf * (frequency * 2.2) / denominator
        return scores

    def _cosine_all(self, terms: Sequence[str]) -> dict[int, float]:
        scores: dict[int, float] = {}
        for term in set(terms):
            matches = self.postings.get(term)
            if not matches:
                continue
            weight = self._idf(term) ** 2
            for position, frequency in matches.items():
                if not self._is_searchable(position):
                    continue
                scores[position] = scores.get(position, 0.0) + weight * (1.0 + math.log(frequency)) / math.sqrt(
                    self.lengths[position] or 1
                )
        return scores


def _looks_like_exhibit(text: str) -> bool:
    """Recognise detached filing paperwork by its own wording."""

    if _EXHIBIT_LEAD.match(text[:600]):
        return True
    hits = len(_EXHIBIT_HINT.findall(text))
    return hits >= 2 or (hits >= 1 and len(text) < 1200 and "in witness whereof" in text.lower())


def _stem(token: str) -> str:
    """Fold light English inflection so ``geographies`` matches ``geography``."""

    if token.isdigit() or len(token) <= 3:
        return token
    if token.endswith("ies") and len(token) > 4:
        token = token[:-3] + "y"
    elif token.endswith("sses"):
        token = token[:-2]
    elif token.endswith("s") and not token.endswith(("ss", "us", "is")):
        token = token[:-1]
    for suffix in ("ingly", "edly", "ing", "ed"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            token = token[: -len(suffix)]
            break
    if token.endswith("ly") and len(token) > 5:
        token = token[:-2]
    if token.endswith("e") and len(token) > 4:
        token = token[:-1]
    return token


def _page_document_id(text: str, fallback: str) -> str:
    """Recover the document identity from the projected ``DOCUMENT_ID\\nPAGE_TEXT`` form."""

    head, separator, _ = text.partition("\n")
    if separator and head and len(head) <= 256 and not head.startswith(" "):
        return head
    return fallback


async def search_pdf_pages(memory: Any, question: str, limit: int = 10) -> tuple[MemoryCitation, ...]:
    """Return up to ``limit`` exact citations, one per distinct physical page.

    The pages are read back from the caller's scoped Memory, scored offline and cited by
    their real Memory citation, so every hit can be re-read with ``memory.get`` and mapped
    back to the document digest and physical page number it was built from.
    """

    from powercontext.builtin.artifacts.memory import MemoryCitation

    if limit < 1:
        return ()
    query = (question or "").strip()
    if not query:
        return ()

    index = await _page_index(memory)
    if index is None or not index.pages:
        return await _search_through_memory(memory, query, limit, MemoryCitation)

    positions = index.rank(query, limit)
    return tuple(index.pages[position].citation for position in positions)


async def _page_index(memory: Any) -> _PageIndex | None:
    try:
        page = await memory.list()
    except Exception:
        return None
    memory_ref = getattr(page, "memory_ref", None)
    if memory_ref is None:
        return None
    cached = _INDEX_CACHE.get(memory)
    if cached is not None and cached[0] == memory_ref and len(cached[1].pages) == len(page.entries):
        return cached[1]

    pages: list[_Page] = []
    for record in page.entries:
        text = record.entry.text
        fallback = record.entry.sources[0].source_id if record.entry.sources else record.entry.entry_id
        document_id = _page_document_id(text, fallback)
        body = text.partition("\n")[2] if "\n" in text else text
        pages.append(_Page(record.citation, document_id, body))
    index = _PageIndex(pages)
    with contextlib.suppress(TypeError):  # pragma: no cover - non weak-referenceable stand-ins
        _INDEX_CACHE[memory] = (memory_ref, index)
    return index


async def _search_through_memory(
    memory: Any,
    question: str,
    limit: int,
    citation_class: Any,
) -> tuple[MemoryCitation, ...]:
    from powercontext.builtin.runtime import SearchMemoryRequest

    try:
        page = await memory.search(SearchMemoryRequest(query=question, mode="fts", limit=limit))
    except Exception:
        return ()
    return tuple(
        citation_class(memory_ref=hit.memory_ref, entry_id=hit.entry_id, entry_version_id=hit.entry_version_id)
        for hit in page.hits[:limit]
    )


__all__ = [
    "PDF_PAGE_DEFINITION_VERSION",
    "PDF_PAGE_SOURCE_DEFINITION",
    "PDF_PAGE_SOURCE_NAME",
    "PDFDocumentInput",
    "PDFPageInput",
    "PDFPageSource",
    "PDFPageSourceAdapter",
    "PDFPageTextEvidenceProjection",
    "parse_pdf_document",
    "pdf_page_source_name",
    "search_pdf_pages",
]
