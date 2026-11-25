"""市況データ取得.

``MarketDataProvider`` を抽象にしておく理由（docs/01 §3-2）— ``yfinance`` は
pandas / numpy を推移的に引き込み Lambda のパッケージが膨らむ。本システムが
必要なのは終値と前日比だけなので、標準ライブラリだけで動く
:class:`HttpChartProvider` を既定とし、Phase 2 でもそのまま使える形にする。

全 40 系列を保存し、AI に渡すのは ``ai_symbols`` の 15 系列のみ（docs/11 §2）。
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

from ..config import Settings
from ..models import GlobalView, MarketSnapshot, Quote
from ..utils import logging as log

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) market-report/0.1"
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

ACWI = "ACWI"
USDJPY = "USDJPY=X"
ETF_JPY = "2559.T"
SPX = "^GSPC"


class MarketDataProvider(Protocol):
    """市況データの取得元。Phase 2 で差し替えられるようにする."""

    def history(self, symbol: str, days: int) -> list[Quote]:
        """直近 ``days`` 日分の日足を古い順に返す."""
        ...


class HttpChartProvider:
    """Yahoo Finance chart API を標準ライブラリで直接叩く（既定）.

    pandas 非依存。OHLCV 全項目を取得する（docs/11 A-89）。
    """

    def __init__(self, timeout: int = 20) -> None:
        self.timeout = timeout

    def _get(self, symbol: str, rng: str) -> dict:
        url = CHART_URL.format(symbol=urllib.parse.quote(symbol)) + f"?range={rng}&interval=1d"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
            return json.loads(resp.read())

    def history(self, symbol: str, days: int) -> list[Quote]:
        rng = "1mo" if days <= 25 else ("3mo" if days <= 70 else "1y")
        payload = self._get(symbol, rng)
        results = (payload.get("chart") or {}).get("result") or []
        if not results:
            raise ValueError(f"{symbol}: chart result が空")
        res = results[0]
        quote = (res.get("indicators") or {}).get("quote") or [{}]
        q = quote[0] if quote else {}
        stamps = res.get("timestamp") or []

        rows: list[Quote] = []
        for i, ts in enumerate(stamps):
            close = _at(q.get("close"), i)
            if close is None:
                continue
            rows.append(
                Quote(
                    symbol=symbol,
                    date=datetime.fromtimestamp(ts, tz=UTC).astimezone().date().isoformat(),
                    open=_at(q.get("open"), i),
                    high=_at(q.get("high"), i),
                    low=_at(q.get("low"), i),
                    close=close,
                    volume=_at(q.get("volume"), i),
                )
            )
        # 前日比を埋める
        for i in range(1, len(rows)):
            prev = rows[i - 1].close
            rows[i].prev_close = prev
            if prev:
                rows[i].change_pct = round((rows[i].close / prev - 1) * 100, 4)  # type: ignore[operator]
        return rows[-days:] if days else rows


class YFinanceProvider:
    """``yfinance`` 実装（任意）.

    ``pip install 'market-report[market]'`` が必要。pandas を引き込むため
    Lambda では :class:`HttpChartProvider` を使う。
    """

    def history(self, symbol: str, days: int) -> list[Quote]:
        import yfinance  # 遅延 import（未インストールでも既定経路は動く）

        period = "1mo" if days <= 25 else ("3mo" if days <= 70 else "1y")
        frame = yfinance.Ticker(symbol).history(period=period, interval="1d")
        rows: list[Quote] = []
        prev: float | None = None
        for idx, row in frame.iterrows():
            close = float(row["Close"])
            rows.append(
                Quote(
                    symbol=symbol,
                    date=idx.date().isoformat(),
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=close,
                    volume=float(row.get("Volume") or 0) or None,
                    prev_close=prev,
                    change_pct=round((close / prev - 1) * 100, 4) if prev else None,
                )
            )
            prev = close
        return rows[-days:] if days else rows


def _at(seq: list | None, i: int) -> float | None:
    if not seq or i >= len(seq):
        return None
    v = seq[i]
    return float(v) if v is not None else None


def market_asof_for(report_date: date) -> date:
    """レポート日（JST）から、市況の参照基準日を求める.

    07:00 JST = 前日 17:00 ET。つまりレポート日 D の朝に実行する時点で完了して
    いる最新の米国セッションは **D-1（ET）** であり、D 当日のセッションはまだ
    始まってもいない。``report_date`` をそのまま ``asof`` に使うと、取引が終わって
    いない日の終値を拾おうとして 1 営業日ぶん先の値を参照してしまう。
    """
    return report_date - timedelta(days=1)


def _latest_on_or_before(rows: list[Quote], asof: date) -> Quote | None:
    for row in reversed(rows):
        if date.fromisoformat(row.date) <= asof:
            return row
    return None


def fetch_snapshot(
    settings: Settings,
    asof: date,
    provider: MarketDataProvider | None = None,
    *,
    history_days: int = 10,
) -> MarketSnapshot:
    """``asof`` 時点で参照できる最新の値を全 40 系列について集める.

    **全系列を米国株セッションの日付に揃える。** 為替と商品先物はほぼ 24 時間
    取引されるため、何もしないと「株式は金曜終値・為替は日曜値」という組み合わせ
    になり、寄与度分解が異なる期間の比較になってしまう。``^GSPC`` の最新営業日を
    基準日とし、各系列はその日以前の最新行を採る。
    """
    prov = provider or HttpChartProvider(timeout=settings.fetch_timeout_sec)
    snapshot = MarketSnapshot(asof_date=asof.isoformat())

    def one(symbol: str) -> tuple[str, list[Quote], str | None]:
        try:
            return symbol, prov.history(symbol, history_days), None
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            return symbol, [], f"{symbol}: {type(exc).__name__}: {exc}"

    histories: dict[str, list[Quote]] = {}
    with ThreadPoolExecutor(max_workers=settings.max_workers) as pool:
        for symbol, rows, err in pool.map(one, settings.all_symbols):
            if err:
                snapshot.fetch_errors.append(err)
                continue
            histories[symbol] = rows

    # 基準日 = 米国株の最新営業日（^GSPC が取れなければ ACWI、それも無ければ asof）
    ref = asof
    for anchor in (SPX, ACWI):
        row = _latest_on_or_before(histories.get(anchor, []), asof)
        if row:
            ref = date.fromisoformat(row.date)
            break
    snapshot.reference_date = ref.isoformat()

    for symbol in settings.all_symbols:
        history = histories.get(symbol)
        if history is None:
            continue
        row = _latest_on_or_before(history, ref)
        if row is None:
            snapshot.fetch_errors.append(f"{symbol}: {ref} 以前のデータなし")
            continue
        row.label = settings.label(symbol)
        row.group = settings.group(symbol)
        snapshot.quotes[symbol] = row

    snapshot.global_view = build_global_view(snapshot)
    log.info(
        logger,
        "市況取得",
        asof=asof.isoformat(),
        reference_date=snapshot.reference_date,
        ok=len(snapshot.quotes),
        total=len(settings.all_symbols),
        errors=len(snapshot.fetch_errors),
    )
    if snapshot.fetch_errors:
        log.warn(logger, "市況の一部が取得できず", errors=snapshot.fetch_errors[:5])
    return snapshot


def build_global_view(snapshot: MarketSnapshot) -> GlobalView:
    """オルカン（円建て全世界株式）の観点を組み立てる（docs/12 §1-3）.

    円建て変化率 ≈ ドル建て株価の変化率 + 為替（円安方向）の変化率。
    レポートの正は ``ACWI × USDJPY`` の理論値で、``2559.T`` は検証用に併記する。
    """
    acwi = snapshot.get(ACWI)
    fx = snapshot.get(USDJPY)
    etf = snapshot.get(ETF_JPY)

    view = GlobalView(
        acwi_close=acwi.close if acwi else None,
        acwi_change_pct=acwi.change_pct if acwi else None,
        usdjpy=fx.close if fx else None,
        usdjpy_change_pct=fx.change_pct if fx else None,
        etf_jpy_close=etf.close if etf else None,
        etf_jpy_change_pct=etf.change_pct if etf else None,
        etf_jpy_date=etf.date if etf else None,
    )

    if acwi and fx and acwi.close and acwi.prev_close and fx.close and fx.prev_close:
        now_jpy = acwi.close * fx.close
        prev_jpy = acwi.prev_close * fx.prev_close
        view.jpy_theoretical_change_pct = round((now_jpy / prev_jpy - 1) * 100, 4)
        view.contribution_equity_pct = acwi.change_pct
        view.contribution_fx_pct = fx.change_pct
    return view


def ai_subset(settings: Settings, snapshot: MarketSnapshot) -> dict[str, Quote]:
    """AI に渡す 15 系列だけを抜き出す（入力を増やしすぎない / docs/11 §2）."""
    return {s: snapshot.quotes[s] for s in settings.ai_symbols if s in snapshot.quotes}
