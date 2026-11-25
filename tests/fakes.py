"""ネットワークに出ないスタブ（AWS 以外）."""

from __future__ import annotations

from market_report.collectors.market import Quote


class FakeMarket:
    """``MarketDataProvider`` のスタブ。2 営業日ぶんだけ返す."""

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
