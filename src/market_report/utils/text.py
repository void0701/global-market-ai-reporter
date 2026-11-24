"""文字列ユーティリティ.

X の投稿文字数は「全角 2 / 半角 1」の重み付けで数える。本システムの要件
「全角 140 文字以内」は重み 280 に相当する（docs/04 §3）。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

# X の重み付け規則: East Asian Width が F(全角) / W(広) / A(曖昧) なら 2、他は 1
_WIDE = frozenset("FWA")

X_WEIGHT_LIMIT = 280
"""全角 140 文字相当の重み上限."""


def char_weight(ch: str) -> int:
    return 2 if unicodedata.east_asian_width(ch) in _WIDE else 1


def x_weighted_len(text: str) -> int:
    """X の数え方での長さ（全角=2 / 半角=1）."""
    return sum(char_weight(c) for c in text)


def fullwidth_len(text: str) -> float:
    """全角文字数に換算した長さ."""
    return x_weighted_len(text) / 2


def truncate_weighted(text: str, limit: int, ellipsis: str = "…") -> str:
    """重み ``limit`` 以内に決定的に切り詰める."""
    if x_weighted_len(text) <= limit:
        return text
    budget = limit - x_weighted_len(ellipsis)
    if budget <= 0:
        return ellipsis[: max(1, limit)]
    out: list[str] = []
    used = 0
    for ch in text:
        w = char_weight(ch)
        if used + w > budget:
            break
        out.append(ch)
        used += w
    return "".join(out).rstrip() + ellipsis


def truncate_chars(text: str, limit: int, ellipsis: str = "…") -> str:
    """文字数で切り詰める（RSS 要約の一律切り詰め用）."""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(ellipsis))].rstrip() + ellipsis


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(text: str) -> str:
    """RSS の description から HTML タグとエンティティを除去する."""
    import html as _html

    if not text:
        return ""
    s = _TAG_RE.sub(" ", text)
    s = _html.unescape(s)
    return _WS_RE.sub(" ", s).strip()


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def short_hash(text: str, length: int = 12) -> str:
    return sha1(text)[:length]


def pct(value: float | None, digits: int = 2, signed: bool = True) -> str:
    """パーセント表示。``None`` は ``—``."""
    if value is None:
        return "—"
    fmt = f"{{:+.{digits}f}}%" if signed else f"{{:.{digits}f}}%"
    return fmt.format(value)


def num(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:,.{digits}f}"
