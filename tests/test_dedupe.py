"""重複排除と媒体上限."""

from __future__ import annotations

from market_report.processing import dedupe, normalize
from market_report.processing.normalize import normalize_title


def test_normalize_strips_source_suffix():
    assert normalize_title("Stocks sink - Reuters") == "stocks sink"
    assert normalize_title("Fed holds rates | CNBC") == "fed holds rates"


def test_identical_titles_cluster_together(settings, make_article):
    arts = normalize.prepare(
        [
            make_article(1, "Stocks Sink in Broad AI Rout - Reuters"),
            make_article(2, "Stocks sink in broad AI rout | CNBC"),
            make_article(3, "Oil prices climb on supply concerns"),
        ]
    )
    _, info = dedupe.run(arts, settings)
    assert info.cluster_count == 2
    assert sum(1 for a in arts if a.is_representative) == 2


def test_cluster_metadata_is_written_back(settings, make_article):
    """``cluster_id`` / 類似度を保存して後から再実験できるようにする（A-85）."""
    arts = normalize.prepare([make_article(i, f"Headline number {i}") for i in range(1, 4)])
    dedupe.run(arts, settings)
    for art in arts:
        assert art.cluster_id is not None
        assert art.similarity_to_representative is not None


def test_representative_prefers_higher_feed_weight(settings, make_article):
    arts = normalize.prepare(
        [
            make_article(1, "Fed holds rates steady"),
            make_article(2, "Fed holds rates steady", feed="Federal Reserve", weight=1.2),
        ]
    )
    dedupe.run(arts, settings)
    rep = next(a for a in arts if a.is_representative)
    assert rep.feed_name == "Federal Reserve"


def test_per_feed_cap_limits_single_source(settings, make_article):
    """1 媒体が選抜を占有しないこと（FT の総合フィード対策）."""
    half = len(_DISTINCT_TITLES) // 2
    arts = normalize.prepare(
        [
            make_article(i, t, feed="Financial Times")
            for i, t in enumerate(_DISTINCT_TITLES[:half], 1)
        ]
        + [make_article(100 + i, t) for i, t in enumerate(_DISTINCT_TITLES[half:], 1)]
    )
    selected, _ = dedupe.run(arts, settings)
    from collections import Counter

    counts = Counter(a.feed_name for a in selected)
    assert len(counts) == 2
    # 供給が限られる場合、上限はソフトに超えるが分布は均衡を保つ
    assert max(counts.values()) - min(counts.values()) <= 1


def test_per_feed_cap_binds_when_supply_is_plentiful(settings, make_article):
    """媒体が多く供給が十分なときは上限が実際に効くこと."""
    arts = normalize.prepare(
        [make_article(i, t, feed="Financial Times") for i, t in enumerate(_DISTINCT_TITLES, 1)]
        + [
            make_article(100 + i, t, feed=f"Feed {i % 5}")
            for i, t in enumerate(_DISTINCT_TITLES, 1)
        ]
    )
    selected, _ = dedupe.run(arts, settings)
    ft = sum(1 for a in selected if a.feed_name == "Financial Times")
    assert ft <= settings.max_per_feed


# 連番だけ違う見出しは difflib 上ほぼ同一と判定されるため、語彙を変えて用意する
_DISTINCT_TITLES = [
    "Fed holds rates steady amid inflation concerns",
    "Oil prices climb on Middle East supply worries",
    "Nvidia earnings beat expectations on datacenter demand",
    "China unveils fresh stimulus for property sector",
    "ECB signals caution over wage growth in eurozone",
    "Treasury yields spike after weak bond auction",
    "Retail sales disappoint as consumers pull back",
    "Gold hits record high on safe haven demand",
    "Bank of Japan debates timing of next policy shift",
    "Semiconductor exports to Asia slump in October",
    "Airlines warn of weaker holiday travel bookings",
    "Housing starts tumble to three year low",
    "Copper rallies on mine disruption in Chile",
    "Jobless claims rise more than economists forecast",
    "Dollar weakens against major peers after data",
    "Utilities outperform as investors seek defensives",
    "Healthcare merger faces antitrust scrutiny",
    "Shipping rates surge on Red Sea diversions",
]


def test_feed_cap_still_fills_quota_when_sources_are_few(settings, make_article):
    """媒体が少ない日に件数が落ちないこと（上限超過分で枠を埋める）."""
    arts = normalize.prepare(
        [make_article(i, title) for i, title in enumerate(_DISTINCT_TITLES, 1)]
    )
    selected, info = dedupe.run(arts, settings)
    assert info.cluster_count == len(_DISTINCT_TITLES)
    assert len(selected) == settings.max_articles_to_analyze


def test_threshold_override_changes_clustering(settings, make_article):
    arts = normalize.prepare(
        [
            make_article(1, "Stocks tumble in wide AI selloff triggered by DeepSeek"),
            make_article(2, "Stocks tumble in wide AI selloff caused by DeepSeek"),
        ]
    )
    _, strict = dedupe.run(list(arts), settings, threshold=0.99)
    arts = normalize.prepare(
        [
            make_article(1, "Stocks tumble in wide AI selloff triggered by DeepSeek"),
            make_article(2, "Stocks tumble in wide AI selloff caused by DeepSeek"),
        ]
    )
    _, loose = dedupe.run(arts, settings, threshold=0.5)
    assert strict.cluster_count > loose.cluster_count
