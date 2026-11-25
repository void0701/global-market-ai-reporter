"""パイプラインのオーケストレーション.

``Lambda ハンドラ（Phase 2）はこのモジュールを呼ぶだけ`` になるよう、AWS への
依存を一切持たせない。公開先は :class:`~market_report.publishing.base.Publisher`
プロトコル越しに扱うため、ローカル → S3 の差し替えで本モジュールは無変更
（docs/01 §3-1）。
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from . import DAILY_SCHEMA_VERSION, RAW_SCHEMA_VERSION
from .analysis import aggregate
from .analysis.analyzer import Analyzer, compose_x_post, daily_prefix, period_prefix
from .analysis.client import LlmResult
from .collectors import market as market_mod
from .collectors import rss
from .config import Settings
from .models import (
    DailyRecord,
    LlmCallRecord,
    PeriodRecord,
    PeriodType,
    RawNewsRecord,
)
from .processing import dedupe, normalize
from .publishing import store
from .publishing.base import Publisher
from .publishing.local import LocalPublisher
from .rendering.html import Renderer, month_title
from .utils import logging as log

logger = logging.getLogger(__name__)

SPX = "^GSPC"

# 明示しないとループ変数が str に推論され、PeriodType キーの dict に使えない
PERIOD_TYPES: tuple[PeriodType, ...] = ("weekly", "monthly", "yearly")
LATEST_PERIOD_PAGES: tuple[tuple[PeriodType, str], ...] = (
    ("weekly", "weekly.html"),
    ("monthly", "monthly.html"),
)


def default_publisher(settings: Settings) -> Publisher:
    return LocalPublisher(Path(settings.output_dir))


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _llm_record(
    target: str,
    kind: str,
    settings: Settings,
    system: str,
    user: str,
    result: LlmResult | None,
    dry_run: bool,
) -> LlmCallRecord:
    """プロンプト全文を含む呼び出し記録（docs/11 §3）."""
    record = LlmCallRecord(
        schema_version=RAW_SCHEMA_VERSION,
        date=target,
        kind=kind,
        model=settings.model_id,
        region=settings.region,
        system_prompt=system,
        user_prompt=user,
        max_tokens=settings.max_tokens,
        dry_run=dry_run,
    )
    if result is not None:
        record.output_schema_hash = result.schema_hash
        record.stop_reason = result.stop_reason
        record.thinking_summary = result.thinking_summary
        record.content_raw = result.content_raw
        record.input_tokens = result.input_tokens
        record.output_tokens = result.output_tokens
        record.latency_ms = result.latency_ms
        record.estimated_cost_usd = result.cost_usd
    return record


# ----------------------------------------------------------------- 日次


def run_daily(
    settings: Settings,
    *,
    target_date: date | None = None,
    dry_run: bool = False,
    publisher: Publisher | None = None,
    provider: market_mod.MarketDataProvider | None = None,
    publish_index: bool = True,
) -> DailyRecord:
    target = target_date or datetime.now().astimezone().date()
    pub = publisher or default_publisher(settings)
    run_at = datetime.now(UTC)

    # 1. 収集
    window_from, window_to = rss.window_for(run_at, settings.window_hours)
    feed_results, articles = rss.collect(settings, window_from, window_to)

    # 2. 正規化 + 重複排除（判定結果は articles に書き戻される）
    articles = normalize.prepare(articles)
    selected, dedupe_info = dedupe.run(articles, settings)

    # 3. 生データを全件保存（重複排除で捨てたものも含む / docs/11 A-84）
    store.dump(
        pub,
        store.raw_news_key(target),
        RawNewsRecord(
            schema_version=RAW_SCHEMA_VERSION,
            date=target.isoformat(),
            collected_at=_now_iso(),
            window_from=window_from.isoformat(),
            window_to=window_to.isoformat(),
            feeds=feed_results,
            articles=articles,
            dedupe=dedupe_info,
        ),
    )

    # 4. 市況（全40系列を保存、AI には15系列のみ渡す）
    #    07:00 JST = 前日 17:00 ET なので、参照するのは前日までのセッション
    snapshot = market_mod.fetch_snapshot(settings, market_mod.market_asof_for(target), provider)
    store.dump(pub, store.raw_market_key(target), snapshot)

    # 5. AI 解析
    analyzer = Analyzer(settings, dry_run=dry_run)
    analysis, result, system, user = analyzer.analyze_daily(target, snapshot, selected)
    store.dump(
        pub,
        store.raw_llm_key(target, "daily"),
        _llm_record(target.isoformat(), "daily", settings, system, user, result, dry_run),
    )

    # 6. 保存（期間レポートの入力になる）
    record = DailyRecord(
        schema_version=DAILY_SCHEMA_VERSION,
        date=target.isoformat(),
        generated_at=_now_iso(),
        model=settings.model_id if not dry_run else f"{settings.model_id} (dry-run)",
        dry_run=dry_run,
        market=snapshot,
        analysis=analysis,
        x_post_full=compose_x_post(
            daily_prefix(snapshot, target), analysis.x_post_text, settings.x_weight_limit
        ),
        article_counts={
            "収集": dedupe_info.input_count,
            "クラスタ": dedupe_info.cluster_count,
            "AI投入": dedupe_info.selected_count,
            "フィード成功": sum(1 for r in feed_results if r.error is None),
            "フィード鮮度警告": sum(1 for r in feed_results if r.stale),
        },
    )
    store.dump(pub, store.daily_key(target), record)

    # 7. HTML
    renderer = Renderer(settings)
    pub.put_text(store.report_key(target), renderer.daily(record, rel="../"))
    # Lambda では配信側が build_site() を 1 回呼んで両方こなすため、
    # ここでは呼ばない（同じ全件読み込みを 2 回したくない / A-160）
    if publish_index:
        rebuild_index(settings, pub)

    log.info(
        logger,
        "日次レポート完了",
        date=record.date,
        dry_run=dry_run,
        cost_usd=result.cost_usd if result else 0.0,
        input_tokens=result.input_tokens if result else 0,
        output_tokens=result.output_tokens if result else 0,
        location=pub.location,
    )
    return record


# ----------------------------------------------------------------- 期間


class InsufficientDataError(RuntimeError):
    """期間レポートの入力が不足している（異常ではない / docs/09 §7-3）."""


_SUB_PERIOD: dict[PeriodType, PeriodType | None] = {
    "weekly": None,
    "monthly": "weekly",
    "yearly": "monthly",
}


def _sub_labels(period_type: PeriodType, start: date, end: date) -> list[str]:
    """1 つ下の階層のラベル一覧（月次 ← 週次 / 年次 ← 月次）."""
    sub = _SUB_PERIOD[period_type]
    if sub == "weekly":
        labels: list[str] = []
        seen: set[str] = set()
        cur = start
        while cur <= end:
            iso = cur.isocalendar()
            label = f"{iso.year}-W{iso.week:02d}"
            if label not in seen:
                seen.add(label)
                labels.append(label)
            cur += timedelta(days=1)
        return labels
    if sub == "monthly":
        return [f"{start.year}-{m:02d}" for m in range(1, 13)]
    return []


def run_period(
    settings: Settings,
    period_type: PeriodType,
    *,
    label: str | None = None,
    target_date: date | None = None,
    dry_run: bool = False,
    publisher: Publisher | None = None,
    publish_index: bool = True,
) -> PeriodRecord:
    pub = publisher or default_publisher(settings)
    target = target_date or datetime.now().astimezone().date()
    period_label = label or aggregate.period_label_for(period_type, target)
    start, end = aggregate.period_bounds(period_type, period_label)

    # 統計は常に日次の全データから集計する（AI 入力は階層的に蒸留するが
    # 数値は欠落させない / docs/09 §1-2）
    daily_records = [
        rec
        for d in store.list_daily_dates(pub)
        if start <= d <= end and (rec := store.load_daily(pub, d)) is not None
    ]
    if not daily_records:
        raise InsufficientDataError(
            f"{period_label}（{start} 〜 {end}）の日次データが 0 件です。"
            "運用開始直後は正常な状態です"
        )

    stats = aggregate.build_stats(daily_records, settings, start, end)

    # AI への入力は 1 つ下の階層から（週次は日次から）
    sub = _SUB_PERIOD[period_type]
    source_labels: list[str] = []
    summaries: list[str] = []
    if sub:
        for sub_label in _sub_labels(period_type, start, end):
            sub_record = store.load_period(pub, sub, sub_label)
            if sub_record:
                source_labels.append(sub_label)
                summaries.append(aggregate.period_source_summary(sub_record))
    if not summaries:
        if sub:
            log.warn(
                logger,
                "下位期間レポートが無いため日次から直接集計する",
                period=period_label,
                sub=sub,
            )
        source_labels = [r.date for r in daily_records]
        summaries = [aggregate.source_summary(r) for r in daily_records]

    analyzer = Analyzer(settings, dry_run=dry_run)
    analysis, result, system, user = analyzer.analyze_period(
        period_type, period_label, start, end, stats, summaries
    )
    store.dump(
        pub,
        store.raw_llm_key(end, period_type),
        _llm_record(period_label, period_type, settings, system, user, result, dry_run),
    )

    record = PeriodRecord(
        schema_version=DAILY_SCHEMA_VERSION,
        period_type=period_type,
        period_label=period_label,
        period_start=start.isoformat(),
        period_end=end.isoformat(),
        generated_at=_now_iso(),
        model=settings.model_id if not dry_run else f"{settings.model_id} (dry-run)",
        dry_run=dry_run,
        source_labels=source_labels,
        stats=stats,
        analysis=analysis,
        x_post_full=compose_x_post(
            period_prefix(period_type, period_label, stats),
            analysis.x_post_text,
            settings.x_weight_limit,
        ),
    )
    store.dump(pub, store.period_key(period_type, period_label), record)

    renderer = Renderer(settings)
    pub.put_text(
        store.period_report_key(period_type, period_label), renderer.period(record, rel="../")
    )
    # Lambda では配信側が build_site() を 1 回呼んで両方こなすため、
    # ここでは呼ばない（同じ全件読み込みを 2 回したくない / A-160）
    if publish_index:
        rebuild_index(settings, pub)

    log.info(
        logger,
        "期間レポート完了",
        period_type=period_type,
        label=period_label,
        days=stats.days,
        sources=len(source_labels),
        cost_usd=result.cost_usd if result else 0.0,
    )
    return record


# ----------------------------------------------------------------- 索引


def _signed(value: float, digits: int = 0) -> str:
    """センチメントの符号付き表示。0 は日本語の慣習に合わせて ``±0``."""
    if value == 0:
        return "±0" if digits == 0 else f"±{0:.{digits}f}"
    return f"{value:+.{digits}f}"


def _daily_item(record: DailyRecord) -> dict:
    change = record.market.change_pct(SPX)
    return {
        "href": f"reports/{record.date}.html",
        "label": record.date,
        "score": record.analysis.sentiment_score,
        "score_text": _signed(record.analysis.sentiment_score),
        "note": record.analysis.sentiment_summary[:70]
        + (f"（S&P500 {change:+.2f}%）" if change is not None else ""),
    }


def _period_item(record: PeriodRecord) -> dict:
    folder = record.period_type
    change = record.stats.spx_period_change_pct
    avg = record.stats.sentiment_avg
    return {
        "href": f"{folder}/{record.period_label}.html",
        "label": record.period_label,
        "score": avg,
        "score_text": _signed(avg, 1) if avg is not None else "—",
        "note": (f"S&P500 {change:+.2f}% / " if change is not None else "")
        + record.analysis.sentiment_trend_summary[:60],
    }


def build_site(settings: Settings, publisher: Publisher) -> dict[str, str]:
    """公開サイト全体を ``{キー: HTML}`` で組み立てる.

    **保存済みのレコードから毎回すべて描き直す**（docs/05 A-141）。Amplify の
    手動デプロイはサイト全体の置き換えであり、毎回全ページが必要になるため
    （docs/13 §8 R-3）。レコードの読み込みはどうせ全件必要なので、HTML を
    S3 から読み直すより再描画するほうが要求数が少ない。

    レポートが 1 本も無ければ空の dict を返す。生成失敗時に ``index.html`` を
    壊さない方針（docs/05 A-54）はこれで保たれる。
    """
    renderer = Renderer(settings)

    dates = store.list_daily_dates(publisher)
    daily_records = store.load_daily_many(publisher, dates)

    period_records: dict[PeriodType, list[PeriodRecord]] = {}
    for ptype in PERIOD_TYPES:
        labels = store.list_period_labels(publisher, ptype)
        period_records[ptype] = store.load_period_many(publisher, ptype, labels)

    if not daily_records:
        return {}

    pages: dict[str, str] = {}

    # 個別ページ（サブディレクトリに置くので rel=".."）
    for record in daily_records:
        key = store.report_key(date.fromisoformat(record.date))
        pages[key] = renderer.daily(record, rel="..")
    for ptype in PERIOD_TYPES:
        for period in period_records[ptype]:
            key = store.period_report_key(ptype, period.period_label)
            pages[key] = renderer.period(period, rel="..")

    # 最新ページ（root に置くので rel=""）
    pages["index.html"] = renderer.daily(daily_records[-1], rel="")
    for ptype, filename in LATEST_PERIOD_PAGES:
        records = period_records[ptype]
        if records:
            pages[filename] = renderer.period(records[-1], rel="")

    pages["archive.html"] = renderer.archive(*_archive_context(daily_records, period_records))
    return pages


def rebuild_index(settings: Settings, publisher: Publisher) -> None:
    """``index.html`` / ``weekly.html`` / ``monthly.html`` / ``archive.html`` を更新する.

    個別レポートは生成時に 1 回書くだけなので、ここでは書き直さない
    （S3 の ``reports/`` は生成時点のスナップショット。配信に出るのは
    :func:`build_site` が描き直したもの）。
    """
    pages = build_site(settings, publisher)
    for key in ("index.html", "weekly.html", "monthly.html", "archive.html"):
        html = pages.get(key)
        if html is not None:
            publisher.put_text(key, html)


def _archive_context(
    daily_records: list[DailyRecord],
    period_records: dict[PeriodType, list[PeriodRecord]],
) -> tuple[dict[str, list], dict[str, Any]]:
    """アーカイブページに渡す ``(sections, latest)`` を組み立てる."""
    sections: dict[str, list] = {}
    sections["daily"] = _group_by_month(
        [_daily_item(r) for r in daily_records],
        [date.fromisoformat(r.date) for r in daily_records],
    )
    sections["weekly"] = _group_by_month(
        [_period_item(w) for w in period_records["weekly"]],
        [date.fromisoformat(w.period_start) for w in period_records["weekly"]],
    )
    sections["monthly"] = _group_by_year(
        [_period_item(m) for m in period_records["monthly"]],
        [date.fromisoformat(m.period_start) for m in period_records["monthly"]],
    )
    sections["yearly"] = (
        [
            {
                "title": "年次",
                "open": True,
                "entries": [_period_item(y) for y in period_records["yearly"]],
            }
        ]
        if period_records["yearly"]
        else []
    )

    latest = {
        "daily": _daily_item(daily_records[-1]) if daily_records else None,
        "weekly": _period_item(period_records["weekly"][-1]) if period_records["weekly"] else None,
        "monthly": _period_item(period_records["monthly"][-1])
        if period_records["monthly"]
        else None,
        "yearly": _period_item(period_records["yearly"][-1]) if period_records["yearly"] else None,
    }
    return sections, latest


def _group_by_month(items: list[dict], keys: list[date]) -> list[dict]:
    """年月の見出しでグルーピングし、直近 1 か月だけ展開する（docs/05 A-55）."""
    groups: dict[tuple[int, int], list[dict]] = {}
    for item, key in zip(items, keys, strict=False):
        groups.setdefault((key.year, key.month), []).append(item)
    ordered = sorted(groups.items(), reverse=True)
    out = []
    for idx, ((year, mon), group_items) in enumerate(ordered):
        out.append(
            {
                "title": month_title(date(year, mon, 1)),
                "open": idx == 0,
                "entries": sorted(group_items, key=lambda i: i["label"], reverse=True),
            }
        )
    return out


def _group_by_year(items: list[dict], keys: list[date]) -> list[dict]:
    groups: dict[int, list[dict]] = {}
    for item, key in zip(items, keys, strict=False):
        groups.setdefault(key.year, []).append(item)
    ordered = sorted(groups.items(), reverse=True)
    return [
        {
            "title": f"{year}年",
            "open": idx == 0,
            "entries": sorted(group_items, key=lambda i: i["label"], reverse=True),
        }
        for idx, (year, group_items) in enumerate(ordered)
    ]
