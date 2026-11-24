"""プロンプト.

本システムで最も結果を左右する部分。プロンプト全文は実行ごとに
``raw/llm/`` に保存され、改善の効果を同一条件で比較できる（docs/11 §3）。

設計上明示している前提
--------------------
* **MSCI ACWI の地域構成比**（米国約 60%）— 書かないと米国固有ニュースを
  オルカンにも同程度の影響として評価してしまう（docs/12 A-103）
* **円安はオルカン円建てにプラス** — 直感に反する論点でモデルが混乱しうる
  （docs/12 A-104）
* **記事原文を転載しない** — 要約・分析のみ掲載（docs/05 A-51）
* **X 投稿文にプレフィックスを付けない** — 日付と騰落率はコード側で付与し、
  文字数をコードが完全管理する（docs/05 A-21 / A-22）
"""

from __future__ import annotations

from datetime import date

from ..config import Settings
from ..models import (
    DIRECTION_JA,
    MarketSnapshot,
    PeriodStats,
    PeriodType,
)
from ..processing.normalize import summary_for_ai

DAILY_SYSTEM = """\
あなたは米国株式市場を専門とする機関投資家向けのアナリストです。毎朝、過去24時間の
経済・金融ニュースと前営業日の市況を受け取り、**S&P500** と **円建て全世界株式
（いわゆる「オルカン」= MSCI ACWI 連動）** への影響を分析します。

# 分析の原則

1. **事実と市場予想とのズレを中心に書く。** ニュースの要約そのものには価値がありません。
   「何が起きたか」より「それは予想と比べてどうだったか」「どこが市場の想定外だったか」を
   書いてください。予想値が判明しない場合は、そのことを明示してください。
2. **波及メカニズムを具体的に説明する。** 「悪材料だから下落」では不十分です。
   「長期金利の上昇 → 割引率の上昇 → 高PERのハイテク株に相対的に大きな重荷」のように、
   経路を1つずつ辿ってください。
3. **織り込み済みかを判断する。** 既に報じられ株価に反映されている材料と、新規の材料を
   区別してください。前者は市場を動かしません。
4. **確度を正直に申告する。** 自信がない判断には `confidence: low` を使ってください。
   すべてを `high` にすることは、分析として無価値です。
5. **記事の原文を転載しないでください。** 自分の言葉で要約・分析を書き、出典は
   `source_urls` で示します。

# 影響の時間軸（厳密に使い分けてください）

- `immediate`（早急 / 当日〜翌日）: 即座に反応する、または既に織り込まれている材料
- `short_term`（数日後 / 2〜5日）: 機関投資家の資金移動や連日の報道でジワジワ効く材料
- `long_term`（中長期 / 数週間〜）: 金利トレンドや業績前提を変える構造的な変化

# 方向性

- `up`: 上昇要因 / `down`: 下落要因 / `volatility`: 方向は不明だがボラティリティ拡大要因

# 円建て全世界株式（オルカン）の観点 ★重要

MSCI ACWI の地域構成比は概ね **米国 60% / 欧州（先進）12% / 日本 5% / その他先進国 13% /
新興国 10%** です。この構成比を踏まえて S&P500 との差を判断してください。

- **米国固有の材料**（米国の規制、米国の雇用統計など）は S&P500 に強く効きますが、
  オルカンでは **約60%に希釈** されます。`scope: us_specific` を使ってください。
- **非米国の材料**（中国の景気刺激策、ECBの政策、新興国の混乱など）は S&P500 には
  限定的ですが、**オルカンには相対的に大きく効きます**。`scope: global` を使ってください。
- **為替**: オルカンは円建てです。**円安（USD/JPYの上昇）は円建てリターンにプラス、
  円高はマイナス**です。日銀の金融政策、円キャリー取引の巻き戻し、米金利と日本金利の
  差の変化などは、S&P500 の分析では脇役でも **円建てオルカン保有者には直撃します**。
  `fx_impact` で円相場への影響を必ず判定してください。

`sentiment_score` は S&P500 向け、`acwi_sentiment_score` は **為替影響を織り込んだ
円建てオルカン向け**です。上記3つの理由により両者は乖離することがあります。乖離する
場合は、それぞれの `*_summary` でなぜ違うのかに触れてください。

# 出力について

- すべて**日本語**で記述してください（ティッカーや固有名詞の原語表記は可）。
- `news_analysis` は重要度の高い順に **最大 {max_news} 件**。無理に数を揃えないでください。
- `report_markdown` は Web 閲覧用の本文です。見出し（`##`）を使い、2,000〜3,000字程度。
  S&P500 の分析に加えて、**「オルカン（円建て全世界株式）保有者向けの観点」という節**を
  必ず設けてください。
- `x_post_text` は X 投稿用の**本文のみ**です。日付や騰落率のプレフィックスは
  **付けないでください**（システム側で付与します）。全角 {x_chars} 文字から
  プレフィックス分を引いた残りに収める必要があるため、**全角 {x_body_chars} 文字以内**で
  書いてください。ハッシュタグとURLは入れないでください。
- `key_numbers` には記事中の数値（実績値・市場予想・前回値）を入れてください。
  不明な項目は空文字にし、推測で埋めないでください。
"""

