"""市況データの基準日と円建て分解."""

from __future__ import annotations

from datetime import date

from market_report.collectors.market import (
    build_global_view,
    fetch_snapshot,
    market_asof_for,
)
from market_report.models import GlobalView, MarketSnapshot, Quote


class FakeProvider:
    """系列ごとに日付と終値を固定で返す."""

    def __init__(self, data: dict[str, list[tuple[str, float]]]):
        self.data = data

    def history(self, symbol: str, days: int) -> list[Quote]:
        rows = []
        prev = None
        for d, close in self.data.get(symbol, []):
            rows.append(
                Quote(
                    symbol=symbol,
                    date=d,
                    close=close,
                    prev_close=prev,
                    change_pct=round((close / prev - 1) * 100, 4) if prev else None,
                )
            )
            prev = close
        return rows


def test_market_asof_is_previous_day():
    """07:00 JST = 前日 17:00 ET。当日のセッションはまだ無い."""
    assert market_asof_for(date(2026, 11, 21)) == date(2026, 11, 20)


def test_fetch_snapshot_aligns_all_series_to_equity_session(settings):
    """為替はほぼ24時間取引されるため、株式セッションに揃えないと期間がズレる."""
    provider = FakeProvider(
        {
            "^GSPC": [("2026-11-19", 7000.0), ("2026-11-20", 6900.0)],
            # 為替は土日も値がつく
            "USDJPY=X": [
                ("2026-11-19", 158.0),
                ("2026-11-20", 159.0),
                ("2026-11-21", 160.0),
                ("2026-11-22", 161.0),
            ],
            "ACWI": [("2026-11-19", 148.0), ("2026-11-20", 142.0)],
        }
    )
    snapshot = fetch_snapshot(settings, date(2026, 11, 22), provider)
    assert snapshot.reference_date == "2026-11-20"
    dates = {q.date for q in snapshot.quotes.values()}
    assert dates == {"2026-11-20"}, "全系列が同じセッション日に揃っていること"


def test_global_view_decomposes_return():
    """円建て ≈ 株価要因 + 為替要因."""
    snapshot = MarketSnapshot(
        asof_date="2026-11-21",
        reference_date="2026-11-20",
        quotes={
            "ACWI": Quote(
                symbol="ACWI", date="2026-11-20", close=142.0, prev_close=148.0, change_pct=-4.054
            ),
            "USDJPY=X": Quote(
                symbol="USDJPY=X",
                date="2026-11-20",
                close=159.0,
                prev_close=158.0,
                change_pct=0.633,
            ),
        },
    )
    view = build_global_view(snapshot)
    assert view.contribution_equity_pct == -4.054
    assert view.contribution_fx_pct == 0.633
    # 厳密には乗法だが、小さい変化率では加法近似とほぼ一致する
    approx = view.contribution_equity_pct + view.contribution_fx_pct
    assert abs(view.jpy_theoretical_change_pct - approx) < 0.1


def test_global_view_is_empty_without_data():
    view = build_global_view(MarketSnapshot(asof_date="2026-11-21"))
    assert view == GlobalView()
