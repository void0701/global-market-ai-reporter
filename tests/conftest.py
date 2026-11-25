from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fakes import FakeMarket  # noqa: E402

from market_report import (
    DAILY_SCHEMA_VERSION,  # noqa: E402
    pipeline,  # noqa: E402
)
from market_report.collectors.market import Quote  # noqa: E402
from market_report.config import load_settings  # noqa: E402
from market_report.models import (  # noqa: E402
    AiDailyOutput,
    AiNewsAnalysis,
    AiWatchItem,
    Article,
    Confidence,
    DailyRecord,
    Direction,
    FeedFetchResult,
    GlobalView,
    Horizon,
    KeyNumber,
    MarketSnapshot,
)


@pytest.fixture
def fake_rss(monkeypatch):
    """RSS 取得を固定の 3 記事に差し替える（ネットワークを使わない）."""

    def _collect(settings, window_from, window_to):
        results = [FeedFetchResult(name="CNBC Top News", url="u", http_status=200, item_count=3)]
        titles = [
            "Fed holds rates steady amid inflation concerns",
            "Oil prices climb on Middle East supply worries",
            "Nvidia earnings beat expectations on datacenter demand",
        ]
        articles = [
            Article(
                id=f"a{i}",
                feed_name="CNBC Top News",
                title=t,
                summary_raw="summary " * 20,
                summary_len=160,
                link=f"https://example.com/{i}",
                published="2026-11-20T12:00:00+00:00",
            )
            for i, t in enumerate(titles, 1)
        ]
        return results, articles

    monkeypatch.setattr(pipeline.rss, "collect", _collect)


@pytest.fixture
def fake_market(monkeypatch):
    """既定の市況プロバイダ（HTTP）を差し替える。``provider`` 引数を渡せない経路用."""
    monkeypatch.setattr(pipeline.market_mod, "HttpChartProvider", lambda *a, **kw: FakeMarket())


@pytest.fixture
def settings(tmp_path):
    return load_settings(output_dir=tmp_path / "out")


@pytest.fixture
def make_article():
    def _make(idx: int, title: str, *, feed="CNBC Top News", weight=1.0, summary_len=120):
        return Article(
            id=f"a{idx}",
            feed_name=feed,
            feed_weight=weight,
            title=title,
            summary_raw="x" * summary_len,
            summary_len=summary_len,
            link=f"https://example.com/{idx}",
            published=f"2026-11-20T{idx % 24:02d}:00:00+00:00",
        )

    return _make


def _news(direction: Direction, horizon: Horizon = "immediate", confidence: Confidence = "high"):
    return AiNewsAnalysis(
        headline="h",
        summary="s",
        transmission="t" * 60,
        horizon=horizon,
        direction=direction,
        confidence=confidence,
        news_category="monetary_policy",
        affected_sectors=["information_technology"],
        mentioned_tickers=["NVDA"],
        surprise_direction="above",
        novelty="new",
        expected_magnitude="large",
        key_numbers=[
            KeyNumber(label="CPI", actual="3.1", consensus="3.0", previous="2.9", unit="%")
        ],
        region_focus="us",
        scope="us_specific",
        fx_impact="neutral",
        source_urls=["https://example.com/a"],
    )


@pytest.fixture
def make_daily():
    """``(日付, S&P500騰落率, 円建て騰落率, immediate判定の方向)`` から日次レコードを作る."""

    def _make(d: date, spx_change: float, jpy_change: float, direction: Direction = "down"):
        close = 7000 * (1 + spx_change / 100)
        prev = 7000.0
        snapshot = MarketSnapshot(
            asof_date=d.isoformat(),
            reference_date=(d - timedelta(days=1)).isoformat(),
            quotes={
                "^GSPC": Quote(
                    symbol="^GSPC",
                    date=d.isoformat(),
                    close=close,
                    prev_close=prev,
                    change_pct=spx_change,
                ),
                "^VIX": Quote(symbol="^VIX", date=d.isoformat(), close=20.0, prev_close=19.0),
                "^TNX": Quote(
                    symbol="^TNX", date=d.isoformat(), close=4.5, prev_close=4.4, change_pct=2.3
                ),
                "ACWI": Quote(
                    symbol="ACWI",
                    date=d.isoformat(),
                    close=142.0,
                    prev_close=143.0,
                    change_pct=-0.7,
                ),
                "USDJPY=X": Quote(
                    symbol="USDJPY=X",
                    date=d.isoformat(),
                    close=159.0,
                    prev_close=158.0,
                    change_pct=0.63,
                ),
            },
            global_view=GlobalView(jpy_theoretical_change_pct=jpy_change),
        )
        return DailyRecord(
            schema_version=DAILY_SCHEMA_VERSION,
            date=d.isoformat(),
            generated_at="2026-11-23T07:00:00+09:00",
            market=snapshot,
            analysis=AiDailyOutput(
                sentiment_score=-2 if direction == "down" else 2,
                sentiment_summary="summary",
                acwi_sentiment_score=-1 if direction == "down" else 1,
                acwi_sentiment_summary="summary",
                fx_outlook="fx",
                news_analysis=[_news(direction), _news("volatility", "long_term", "low")],
                watch_list=[AiWatchItem(label="CPI", when="今週", why="重要")],
                report_markdown="## test\n\nbody",
                x_post_text="post",
            ),
        )

    return _make
