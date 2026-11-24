"""パイプライン全体（Bedrock はドライランで置き換える）."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from market_report import pipeline
from market_report.collectors.market import Quote
from market_report.models import Article, FeedFetchResult
from market_report.publishing import store
from market_report.publishing.local import LocalPublisher


class FakeMarket:
    def history(self, symbol: str, days: int) -> list[Quote]:
        base = {"^GSPC": 7000.0, "ACWI": 145.0, "USDJPY=X": 158.0, "^VIX": 20.0, "^TNX": 4.5}
        start = base.get(symbol, 100.0)
        return [
            Quote(symbol=symbol, date="2026-11-19", close=start, change_pct=None),
            Quote(
                symbol=symbol,
                date="2026-11-20",
                close=start * 0.97,
                prev_close=start,
                change_pct=-3.0,
            ),
        ]


@pytest.fixture
def fake_rss(monkeypatch):
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


def test_run_daily_dry_run_writes_all_three_layers(settings, fake_rss, tmp_path):
    pub = LocalPublisher(Path(settings.output_dir))
    record = pipeline.run_daily(
        settings,
        target_date=date(2026, 11, 21),
        dry_run=True,
        publisher=pub,
        provider=FakeMarket(),  # type: ignore[arg-type]
    )
    assert record.dry_run is True
    root = Path(settings.output_dir)
    for key in (
        "raw/news/2026-11-21.json",
        "raw/market/2026-11-21.json",
        "raw/llm/2026-11-21-daily.json",
        "data/daily/2026-11-21.json",
        "reports/2026-11-21.html",
        "index.html",
        "archive.html",
    ):
        assert (root / key).exists(), key


def test_raw_news_keeps_every_article_including_dropped(settings, fake_rss):
    """重複排除で捨てた記事も保存する（A-84）."""
    pub = LocalPublisher(Path(settings.output_dir))
    pipeline.run_daily(
        settings,
        target_date=date(2026, 11, 21),
        dry_run=True,
        publisher=pub,
        provider=FakeMarket(),  # type: ignore[arg-type]
    )
    raw = json.loads((Path(settings.output_dir) / "raw/news/2026-11-21.json").read_text())
    assert len(raw["articles"]) == 3
    assert all("cluster_id" in a for a in raw["articles"])
    assert raw["dedupe"]["algorithm"] == "jaccard+difflib"


def test_llm_record_stores_full_prompt(settings, fake_rss):
    """プロンプト全文を残す（A-86）."""
    pub = LocalPublisher(Path(settings.output_dir))
    pipeline.run_daily(
        settings,
        target_date=date(2026, 11, 21),
        dry_run=True,
        publisher=pub,
        provider=FakeMarket(),  # type: ignore[arg-type]
    )
    llm = json.loads((Path(settings.output_dir) / "raw/llm/2026-11-21-daily.json").read_text())
    assert len(llm["system_prompt"]) > 500
    assert "オルカン" in llm["system_prompt"]
    assert "MSCI ACWI の地域構成比" in llm["system_prompt"]
    assert llm["dry_run"] is True


def test_x_post_fits_limit(settings, fake_rss):
    pub = LocalPublisher(Path(settings.output_dir))
    record = pipeline.run_daily(
        settings,
        target_date=date(2026, 11, 21),
        dry_run=True,
        publisher=pub,
        provider=FakeMarket(),  # type: ignore[arg-type]
    )
    from market_report.utils.text import x_weighted_len

    assert x_weighted_len(record.x_post_full) <= settings.x_weight_limit
    assert record.x_post_full.startswith("【")


def test_period_report_raises_when_no_data(settings):
    pub = LocalPublisher(Path(settings.output_dir))
    with pytest.raises(pipeline.InsufficientDataError):
        pipeline.run_period(settings, "weekly", label="2026-W47", dry_run=True, publisher=pub)


def test_weekly_report_rolls_up_dailies(settings, fake_rss):
    pub = LocalPublisher(Path(settings.output_dir))
    for day in (17, 18, 19, 20):
        pipeline.run_daily(
            settings,
            target_date=date(2026, 11, day),
            dry_run=True,
            publisher=pub,
            provider=FakeMarket(),  # type: ignore[arg-type]
        )
    record = pipeline.run_period(settings, "weekly", label="2026-W47", dry_run=True, publisher=pub)
    assert record.stats.days == 4
    assert (Path(settings.output_dir) / "weekly/2026-W47.html").exists()
    assert (Path(settings.output_dir) / "weekly.html").exists()
    assert store.load_period(pub, "weekly", "2026-W47") is not None
