"""``--dry-run`` 用の合成データ.

Bedrock を呼ばずにパイプライン全段（収集 → 重複排除 → 集計 → HTML 生成）を
検証するため、実際に取得した記事と市況から決定的な擬似解析結果を組み立てる。

**課金を発生させずにテンプレート調整やデバッグを回せるようにするのが目的**で、
分析内容そのものに意味はない（docs/04 §6）。
"""

from __future__ import annotations

from datetime import date

from ..models import (
    AiDailyOutput,
    AiNewsAnalysis,
    AiPeriodOutput,
    AiPeriodTheme,
    AiWatchItem,
    Article,
    Confidence,
    Direction,
    FxImpact,
    Horizon,
    KeyNumber,
    Magnitude,
    MarketSnapshot,
    NewsCategory,
    Novelty,
    PeriodStats,
    PeriodType,
    RegionFocus,
    Scope,
    Sector,
    SurpriseDirection,
)

_DRY = "[DRY-RUN] "

# Literal を明示する。綴り間違いがその場で型エラーになり、スキーマと食い違った
# ドライラン出力が混入しない（docs/13 §8 R-4）。
_HORIZONS: tuple[Horizon, ...] = ("immediate", "short_term", "long_term")
_DIRECTIONS: tuple[Direction, ...] = ("down", "up", "volatility")
_CONFIDENCES: tuple[Confidence, ...] = ("high", "medium", "low")
_CATEGORIES: tuple[NewsCategory, ...] = (
    "monetary_policy",
    "economic_data",
    "corporate_earnings",
    "geopolitics",
    "other",
)
_SECTORS: tuple[Sector, ...] = (
    "information_technology",
    "financials",
    "energy",
    "health_care",
    "industrials",
)
_SURPRISES: tuple[SurpriseDirection, ...] = ("above", "below", "inline", "no_consensus")
_NOVELTY: tuple[Novelty, ...] = ("new", "continuing", "priced_in")
_MAGNITUDE: tuple[Magnitude, ...] = ("large", "medium", "small")
_REGIONS: tuple[RegionFocus, ...] = ("us", "global", "china", "europe", "japan")
_SCOPES: tuple[Scope, ...] = ("us_specific", "global")
_FX: tuple[FxImpact, ...] = ("yen_weaker", "yen_stronger", "neutral")


def _sentiment_from_market(snapshot: MarketSnapshot, symbol: str) -> int:
    """実際の騰落率から決定的にスコアを作る（-5〜+5）."""
    change = snapshot.change_pct(symbol)
    if change is None:
        return 0
    return max(-5, min(5, int(round(change))))


