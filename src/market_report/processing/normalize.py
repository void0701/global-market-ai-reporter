"""記事の正規化.

タイトルを比較しやすい形に落とし、要約を一律で切り詰める（docs/08 F-3）。
``summary_raw`` は切り詰めずに保存し、切り詰めは AI へ渡す直前に行う
（docs/11 §2 — 保存は最大限・入力は選別）。
"""

from __future__ import annotations

import re
import unicodedata

from ..config import Settings
from ..models import Article
from ..utils.text import truncate_chars

# 「 - Reuters」「| CNBC」のような媒体名サフィックス
_SOURCE_SUFFIX_RE = re.compile(
    r"\s*[\-–—|｜:]\s*"
    r"(reuters|cnbc|bloomberg|the new york times|nytimes|wsj|the wall street journal|"
    r"marketwatch|bbc|bbc news|financial times|ft|the guardian|seeking alpha|yahoo finance|"
    r"associated press|ap)\s*$",
    re.IGNORECASE,
)
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")

# 比較のノイズになる一般語
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "from",
        "by",
        "with",
        "without",
        "about",
        "into",
        "over",
        "after",
        "before",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "their",
        "his",
        "her",
        "they",
        "we",
        "you",
        "will",
        "would",
        "can",
        "could",
        "may",
        "might",
        "shall",
        "should",
        "must",
        "do",
        "does",
        "did",
        "has",
        "have",
        "had",
        "not",
        "no",
        "nor",
        "s",
        "t",
        "re",
        "ve",
        "ll",
        "d",
        "m",
        "say",
        "says",
        "said",
        "new",
        "news",
        "update",
        "updates",
        "live",
        "report",
        "reports",
        "reported",
    ]
)


def normalize_title(title: str) -> str:
    """比較用のタイトル. 媒体名サフィックス除去 → 記号除去 → 小文字化."""
    s = unicodedata.normalize("NFKC", title or "")
    prev = None
    while prev != s:  # 「- Reuters | CNBC」のような二重サフィックス
        prev = s
        s = _SOURCE_SUFFIX_RE.sub("", s).strip()
    s = _PUNCT_RE.sub(" ", s).lower()
    return _WS_RE.sub(" ", s).strip()


def title_tokens(normalized: str) -> frozenset[str]:
    """Jaccard 係数を取るためのトークン集合."""
    return frozenset(tok for tok in normalized.split() if len(tok) > 1 and tok not in _STOPWORDS)


def prepare(articles: list[Article]) -> list[Article]:
    """正規化タイトルを埋め、タイトルが空になったものを落とす."""
    out: list[Article] = []
    for art in articles:
        art.title_normalized = normalize_title(art.title)
        if art.title_normalized:
            out.append(art)
    return out


def summary_for_ai(article: Article, settings: Settings) -> str:
    """AI に渡す要約。全フィード一律で切り詰める（Guardian の 665 字対策）."""
    return truncate_chars(article.summary_raw, settings.summary_truncate_chars)
