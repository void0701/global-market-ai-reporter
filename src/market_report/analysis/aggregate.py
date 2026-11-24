"""期間レポートの機械集計.

統計値は **AI に計算させず決定的に集計する**（docs/09 §1-3）。理由は 3 つ。

1. 平均や累積騰落率は決定的に計算できる。AI に委ねると誤り、検証もできない
2. 集計済みの数値を AI への入力に含めれば、事実を取り違えずに記述できる
3. Bedrock が失敗しても統計サマリ付きのレポートは生成できる
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import date, timedelta

from ..config import Settings
from ..models import DailyRecord, PeriodRecord, PeriodStats, PeriodType
from ..utils import logging as log
from . import scoring

logger = logging.getLogger(__name__)

SPX = "^GSPC"
ACWI = "ACWI"
USDJPY = "USDJPY=X"
VIX = "^VIX"
TNX = "^TNX"

# 日次を実行する JST 曜日（火〜土）。日・月は米国の取引セッションを含まない
RUN_WEEKDAYS = frozenset({1, 2, 3, 4, 5})  # Mon=0


def _baseline_and_last(
    records: list[DailyRecord], symbol: str
) -> tuple[float | None, float | None]:
    """期間の起点（最初の日の前日終値）と終点（最後の日の終値）."""
    firsts = [r.market.get(symbol) for r in records]
    quotes = [q for q in firsts if q and q.close is not None]
    if not quotes:
        return None, None
    base = quotes[0].prev_close if quotes[0].prev_close is not None else quotes[0].close
    return base, quotes[-1].close


def _period_change_pct(records: list[DailyRecord], symbol: str) -> float | None:
    base, last = _baseline_and_last(records, symbol)
    if not base or last is None:
        return None
    return round((last / base - 1) * 100, 4)


def _expected_run_dates(start: date, end: date) -> list[date]:
    """期間内の日次実行予定日（JST 火〜土）."""
    out, cur = [], start
    while cur <= end:
        if cur.weekday() in RUN_WEEKDAYS:
            out.append(cur)
        cur += timedelta(days=1)
    return out


def _fill_rates(records: list[DailyRecord]) -> tuple[dict[str, float], float | None]:
    """フィールド充填率の監視（docs/11 §6-3）.

    充填率が極端なフィールドは「モデルが機械的に埋めている」サイン。
    """
    items = [i for r in records for i in r.analysis.news_analysis]
    if not items:
        return {}, None
    total = len(items)
    rates = {
        "affected_sectors": sum(1 for i in items if i.affected_sectors) / total,
        "mentioned_tickers": sum(1 for i in items if i.mentioned_tickers) / total,
        "key_numbers": sum(1 for i in items if i.key_numbers) / total,
        "surprise_known": sum(1 for i in items if i.surprise_direction != "no_consensus") / total,
        "category_not_other": sum(1 for i in items if i.news_category != "other") / total,
        "source_urls": sum(1 for i in items if i.source_urls) / total,
    }
    avg_chars = sum(len(i.transmission) for i in items) / total
    return {k: round(v, 4) for k, v in rates.items()}, round(avg_chars, 1)


def build_stats(
    records: list[DailyRecord],
    settings: Settings,
    period_start: date,
    period_end: date,
) -> PeriodStats:
    """日次レコード群から統計サマリを組み立てる."""
    records = sorted(records, key=lambda r: r.date)
    stats = PeriodStats(days=len(records))
    if not records:
        return stats

    # --- センチメント ---
    spx_scores = [r.analysis.sentiment_score for r in records]
    acwi_scores = [r.analysis.acwi_sentiment_score for r in records]
    stats.sentiment_avg = round(sum(spx_scores) / len(spx_scores), 2)
    stats.sentiment_min = min(spx_scores)
    stats.sentiment_max = max(spx_scores)
    stats.sentiment_series = [(r.date, r.analysis.sentiment_score) for r in records]
    stats.acwi_sentiment_avg = round(sum(acwi_scores) / len(acwi_scores), 2)
    stats.acwi_sentiment_series = [(r.date, r.analysis.acwi_sentiment_score) for r in records]

    # --- 市況 ---
    stats.spx_period_change_pct = _period_change_pct(records, SPX)
    stats.acwi_period_change_pct = _period_change_pct(records, ACWI)
    stats.usdjpy_period_change_pct = _period_change_pct(records, USDJPY)

    acwi_base, acwi_last = _baseline_and_last(records, ACWI)
    fx_base, fx_last = _baseline_and_last(records, USDJPY)
    if acwi_base and acwi_last and fx_base and fx_last:
        stats.jpy_global_period_change_pct = round(
            ((acwi_last * fx_last) / (acwi_base * fx_base) - 1) * 100, 4
        )
        stats.contribution_equity_pct = stats.acwi_period_change_pct
        stats.contribution_fx_pct = stats.usdjpy_period_change_pct
    if stats.spx_period_change_pct is not None and stats.acwi_period_change_pct is not None:
        stats.spx_vs_acwi_gap_pp = round(
            stats.spx_period_change_pct - stats.acwi_period_change_pct, 2
        )

    daily_spx = [
        (r.date, r.market.change_pct(SPX)) for r in records if r.market.change_pct(SPX) is not None
    ]
    if daily_spx:
        stats.spx_best_day = max(daily_spx, key=lambda kv: kv[1])  # type: ignore[arg-type,return-value]
        stats.spx_worst_day = min(daily_spx, key=lambda kv: kv[1])  # type: ignore[arg-type,return-value]

    vix_values = [q.close for r in records if (q := r.market.get(VIX)) and q.close is not None]
    if vix_values:
        stats.vix_avg = round(sum(vix_values) / len(vix_values), 2)
        stats.vix_max = round(max(vix_values), 2)

    tnx_base, tnx_last = _baseline_and_last(records, TNX)
    if tnx_base is not None and tnx_last is not None:
        # ^TNX は利回りそのまま（%）。1% = 100bp（docs/10 §1 で実データ確認）
        stats.tnx_change_bp = round((tnx_last - tnx_base) * 100, 1)

    # --- 判定の分布 ---
    items = [i for r in records for i in r.analysis.news_analysis]
    stats.direction_counts = dict(Counter(i.direction for i in items))
    stats.horizon_counts = dict(Counter(i.horizon for i in items))
    stats.confidence_counts = dict(Counter(i.confidence for i in items))
    stats.news_category_counts = dict(Counter(i.news_category for i in items))
    stats.sector_counts = dict(Counter(s for i in items for s in i.affected_sectors))
    stats.surprise_counts = dict(Counter(i.surprise_direction for i in items))
    stats.novelty_counts = dict(Counter(i.novelty for i in items))
    stats.magnitude_counts = dict(Counter(i.expected_magnitude for i in items))
    stats.region_counts = dict(Counter(i.region_focus for i in items))
    stats.scope_counts = dict(Counter(i.scope for i in items))
    stats.fx_impact_counts = dict(Counter(i.fx_impact for i in items))
    stats.ticker_counts = dict(Counter(t for i in items for t in i.mentioned_tickers))

    # --- 地域 / セクター ETF（AI には渡さず保存のみの系列を活用）---
    for group, target in (
        ("region", stats.region_etf_changes),
        ("sector", stats.sector_etf_changes),
    ):
        for symbol in settings.symbols_in_group(group):
            change = _period_change_pct(records, symbol)
            if change is not None:
                target[settings.label(symbol)] = change

    # --- 予測の的中率（ベンチマーク併記）---
    stats.forecast_spx = scoring.evaluate(records, "spx")
    stats.forecast_acwi = scoring.evaluate(records, "acwi")

    # --- 品質監視 ---
    stats.field_fill_rates, stats.transmission_avg_chars = _fill_rates(records)

    have = {r.date for r in records}
    stats.missing_dates = [
        d.isoformat()
        for d in _expected_run_dates(period_start, period_end)
        if d.isoformat() not in have
    ]

    log.info(
        logger,
        "期間集計",
        days=stats.days,
        news_items=len(items),
        missing=len(stats.missing_dates),
        spx_change=stats.spx_period_change_pct,
        jpy_global_change=stats.jpy_global_period_change_pct,
    )
    return stats


def source_summary(record: DailyRecord) -> str:
    """日次レコードを期間レポートの入力テキストに落とす.

    ``report_markdown`` は**渡さない**（階層的ロールアップの要点。これを渡すと
    入力が膨れ、蒸留の意味がなくなる）。
    """
    a = record.analysis
    lines = [
        f"## {record.date}",
        f"- S&P500 センチメント: {a.sentiment_score} — {a.sentiment_summary}",
        f"- オルカン センチメント: {a.acwi_sentiment_score} — {a.acwi_sentiment_summary}",
        f"- 為替見通し: {a.fx_outlook}",
    ]
    spx = record.market.change_pct(SPX)
    jpy = record.market.global_view.jpy_theoretical_change_pct
    lines.append(f"- 実績: S&P500 {spx:+.2f}%" if spx is not None else "- 実績: S&P500 —")
    if jpy is not None:
        lines[-1] += f" / 円建て全世界株 {jpy:+.2f}%"
    for item in a.news_analysis:
        lines.append(
            f"  - [{item.horizon}/{item.direction}/確度{item.confidence}/"
            f"{item.news_category}/{item.scope}/{item.fx_impact}] {item.headline}"
        )
        lines.append(f"      {item.summary}")
        lines.append(f"      波及: {item.transmission}")
    for watch in a.watch_list:
        lines.append(f"  - [監視] {watch.label}（{watch.when}）: {watch.why}")
    return "\n".join(lines)


def period_bounds(period_type: PeriodType, label: str) -> tuple[date, date]:
    """``2026-W47`` / ``2026-11`` / ``2026`` から期間の開始日・終了日を得る."""
    if period_type == "weekly":
        year_s, week_s = label.split("-W")
        start = date.fromisocalendar(int(year_s), int(week_s), 1)
        return start, start + timedelta(days=6)
    if period_type == "monthly":
        year, month = (int(x) for x in label.split("-"))
        start = date(year, month, 1)
        end = date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1)
        return start, end
    year = int(label)
    return date(year, 1, 1), date(year, 12, 31)


def period_label_for(period_type: PeriodType, target: date) -> str:
    """対象日が属する**1 つ前**の期間ラベルを返す.

    週次は月曜に前週を、月次は 1 日に前月を、年次は 1/1 に前年を対象にする。
    """
    if period_type == "weekly":
        prev = target - timedelta(days=7)
        iso = prev.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if period_type == "monthly":
        first = target.replace(day=1)
        prev = first - timedelta(days=1)
        return f"{prev.year}-{prev.month:02d}"
    return str(target.year - 1)


def period_source_summary(record: PeriodRecord) -> str:
    """下位期間レポートを上位期間の入力テキストに落とす（階層的ロールアップ）.

    ``report_markdown`` は渡さない。蒸留済みの要素だけを上げることで、年次で
    日次 250 本を入力するような破綻を避ける（docs/09 §1-2）。
    """
    a = record.analysis
    s = record.stats
    lines = [
        f"## {record.period_label}（{record.period_start} 〜 {record.period_end} / {s.days}日）",
        f"- S&P500 期間騰落率: {_fmt_pct(s.spx_period_change_pct)} "
        f"/ センチメント平均 {s.sentiment_avg}",
        f"- 円建て全世界株: {_fmt_pct(s.jpy_global_period_change_pct)} "
        f"（株価 {_fmt_pct(s.contribution_equity_pct)} + 為替 {_fmt_pct(s.contribution_fx_pct)}）",
        f"- センチメント推移の解釈: {a.sentiment_trend_summary}",
        f"- 市況: {a.market_recap}",
        f"- オルカン: {a.global_recap}",
        f"- 予測の振り返り: {a.forecast_review}",
        f"- 見通し: {a.outlook}",
    ]
    for theme in a.key_themes:
        lines.append(f"  - [テーマ/{theme.persistence}] {theme.title}: {theme.narrative}")
    return "\n".join(lines)


def _fmt_pct(value: float | None) -> str:
    return "—" if value is None else f"{value:+.2f}%"
