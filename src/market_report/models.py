"""データモデル.

2 系統に分かれる。

* ``Ai*`` — Bedrock の構造化出力でモデルが生成する部分。ここを変えると
  過去データとの互換が切れるため :data:`DAILY_SCHEMA_VERSION` を上げる。
* ``*Record`` — S3 / ローカルに保存する JSON 全体（AI 出力 + 市況 + メタ）。

構造化出力の制約（docs/02 §3）に合わせ、列挙は ``Enum`` ではなく ``Literal``
を使い、全フィールドを必須にする。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------
# 列挙（Literal）
# --------------------------------------------------------------------------

Horizon = Literal["immediate", "short_term", "long_term"]
Direction = Literal["up", "down", "volatility"]
Confidence = Literal["high", "medium", "low"]
NewsCategory = Literal[
    "monetary_policy",
    "economic_data",
    "corporate_earnings",
    "geopolitics",
    "commodity",
    "regulation",
    "other",
]
Sector = Literal[
    "information_technology",
    "financials",
    "health_care",
    "consumer_discretionary",
    "communication_services",
    "industrials",
    "consumer_staples",
    "energy",
    "utilities",
    "real_estate",
    "materials",
]
SurpriseDirection = Literal["above", "below", "inline", "no_consensus"]
Novelty = Literal["new", "continuing", "priced_in"]
Magnitude = Literal["large", "medium", "small"]
RegionFocus = Literal["us", "europe", "japan", "em", "china", "global"]
Scope = Literal["us_specific", "global"]
FxImpact = Literal["yen_weaker", "yen_stronger", "neutral"]
Persistence = Literal["continuing", "resolved", "new"]
PeriodType = Literal["weekly", "monthly", "yearly"]

HORIZON_JA: dict[str, str] = {
    "immediate": "早急（当日〜翌日）",
    "short_term": "数日後（2〜5日）",
    "long_term": "中長期（数週間〜）",
}
DIRECTION_JA: dict[str, str] = {"up": "上昇要因", "down": "下落要因", "volatility": "ボラ拡大"}
CONFIDENCE_JA: dict[str, str] = {"high": "高", "medium": "中", "low": "低"}
NEWS_CATEGORY_JA: dict[str, str] = {
    "monetary_policy": "金融政策",
    "economic_data": "経済指標",
    "corporate_earnings": "企業業績",
    "geopolitics": "地政学",
    "commodity": "商品",
    "regulation": "規制",
    "other": "その他",
}
SECTOR_JA: dict[str, str] = {
    "information_technology": "情報技術",
    "financials": "金融",
    "health_care": "ヘルスケア",
    "consumer_discretionary": "一般消費財",
    "communication_services": "通信サービス",
    "industrials": "資本財",
    "consumer_staples": "生活必需品",
    "energy": "エネルギー",
    "utilities": "公共事業",
    "real_estate": "不動産",
    "materials": "素材",
}
SURPRISE_JA: dict[str, str] = {
    "above": "予想を上振れ",
    "below": "予想を下振れ",
    "inline": "予想通り",
    "no_consensus": "予想なし",
}
NOVELTY_JA: dict[str, str] = {
    "new": "新規材料",
    "continuing": "既報の継続",
    "priced_in": "織り込み済み",
}
MAGNITUDE_JA: dict[str, str] = {"large": "大", "medium": "中", "small": "小"}
REGION_JA: dict[str, str] = {
    "us": "米国",
    "europe": "欧州",
    "japan": "日本",
    "em": "新興国",
    "china": "中国",
    "global": "グローバル",
}
SCOPE_JA: dict[str, str] = {"us_specific": "米国固有", "global": "全世界共通"}
FX_IMPACT_JA: dict[str, str] = {
    "yen_weaker": "円安要因",
    "yen_stronger": "円高要因",
    "neutral": "中立",
}
PERSISTENCE_JA: dict[str, str] = {"continuing": "継続中", "resolved": "解消", "new": "新規"}

# --------------------------------------------------------------------------
# AI 出力（日次）
# --------------------------------------------------------------------------


class StrictModel(BaseModel):
    """構造化出力用。未知フィールドを拒否する."""

    model_config = ConfigDict(extra="forbid")


class KeyNumber(StrictModel):
    """ニュース中の数値。「事実と市場予想とのズレ」を機械検証可能にする."""

    label: str = Field(description="指標名（例: 10月CPI 前年比）")
    actual: str = Field(description="実績値。不明なら空文字")
    consensus: str = Field(description="市場予想。不明なら空文字")
    previous: str = Field(description="前回値。不明なら空文字")
    unit: str = Field(description="単位（%, 万人, ドル等）。不明なら空文字")


class AiNewsAnalysis(StrictModel):
    headline: str = Field(description="ニュースの見出し（日本語）")
    summary: str = Field(description="事実と市場予想とのズレを中心とした概要（日本語）")
    transmission: str = Field(description="S&P500 への波及メカニズム（日本語）")
    horizon: Horizon
    direction: Direction
    confidence: Confidence
    news_category: NewsCategory
    affected_sectors: list[Sector] = Field(description="影響を受けるGICSセクター。該当なしは空配列")
    mentioned_tickers: list[str] = Field(
        description="言及された個別銘柄のティッカー。無ければ空配列"
    )
    surprise_direction: SurpriseDirection
    novelty: Novelty
    expected_magnitude: Magnitude
    key_numbers: list[KeyNumber] = Field(description="記事中の数値。無ければ空配列")
    region_focus: RegionFocus
    scope: Scope
    fx_impact: FxImpact = Field(description="円相場への影響。円安はオルカン円建てにプラス")
    source_urls: list[str] = Field(description="出典URL")


class AiWatchItem(StrictModel):
    label: str = Field(description="監視対象の指標・イベント名")
    when: str = Field(description="いつ（日付や『今週後半』等）")
    why: str = Field(description="なぜ重要か")


class AiDailyOutput(StrictModel):
    """日次解析でモデルが生成する全体."""

    sentiment_score: int = Field(
        ge=-5, le=5, description="S&P500 向け。-5=極度の警戒 〜 +5=強い楽観"
    )
    sentiment_summary: str = Field(description="S&P500 のセンチメント一言総括（日本語）")
    acwi_sentiment_score: int = Field(
        ge=-5, le=5, description="円建て全世界株式(オルカン)向け。為替影響を織り込む"
    )
    acwi_sentiment_summary: str = Field(description="オルカンのセンチメント一言総括（日本語）")
    fx_outlook: str = Field(description="為替（特にUSD/JPY）の見通し（日本語）")
    news_analysis: list[AiNewsAnalysis] = Field(description="重要ニュースの分析")
    watch_list: list[AiWatchItem] = Field(description="今日〜数日で監視すべき指標・イベント")
    report_markdown: str = Field(description="Web閲覧用の詳細レポート本文（Markdown・日本語）")
    x_post_text: str = Field(
        description="X投稿用の本文のみ。日付と騰落率のプレフィックスは付けない（コード側で付与）"
    )


# --------------------------------------------------------------------------
# AI 出力（期間レポート）
# --------------------------------------------------------------------------


class AiPeriodTheme(StrictModel):
    title: str = Field(description="テーマ名（日本語）")
    narrative: str = Field(description="期間を通じた経緯（日本語）")
    persistence: Persistence
    related_dates: list[str] = Field(description="関連する日次レポートの日付 YYYY-MM-DD")


class AiPeriodOutput(StrictModel):
    sentiment_trend_summary: str = Field(description="センチメント推移の解釈（日本語）")
    key_themes: list[AiPeriodTheme] = Field(description="期間の主要テーマ")
    market_recap: str = Field(description="市況の振り返り（日本語）")
    global_recap: str = Field(
        description="円建て全世界株式(オルカン)の振り返り。株価要因と為替要因の寄与に触れる"
    )
    forecast_review: str = Field(
        description="予測の振り返り。提示された的中率とベンチマークを踏まえる"
    )
    outlook: str = Field(description="次期間の見通し（日本語）")
    report_markdown: str = Field(description="Web閲覧用の詳細レポート本文（Markdown・日本語）")
    x_post_text: str = Field(description="X投稿用の本文のみ。プレフィックスは付けない")


# --------------------------------------------------------------------------
# 市況
# --------------------------------------------------------------------------


class Quote(BaseModel):
    """1 系列の 1 営業日分。OHLCV 全項目を保存する（docs/11 A-89）."""

    symbol: str
    label: str = ""
    group: str = ""
    date: str
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None
    prev_close: float | None = None
    change_pct: float | None = None


class GlobalView(BaseModel):
    """オルカン（円建て全世界株式）の観点（docs/12）."""

    acwi_close: float | None = None
    acwi_change_pct: float | None = None
    usdjpy: float | None = None
    usdjpy_change_pct: float | None = None
    # ACWI × USDJPY の理論値。最新の米国セッションを反映する（レポートの正）
    jpy_theoretical_change_pct: float | None = None
    # 株価要因 / 為替要因への寄与度分解
    contribution_equity_pct: float | None = None
    contribution_fx_pct: float | None = None
    # 2559.T の実測。07:00 JST 時点では 1 セッション遅れる（docs/12 §1-4）
    etf_jpy_close: float | None = None
    etf_jpy_change_pct: float | None = None
    etf_jpy_date: str | None = None
    etf_lag_note: str = "2559.T は東証引け（15:30 JST）のため 07:00 JST 時点では 1 営業日前の値"


class MarketSnapshot(BaseModel):
    """その日の市況。``quotes`` は保存用の全系列を持つ.

    ``reference_date`` は米国株の最新営業日。為替・商品はほぼ24時間取引される
    ため、この日付に全系列を揃えないと寄与度分解が異なる期間の比較になる。
    """

    asof_date: str
    reference_date: str = ""
    quotes: dict[str, Quote] = Field(default_factory=dict)
    global_view: GlobalView = Field(default_factory=GlobalView)
    fetch_errors: list[str] = Field(default_factory=list)

    def get(self, symbol: str) -> Quote | None:
        return self.quotes.get(symbol)

    def change_pct(self, symbol: str) -> float | None:
        q = self.quotes.get(symbol)
        return q.change_pct if q else None


# --------------------------------------------------------------------------
# 記事（生データ）
# --------------------------------------------------------------------------


class Article(BaseModel):
    """RSS 1 記事。重複排除で捨てたものも全件保存する（docs/11 A-84）."""

    id: str
    feed_name: str
    feed_weight: float = 1.0
    feed_category: str = ""
    paywall: bool = False
    title: str
    summary_raw: str = ""
    summary_len: int = 0
    link: str = ""
    guid: str = ""
    published: str | None = None
    categories: list[str] = Field(default_factory=list)
    tickers: list[str] = Field(default_factory=list)
    title_normalized: str = ""
    cluster_id: int | None = None
    is_representative: bool = False
    similarity_to_representative: float | None = None
    selected_for_analysis: bool = False

    @property
    def summary_for_ai(self) -> str:
        return self.summary_raw


class FeedFetchResult(BaseModel):
    name: str
    url: str
    http_status: int | None = None
    item_count: int = 0
    in_window_count: int = 0
    latest_item_age_hours: float | None = None
    stale: bool = False
    fetch_ms: int = 0
    error: str | None = None


class DedupeInfo(BaseModel):
    algorithm: str = "jaccard+difflib"
    threshold: float = 0.75
    input_count: int = 0
    cluster_count: int = 0
    selected_count: int = 0


class RawNewsRecord(BaseModel):
    """``raw/news/YYYY-MM-DD.json``."""

    schema_version: int
    date: str
    collected_at: str
    window_from: str
    window_to: str
    feeds: list[FeedFetchResult] = Field(default_factory=list)
    articles: list[Article] = Field(default_factory=list)
    dedupe: DedupeInfo = Field(default_factory=DedupeInfo)


class LlmCallRecord(BaseModel):
    """``raw/llm/YYYY-MM-DD.json``. プロンプト全文を残す（docs/11 §3)."""

    schema_version: int
    date: str
    kind: str = "daily"
    model: str = ""
    region: str = ""
    system_prompt: str = ""
    user_prompt: str = ""
    output_schema_hash: str = ""
    max_tokens: int = 0
    stop_reason: str | None = None
    thinking_summary: str = ""
    content_raw: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    estimated_cost_usd: float = 0.0
    dry_run: bool = False


# --------------------------------------------------------------------------
# 解析結果（保存）
# --------------------------------------------------------------------------


class DailyRecord(BaseModel):
    """``data/daily/YYYY-MM-DD.json``. 期間レポートの入力になる."""

    schema_version: int
    date: str
    generated_at: str
    model: str = ""
    dry_run: bool = False
    market: MarketSnapshot
    analysis: AiDailyOutput
    x_post_full: str = ""
    article_counts: dict[str, int] = Field(default_factory=dict)


class ForecastEvaluation(BaseModel):
    """予測の的中率。ベンチマーク併記が必須（docs/09 §3-3）."""

    sample_size: int = 0
    hits: int = 0
    hit_rate: float | None = None
    always_up_hit_rate: float | None = None
    always_down_hit_rate: float | None = None
    edge_vs_always_up_pp: float | None = None
    note: str = ""


class PeriodStats(BaseModel):
    """機械集計。AI には計算させない（docs/09 §1-3）."""

    days: int = 0
    sentiment_avg: float | None = None
    sentiment_min: int | None = None
    sentiment_max: int | None = None
    sentiment_series: list[tuple[str, int]] = Field(default_factory=list)
    acwi_sentiment_avg: float | None = None
    acwi_sentiment_series: list[tuple[str, int]] = Field(default_factory=list)

    spx_period_change_pct: float | None = None
    spx_best_day: tuple[str, float] | None = None
    spx_worst_day: tuple[str, float] | None = None
    acwi_period_change_pct: float | None = None
    usdjpy_period_change_pct: float | None = None
    jpy_global_period_change_pct: float | None = None
    contribution_equity_pct: float | None = None
    contribution_fx_pct: float | None = None
    spx_vs_acwi_gap_pp: float | None = None

    vix_avg: float | None = None
    vix_max: float | None = None
    tnx_change_bp: float | None = None

    direction_counts: dict[str, int] = Field(default_factory=dict)
    horizon_counts: dict[str, int] = Field(default_factory=dict)
    confidence_counts: dict[str, int] = Field(default_factory=dict)
    news_category_counts: dict[str, int] = Field(default_factory=dict)
    sector_counts: dict[str, int] = Field(default_factory=dict)
    surprise_counts: dict[str, int] = Field(default_factory=dict)
    novelty_counts: dict[str, int] = Field(default_factory=dict)
    magnitude_counts: dict[str, int] = Field(default_factory=dict)
    region_counts: dict[str, int] = Field(default_factory=dict)
    scope_counts: dict[str, int] = Field(default_factory=dict)
    fx_impact_counts: dict[str, int] = Field(default_factory=dict)
    ticker_counts: dict[str, int] = Field(default_factory=dict)

    region_etf_changes: dict[str, float] = Field(default_factory=dict)
    sector_etf_changes: dict[str, float] = Field(default_factory=dict)

    forecast_spx: ForecastEvaluation = Field(default_factory=ForecastEvaluation)
    forecast_acwi: ForecastEvaluation = Field(default_factory=ForecastEvaluation)

    # フィールド充填率の監視（docs/11 §6-3）
    field_fill_rates: dict[str, float] = Field(default_factory=dict)
    transmission_avg_chars: float | None = None
    missing_dates: list[str] = Field(default_factory=list)


class PeriodRecord(BaseModel):
    """``data/{weekly,monthly,yearly}/<label>.json``."""

    schema_version: int
    period_type: PeriodType
    period_label: str
    period_start: str
    period_end: str
    generated_at: str
    model: str = ""
    dry_run: bool = False
    source_labels: list[str] = Field(default_factory=list)
    stats: PeriodStats
    analysis: AiPeriodOutput
    x_post_full: str = ""
