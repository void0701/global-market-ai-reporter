"""予測の的中率とベンチマーク（docs/09 §3-3）."""

from __future__ import annotations

from datetime import date

from market_report.analysis import scoring


def _days(make_daily, specs):
    return [make_daily(date(2026, 11, d), spx, jpy, direction) for d, spx, jpy, direction in specs]


def test_hit_rate_counts_direction_matches(make_daily):
    # 17日に「下落」判定 -> 18日実績が下落なら的中
    records = _days(
        make_daily,
        [(17, -1.0, -1.0, "down"), (18, -2.0, -2.0, "down"), (19, 1.0, 1.0, "up")],
    )
    result = scoring.evaluate(records, "spx")
    assert result.sample_size == 2
    assert result.hits == 1  # 17->18 的中 / 18->19 は下落判定に対し上昇


def test_benchmarks_are_always_reported(make_daily):
    """ベンチマークなしの的中率は解釈不能なため必ず併記する."""
    records = _days(make_daily, [(17, -1.0, -1.0, "down"), (18, -2.0, -2.0, "down")])
    result = scoring.evaluate(records, "spx")
    assert result.always_up_hit_rate is not None
    assert result.always_down_hit_rate is not None
    assert result.edge_vs_always_up_pp is not None


def test_edge_is_zero_when_model_matches_always_down(make_daily):
    """全日下落なら「常に下落」も100%。edge で見れば優位がないことが分かる."""
    records = _days(make_daily, [(d, -1.0, -1.0, "down") for d in (17, 18, 19, 20)])
    result = scoring.evaluate(records, "spx")
    assert result.hit_rate == 1.0
    assert result.always_down_hit_rate == 1.0
    assert result.always_up_hit_rate == 0.0


def test_small_sample_is_flagged(make_daily):
    records = _days(make_daily, [(17, -1.0, -1.0, "down"), (18, -1.0, -1.0, "down")])
    result = scoring.evaluate(records, "spx")
    assert "統計的な結論を出せる水準" in result.note


def test_no_sample_when_single_day(make_daily):
    result = scoring.evaluate(_days(make_daily, [(17, -1.0, -1.0, "down")]), "spx")
    assert result.sample_size == 0
    assert result.hit_rate is None


def test_volatility_judgments_are_excluded(make_daily):
    """方向を持たない判定は的中率の対象外."""
    records = _days(make_daily, [(17, -1.0, -1.0, "down"), (18, -1.0, -1.0, "down")])
    signal = scoring.net_signal(records[0])
    assert signal < 0  # down(high)=-1.5 のみ。volatility は加算されない
    assert abs(signal) == 1.5