def daily_output(
    target_date: date,
    snapshot: MarketSnapshot,
    articles: list[Article],
    max_news: int,
) -> AiDailyOutput:
    spx = snapshot.change_pct("^GSPC")
    jpy = snapshot.global_view.jpy_theoretical_change_pct
    news: list[AiNewsAnalysis] = []
    for idx, art in enumerate(articles[:max_news]):
        news.append(
            AiNewsAnalysis(
                headline=f"{_DRY}{art.title}",
                summary=(
                    f"{_DRY}これはドライラン用の擬似分析です。実際の市場予想との比較は"
                    f"行われていません。媒体: {art.feed_name}"
                ),
                transmission=(
                    f"{_DRY}擬似的な波及経路の説明。長期金利の変化を通じた株式の"
                    f"割引率への影響を想定したサンプル文です。"
                ),
                horizon=_HORIZONS[idx % len(_HORIZONS)],
                direction=_DIRECTIONS[idx % len(_DIRECTIONS)],
                confidence=_CONFIDENCES[idx % len(_CONFIDENCES)],
                news_category=_CATEGORIES[idx % len(_CATEGORIES)],
                affected_sectors=[_SECTORS[idx % len(_SECTORS)]],
                mentioned_tickers=art.tickers[:3],
                surprise_direction=_SURPRISES[idx % len(_SURPRISES)],
                novelty=_NOVELTY[idx % len(_NOVELTY)],
                expected_magnitude=_MAGNITUDE[idx % len(_MAGNITUDE)],
                key_numbers=[
                    KeyNumber(
                        label=f"{_DRY}擬似指標", actual="", consensus="", previous="", unit=""
                    )
                ],
                region_focus=_REGIONS[idx % len(_REGIONS)],
                scope=_SCOPES[idx % len(_SCOPES)],
                fx_impact=_FX[idx % len(_FX)],
                source_urls=[art.link] if art.link else [],
            )
        )

    body = (
        f"{_DRY}S&P500 は{spx:+.2f}%、円建て全世界株は{jpy:+.2f}%。"
        if spx is not None and jpy is not None
        else f"{_DRY}市況データ不足。"
    )
    return AiDailyOutput(
        sentiment_score=_sentiment_from_market(snapshot, "^GSPC"),
        sentiment_summary=f"{_DRY}S&P500 のセンチメント要約（擬似）",
        acwi_sentiment_score=max(-5, min(5, int(round(jpy)))) if jpy is not None else 0,
        acwi_sentiment_summary=f"{_DRY}オルカンのセンチメント要約（擬似）",
        fx_outlook=f"{_DRY}為替見通し（擬似）。USD/JPY の方向性に関するサンプル文。",
        news_analysis=news,
        watch_list=[
            AiWatchItem(
                label=f"{_DRY}擬似の監視対象 {i}", when="今週後半", why="ドライラン用のサンプル"
            )
            for i in range(1, 3)
        ],
        report_markdown=(
            f"## {_DRY}サマリ\n\n{body}\n\n"
            f"このレポートは `--dry-run` で生成された擬似データです。"
            f"Bedrock は呼び出されておらず、分析内容に意味はありません。\n\n"
            f"## 市況\n\n基準日 {snapshot.reference_date}。\n\n"
            f"## オルカン（円建て全世界株式）保有者向けの観点\n\n"
            f"擬似データのため内容はありません。\n\n"
            f"## 重要ニュース\n\n" + "\n".join(f"- {n.headline}" for n in news)
        ),
        x_post_text=f"{_DRY}ドライランで生成した擬似の投稿本文です。実際の分析は含まれません。",
    )


def period_output(period_type: PeriodType, label: str, stats: PeriodStats) -> AiPeriodOutput:
    return AiPeriodOutput(
        sentiment_trend_summary=(
            f"{_DRY}{label} のセンチメント推移（平均 {stats.sentiment_avg}）の擬似解釈。"
        ),
        key_themes=[
            AiPeriodTheme(
                title=f"{_DRY}擬似テーマ {i}",
                narrative="ドライラン用のサンプル経緯。",
                persistence=("continuing", "resolved", "new")[i % 3],
                related_dates=[d for d, _ in stats.sentiment_series[:2]],
            )
            for i in range(1, 4)
        ],
        market_recap=(
            f"{_DRY}市況の振り返り（擬似）。S&P500 期間騰落率 {stats.spx_period_change_pct}%。"
        ),
        global_recap=(
            f"{_DRY}オルカンの振り返り（擬似）。円建て {stats.jpy_global_period_change_pct}% "
            f"= 株価 {stats.contribution_equity_pct}% + 為替 {stats.contribution_fx_pct}%。"
        ),
        forecast_review=(
            f"{_DRY}予測の振り返り（擬似）。的中率 {stats.forecast_spx.hit_rate} / "
            f"ベンチマーク {stats.forecast_spx.always_up_hit_rate}。"
        ),
        outlook=f"{_DRY}次期間の見通し（擬似）。",
        report_markdown=(
            f"## {_DRY}{label} サマリ\n\nこのレポートは `--dry-run` で生成された"
            f"擬似データです。\n\n## 市況\n\nS&P500 {stats.spx_period_change_pct}%\n\n"
            f"## オルカン\n\n円建て {stats.jpy_global_period_change_pct}%\n"
        ),
        x_post_text=f"{_DRY}{label} の擬似投稿本文。",
    )