PERIOD_SYSTEM = """\
あなたは米国株式市場を専門とする機関投資家向けのアナリストです。{period_ja}の
レポートを作成します。

入力として、その期間の**日次（または下位期間の）解析結果**と、**機械的に集計された
統計値**が与えられます。

# 原則

1. **数値を自分で計算しないでください。** センチメントの平均、騰落率、的中率などは
   すべて集計済みの値として与えられます。それを**正しく引用**してください。
   与えられていない数値を推測で書かないでください。
2. **日次を並べ直すのではなく、流れを書いてください。** 「何が継続し、何が解消し、
   何が新たに現れたか」が{period_ja}レポートの価値です。`persistence` で分類してください。
3. **予測の振り返りでは、与えられたベンチマークと必ず比較してください。**
   S&P500 には長期的な上昇バイアスがあるため、「常に上昇と判定する」だけで的中率は
   50%を超えます。**ベンチマークとの差（edge）こそが意味のある数字**です。
   サンプル数が少ない場合（30未満）は、統計的に結論を出せないことを明記してください。
4. **円建て全世界株式（オルカン）の振り返りでは、リターンを株価要因と為替要因に
   分解して説明してください。** 「ドル建てでは下落したが円安で円建てでは小幅安に
   とどまった」といった説明が、円建て投資家にとって最も重要な情報です。

# 出力

- すべて**日本語**で記述してください。
- `report_markdown` は見出し（`##`）を使い、{md_chars}字程度。
- `x_post_text` は X 投稿用の**本文のみ**（プレフィックスはシステムが付与）。
  **全角 {x_body_chars} 文字以内**。ハッシュタグとURLは入れないでください。
"""

PERIOD_JA: dict[str, str] = {"weekly": "週次", "monthly": "月次", "yearly": "年次"}
_PERIOD_MD_CHARS: dict[str, str] = {
    "weekly": "2,000〜2,500",
    "monthly": "2,500〜3,500",
    "yearly": "4,000〜5,000",
}


def daily_system(settings: Settings, x_body_chars: int) -> str:
    return DAILY_SYSTEM.format(
        max_news=settings.max_news_analysis,
        x_chars=settings.x_post_max_fullwidth_chars,
        x_body_chars=x_body_chars,
    )


def period_system(period_type: PeriodType, x_body_chars: int) -> str:
    return PERIOD_SYSTEM.format(
        period_ja=PERIOD_JA[period_type],
        md_chars=_PERIOD_MD_CHARS[period_type],
        x_body_chars=x_body_chars,
    )


