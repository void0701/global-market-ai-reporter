"""期間集計（機械計算）."""

from __future__ import annotations

from datetime import date

from market_report.analysis import aggregate


def _week(make_daily):
    return [
        make_daily(date(2026, 11, 17), -1.0, -1.2, "down"),
        make_daily(date(2026, 11, 18), -0.5, -0.6, "down"),
        make_daily(date(2026, 11, 19), 0.8, 0.9, "up"),
        make_daily(date(2026, 11, 20), -2.0, -2.1, "down"),
    ]


def test_period_bounds():
    assert aggregate.period_bounds("weekly", "2026-W47") == (
        date(2026, 11, 16),
        date(2026, 11, 22),
    )
    assert aggregate.period_bounds("monthly", "2026-11") == (
        date(2026, 11, 1),
        date(2026, 11, 30),
    )
    assert aggregate.period_bounds("yearly", "2026") == (date(2026, 1, 1), date(2026, 12, 31))


def test_period_bounds_december_rollover():
    assert aggregate.period_bounds("monthly", "2026-12")[1] == date(2026, 12, 31)


def test_period_label_targets_previous_period():
    """月曜に前週、1日に前月、1/1 に前年を対象にする."""
    assert aggregate.period_label_for("weekly", date(2026, 11, 23)) == "2026-W47"
    assert aggregate.period_label_for("monthly", date(2026, 12, 1)) == "2026-11"
    assert aggregate.period_label_for("yearly", date(2027, 1, 1)) == "2026"


def test_build_stats_computes_sentiment_and_counts(settings, make_daily):
    stats = aggregate.build_stats(
        _week(make_daily), settings, date(2026, 11, 16), date(2026, 11, 22)
    )
    assert stats.days == 4
    assert stats.sentiment_min == -2
    assert stats.sentiment_max == 2
    assert len(stats.sentiment_series) == 4
    # 1日あたり news 2件（immediate + long_term volatility）
    assert sum(stats.direction_counts.values()) == 8
    assert stats.horizon_counts["long_term"] == 4


def test_build_stats_flags_missing_run_days(settings, make_daily):
    """欠損日を検出する（米国の祝日もここに現れる）."""
    stats = aggregate.build_stats(
        _week(make_daily), settings, date(2026, 11, 16), date(2026, 11, 22)
    )
    # 火〜土が実行予定日。11/21(土) が欠けている
    assert "2026-11-21" in stats.missing_dates


def test_build_stats_tracks_fill_rates(settings, make_daily):
    stats = aggregate.build_stats(
        _week(make_daily), settings, date(2026, 11, 16), date(2026, 11, 22)
    )
    assert stats.field_fill_rates["affected_sectors"] == 1.0
    assert stats.transmission_avg_chars is not None


def test_build_stats_is_safe_with_no_records(settings):
    stats = aggregate.build_stats([], settings, date(2026, 11, 16), date(2026, 11, 22))
    assert stats.days == 0
    assert stats.sentiment_avg is None


def test_source_summary_omits_report_markdown(make_daily):
    """階層的ロールアップの要点: 本文は上位階層に渡さない."""
    record = make_daily(date(2026, 11, 20), -2.0, -2.1, "down")
    summary = aggregate.source_summary(record)
    assert record.analysis.report_markdown not in summary
    assert "2026-11-20" in summary
