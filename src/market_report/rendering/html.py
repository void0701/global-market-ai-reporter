"""HTML 生成（Jinja2）.

Markdown は最小限のサブセットを自前で変換する。``markdown`` パッケージを
入れないのは、Lambda のパッケージをこれ以上増やさないため（純 Python だが
依存を増やす理由が乏しい）。
"""

from __future__ import annotations

import html as html_mod
import logging
import re
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from markupsafe import Markup

from ..config import Settings
from ..models import (
    CONFIDENCE_JA,
    DIRECTION_JA,
    FX_IMPACT_JA,
    HORIZON_JA,
    MAGNITUDE_JA,
    NEWS_CATEGORY_JA,
    NOVELTY_JA,
    PERSISTENCE_JA,
    REGION_JA,
    SCOPE_JA,
    SECTOR_JA,
    SURPRISE_JA,
    DailyRecord,
    PeriodRecord,
)
from ..utils.text import fullwidth_len

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))

TEMPLATE_DIR = Path(__file__).parent / "templates"

LABELS: dict[str, Any] = {
    "horizon": HORIZON_JA,
    "direction": DIRECTION_JA,
    "confidence": CONFIDENCE_JA,
    "news_category": NEWS_CATEGORY_JA,
    "sector": SECTOR_JA,
    "surprise": SURPRISE_JA,
    "novelty": NOVELTY_JA,
    "magnitude": MAGNITUDE_JA,
    "region": REGION_JA,
    "scope": SCOPE_JA,
    "fx_impact": FX_IMPACT_JA,
    "persistence": PERSISTENCE_JA,
    "period": {"weekly": "週次", "monthly": "月次", "yearly": "年次"},
    "period_short": {"daily": "日次", "weekly": "週次", "monthly": "月次", "yearly": "年次"},
    "period_long": {
        "daily": "日次レポート",
        "weekly": "週次レポート",
        "monthly": "月次レポート",
        "yearly": "年次レポート",
    },
}

# ---------------------------------------------------------------- Markdown

_H_RE = re.compile(r"^(#{1,4})\s+(.*)$")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_CODE_RE = re.compile(r"`([^`]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_BULLET_RE = re.compile(r"^[-*]\s+(.*)$")
_OL_RE = re.compile(r"^(\d+)\.\s+(.*)$")


def _inline(text: str) -> str:
    out = html_mod.escape(text)
    out = _LINK_RE.sub(r'<a href="\2" rel="noopener nofollow">\1</a>', out)
    out = _BOLD_RE.sub(r"<strong>\1</strong>", out)
    return _CODE_RE.sub(r"<code>\1</code>", out)


def markdown_to_html(text: str) -> Markup:
    """見出し / 段落 / 箇条書き / 強調 / インラインコード / リンクのみ対応."""
    lines = (text or "").replace("\r\n", "\n").split("\n")
    out: list[str] = []
    list_tag: str | None = None
    para: list[str] = []

    def flush_para() -> None:
        if para:
            out.append("<p>" + " ".join(para) + "</p>")
            para.clear()

    def flush_list() -> None:
        nonlocal list_tag
        if list_tag:
            out.append(f"</{list_tag}>")
            list_tag = None

    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            flush_para()
            flush_list()
            continue
        heading = _H_RE.match(line)
        if heading:
            flush_para()
            flush_list()
            level = min(len(heading.group(1)) + 1, 5)  # ## -> h3（h2 はテンプレ側が使う）
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            continue
        bullet = _BULLET_RE.match(line)
        ordered = _OL_RE.match(line)
        if bullet or ordered:
            flush_para()
            want = "ul" if bullet else "ol"
            if list_tag != want:
                flush_list()
                out.append(f"<{want}>")
                list_tag = want
            body = bullet.group(1) if bullet else ordered.group(2)  # type: ignore[union-attr]
            out.append(f"<li>{_inline(body)}</li>")
            continue
        flush_list()
        para.append(_inline(line.strip()))

    flush_para()
    flush_list()
    return Markup("\n".join(out))


# ---------------------------------------------------------------- filters


def _f_pct(value: float | None, unit: str = "%") -> str:
    if value is None:
        return "—"
    return f"{value:+.2f}{unit}"


def _f_num(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:,.{digits}f}"


def _f_rate(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.1f}%"


def _f_cls(value: float | int | None) -> str:
    if value is None or value == 0:
        return "flat"
    return "up" if value > 0 else "down"


def _f_host(url: str) -> str:
    try:
        return urlparse(url).netloc or url
    except ValueError:
        return url


def build_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "j2"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["pct"] = _f_pct
    env.filters["num"] = _f_num
    env.filters["rate"] = _f_rate
    env.filters["cls"] = _f_cls
    env.filters["host"] = _f_host
    env.filters["markdown"] = markdown_to_html
    env.filters["fullwidth"] = lambda s: f"{fullwidth_len(s or ''):.1f}"
    return env


def _display_timestamp(value: str | None) -> str:
    """レポートの「生成」表示に使う文字列.

    **レコードに記録された生成時刻を使う**（A-142）。レンダリング時刻にすると、
    毎朝サイト全体を作り直す設計（A-141）のもとで過去のレポートに今日の日時が
    表示されてしまう。欠落・不正な値のときだけ現在時刻に落とす。
    """
    stamp = None
    if value:
        try:
            stamp = datetime.fromisoformat(value)
        except ValueError:
            logger.warning("generated_at を解釈できません: %s", value[:40])
    if stamp is None:
        stamp = datetime.now(UTC)
    # 本システムは JST 基準（07:00 JST 実行）なので表示も JST に寄せる。
    # fromisoformat の tzinfo は名前を持たないため、%Z では "UTC+09:00" になる。
    # zoneinfo を使わないのは Lambda の tzdata 同梱に依存しないため。
    return stamp.astimezone(JST).strftime("%Y-%m-%d %H:%M JST")


class Renderer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.env = build_env()

    def _common(
        self, *, rel: str, title: str, model: str, dry_run: bool, generated_at: str | None = None
    ) -> dict[str, Any]:
        return {
            "rel": rel,
            "page_title": title,
            "generated_at": _display_timestamp(generated_at),
            "model": model,
            "dry_run": dry_run,
            "settings": self.settings,
            "labels": LABELS,
        }

    def daily(self, record: DailyRecord, *, rel: str = "") -> str:
        ctx = self._common(
            rel=rel,
            title=f"{record.date} 米国市場 AI 解析レポート",
            model=record.model,
            dry_run=record.dry_run,
            generated_at=record.generated_at,
        )
        ctx.update(record=record, a=record.analysis)
        return self.env.get_template("report.html.j2").render(**ctx)

    def period(self, record: PeriodRecord, *, rel: str = "") -> str:
        kind = LABELS["period"][record.period_type]
        ctx = self._common(
            rel=rel,
            title=f"{record.period_label} {kind}レポート",
            model=record.model,
            dry_run=record.dry_run,
            generated_at=record.generated_at,
        )
        ctx.update(record=record, a=record.analysis, s=record.stats)
        return self.env.get_template("period.html.j2").render(**ctx)

    def archive(self, sections: dict[str, list], latest: dict[str, Any]) -> str:
        ctx = self._common(
            rel="", title="アーカイブ — 米国市場 AI 解析レポート", model="", dry_run=False
        )
        ctx.update(sections=sections, latest=latest)
        return self.env.get_template("archive.html.j2").render(**ctx)


def month_title(d: date) -> str:
    return f"{d.year}年{d.month}月"