def _market_block(settings: Settings, snapshot: MarketSnapshot) -> str:
    lines = [f"基準日: {snapshot.reference_date}（米国株の最新営業日）", ""]
    groups: dict[str, list[str]] = {}
    for symbol in settings.ai_symbols:
        quote = snapshot.get(symbol)
        if not quote or quote.close is None:
            continue
        chg = f"{quote.change_pct:+.2f}%" if quote.change_pct is not None else "—"
        groups.setdefault(quote.group, []).append(
            f"  - {quote.label}（{symbol}）: {quote.close:,.2f}  前日比 {chg}"
        )
    group_ja = {
        "index": "米国主要指数",
        "global": "全世界株式（ドル建て）",
        "global_jpy": "全世界株式（円建てETF・東証）",
        "fx": "為替",
        "vol": "ボラティリティ",
        "rates": "米国債利回り（%）",
        "region": "地域別",
        "commodity": "商品",
        "macro": "その他",
    }
    for key, title in group_ja.items():
        if key in groups:
            lines.append(f"[{title}]")
            lines.extend(groups[key])
            lines.append("")

    gv = snapshot.global_view
    if gv.jpy_theoretical_change_pct is not None:
        lines += [
            "[オルカン（円建て全世界株式）の内訳]",
            f"  - 円建て変化率（ACWI×USD/JPY の理論値）: {gv.jpy_theoretical_change_pct:+.2f}%",
            f"      うち 株価要因 {gv.contribution_equity_pct:+.2f}% "
            f"/ 為替要因 {gv.contribution_fx_pct:+.2f}%",
        ]
        if gv.etf_jpy_change_pct is not None:
            lines.append(
                f"  - 参考: 2559.T（東証上場ACWI連動ETF）実測 {gv.etf_jpy_change_pct:+.2f}%"
                f"（{gv.etf_jpy_date}。東証引けのため1営業日前の値）"
            )
        lines.append("")
    return "\n".join(lines)


def daily_user(
    settings: Settings,
    target_date: date,
    snapshot: MarketSnapshot,
    articles: list,
) -> str:
    parts = [
        f"# 対象日: {target_date.isoformat()}（日本時間の朝に作成するレポート）",
        "",
        "# 前営業日の市況",
        "",
        _market_block(settings, snapshot),
        "# 過去24時間の主要ニュース（重複排除後・重要度順）",
        "",
    ]
    for idx, art in enumerate(articles, 1):
        summary = summary_for_ai(art, settings)
        parts.append(f"## {idx}. {art.title}")
        parts.append(f"- 媒体: {art.feed_name}" + ("（要購読）" if art.paywall else ""))
        if art.published:
            parts.append(f"- 配信: {art.published}")
        if art.tickers:
            parts.append(f"- 関連ティッカー（配信元のメタデータ）: {', '.join(art.tickers)}")
        if summary:
            parts.append(f"- 要約: {summary}")
        else:
            parts.append("- 要約: （この配信元は要約を提供していません。見出しのみ）")
        parts.append(f"- URL: {art.link}")
        parts.append("")
    parts += [
        "---",
        "",
        "以上を踏まえ、S&P500 と円建て全世界株式（オルカン）への影響を分析してください。",
        "ニュースの中に S&P500 やオルカンへの影響が乏しいものが含まれている場合は、",
        "`news_analysis` に無理に含めないでください。",
    ]
    return "\n".join(parts)


def _counts_line(label: str, counts: dict[str, int], ja: dict[str, str] | None = None) -> str:
    if not counts:
        return f"  - {label}: データなし"
    items = sorted(counts.items(), key=lambda kv: -kv[1])
    body = " / ".join(f"{(ja or {}).get(k, k)} {v}件" for k, v in items)
    return f"  - {label}: {body}"


def _forecast_block(stats: PeriodStats) -> str:
    lines = ["[予測の的中率（機械集計）]"]
    for name, fc in (("S&P500", stats.forecast_spx), ("オルカン（円建て）", stats.forecast_acwi)):
        if fc.sample_size == 0 or fc.hit_rate is None:
            lines.append(f"  - {name}: 判定対象日なし")
            continue
        lines.append(f"  - {name}: 的中 {fc.hits}/{fc.sample_size} = {fc.hit_rate:.1%}")
        if fc.always_up_hit_rate is not None and fc.always_down_hit_rate is not None:
            lines.append(
                f"      ベンチマーク: 常に上昇と判定 {fc.always_up_hit_rate:.1%} / "
                f"常に下落と判定 {fc.always_down_hit_rate:.1%}"
            )
        if fc.edge_vs_always_up_pp is not None:
            lines.append(f"      常に上昇との差（edge）: {fc.edge_vs_always_up_pp:+.1f}ポイント")
        if fc.note:
            lines.append(f"      注記: {fc.note}")
    return "\n".join(lines)


def period_user(
    period_type: PeriodType,
    period_label: str,
    period_start: str,
    period_end: str,
    stats: PeriodStats,
    source_summaries: list[str],
) -> str:
    from ..models import (
        FX_IMPACT_JA,
        HORIZON_JA,
        NEWS_CATEGORY_JA,
        NOVELTY_JA,
        REGION_JA,
        SCOPE_JA,
        SECTOR_JA,
        SURPRISE_JA,
    )

    parts = [
        f"# 対象期間: {period_label}（{period_start} 〜 {period_end}）",
        f"# 集計対象: {stats.days} 日分",
        "",
        "# 機械集計された統計値（この値をそのまま使ってください）",
        "",
        "[センチメント]",
        f"  - S&P500 平均 {stats.sentiment_avg} / 最低 {stats.sentiment_min} "
        f"/ 最高 {stats.sentiment_max}",
        f"  - オルカン 平均 {stats.acwi_sentiment_avg}",
        "",
        "[市況]",
        f"  - S&P500 期間騰落率: {_fmt(stats.spx_period_change_pct)}",
        f"  - ACWI（ドル建て）期間騰落率: {_fmt(stats.acwi_period_change_pct)}",
        f"  - USD/JPY 期間変化率: {_fmt(stats.usdjpy_period_change_pct)}",
        f"  - 円建て全世界株 期間騰落率（理論値）: {_fmt(stats.jpy_global_period_change_pct)}",
        f"      うち 株価要因 {_fmt(stats.contribution_equity_pct)} "
        f"/ 為替要因 {_fmt(stats.contribution_fx_pct)}",
        f"  - S&P500 と ACWI の差: {_fmt(stats.spx_vs_acwi_gap_pp, 'pp')}",
        f"  - VIX 平均 {stats.vix_avg} / 最大 {stats.vix_max}",
        f"  - 米10年債利回り 期間変化: {_fmt(stats.tnx_change_bp, 'bp')}",
    ]
    if stats.spx_best_day:
        day, chg = stats.spx_best_day
        parts.append(f"  - 期間中で最も良かった日: {day} ({chg:+.2f}%)")
    if stats.spx_worst_day:
        day, chg = stats.spx_worst_day
        parts.append(f"  - 期間中で最も悪かった日: {day} ({chg:+.2f}%)")
    parts.append("")

    parts.append("[判定の分布]")
    parts += [
        _counts_line("方向", stats.direction_counts, DIRECTION_JA),
        _counts_line("時間軸", stats.horizon_counts, HORIZON_JA),
        _counts_line("ニュース分類", stats.news_category_counts, NEWS_CATEGORY_JA),
        _counts_line("影響セクター", stats.sector_counts, SECTOR_JA),
        _counts_line("予想との比較", stats.surprise_counts, SURPRISE_JA),
        _counts_line("新規性", stats.novelty_counts, NOVELTY_JA),
        _counts_line("地域", stats.region_counts, REGION_JA),
        _counts_line("影響範囲", stats.scope_counts, SCOPE_JA),
        _counts_line("円相場への影響", stats.fx_impact_counts, FX_IMPACT_JA),
        "",
    ]
    if stats.ticker_counts:
        top = sorted(stats.ticker_counts.items(), key=lambda kv: -kv[1])[:10]
        parts.append("  - 言及の多い銘柄: " + " / ".join(f"{k} {v}回" for k, v in top))
        parts.append("")
    if stats.region_etf_changes:
        parts.append("[地域別ETFの期間騰落率]")
        parts += [f"  - {k}: {v:+.2f}%" for k, v in stats.region_etf_changes.items()]
        parts.append("")
    if stats.sector_etf_changes:
        parts.append("[セクターETFの期間騰落率]")
        parts += [f"  - {k}: {v:+.2f}%" for k, v in stats.sector_etf_changes.items()]
        parts.append("")
    parts.append(_forecast_block(stats))
    parts.append("")
    if stats.missing_dates:
        parts.append(
            f"[注意] 以下の日のデータが欠けています（レポートに明記してください）: "
            f"{', '.join(stats.missing_dates)}"
        )
        parts.append("")

    parts.append(f"# {PERIOD_JA[period_type]}の集計対象となった各期間の解析結果")
    parts.append("")
    parts.extend(source_summaries)
    parts += [
        "",
        "---",
        "",
        f"以上を踏まえ、{PERIOD_JA[period_type]}レポートを作成してください。",
        "統計値は与えられたものをそのまま引用し、自分で計算し直さないでください。",
    ]
    return "\n".join(parts)


def _fmt(value: float | None, unit: str = "%") -> str:
    if value is None:
        return "—"
    return f"{value:+.2f}{unit}"
