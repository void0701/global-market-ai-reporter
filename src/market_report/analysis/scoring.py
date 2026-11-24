"""予測の的中率.

日次の ``immediate`` 判定（当日〜翌日に効く材料）を**翌取引日**の実績と
突き合わせる（docs/09 §3）。

**ベンチマークの併記が必須**（docs/09 §3-3）。S&P500 には長期的な上昇バイアスが
あるため「常に上昇と判定する」だけで 50% を超える。ベンチマークなしの的中率は
解釈不能であり、意味を持つのは差（edge）のみ。

この指標の本来の価値は「当たったか」ではなく、**判定に系統的な偏りがないかを
検出すること**にある。
"""

from __future__ import annotations

from ..models import DailyRecord, ForecastEvaluation

_CONFIDENCE_WEIGHT = {"high": 1.5, "medium": 1.0, "low": 0.5}

MIN_MEANINGFUL_SAMPLE = 30
"""これ未満のサンプル数では統計的な結論を出せない（docs/09 §3-2）."""


def net_signal(record: DailyRecord) -> float:
    """``immediate`` 判定を確度で重み付けして合成する.

    ``volatility`` は方向を持たないため対象外。正なら上昇予想、負なら下落予想。
    """
    score = 0.0
    for item in record.analysis.news_analysis:
        if item.horizon != "immediate":
            continue
        weight = _CONFIDENCE_WEIGHT.get(item.confidence, 1.0)
        if item.direction == "up":
            score += weight
        elif item.direction == "down":
            score -= weight
    return score


def _actual(record: DailyRecord, which: str) -> float | None:
    if which == "spx":
        return record.market.change_pct("^GSPC")
    return record.market.global_view.jpy_theoretical_change_pct


def _sentiment(record: DailyRecord, which: str) -> int:
    return (
        record.analysis.sentiment_score if which == "spx" else record.analysis.acwi_sentiment_score
    )


def evaluate(records: list[DailyRecord], which: str = "spx") -> ForecastEvaluation:
    """``records`` を日付順に並べ、N 日目の予想と N+1 日目の実績を比較する.

    ``which`` が ``"spx"`` なら ``immediate`` 判定の合成、``"acwi"`` なら
    ``acwi_sentiment_score`` の符号を予想とみなす（オルカンは為替込みの総合
    判断であり、個別ニュースの direction からは合成できないため）。
    """
    ordered = sorted(records, key=lambda r: r.date)
    pairs: list[tuple[int, float]] = []  # (予想の符号, 翌日の実績%)

    for i in range(len(ordered) - 1):
        today, tomorrow = ordered[i], ordered[i + 1]
        actual = _actual(tomorrow, which)
        if actual is None:
            continue
        signal = net_signal(today) if which == "spx" else float(_sentiment(today, which))
        if signal == 0:
            continue  # 方向を示していない日は対象外
        pairs.append((1 if signal > 0 else -1, actual))

    evaluation = ForecastEvaluation(sample_size=len(pairs))
    if not pairs:
        evaluation.note = "判定対象日がありません（予想が中立、または実績データ不足）"
        return evaluation

    evaluation.hits = sum(1 for sign, actual in pairs if sign * actual > 0)
    evaluation.hit_rate = round(evaluation.hits / len(pairs), 4)
    up_days = sum(1 for _, actual in pairs if actual > 0)
    down_days = sum(1 for _, actual in pairs if actual < 0)
    evaluation.always_up_hit_rate = round(up_days / len(pairs), 4)
    evaluation.always_down_hit_rate = round(down_days / len(pairs), 4)
    evaluation.edge_vs_always_up_pp = round(
        (evaluation.hit_rate - evaluation.always_up_hit_rate) * 100, 2
    )

    notes = []
    if evaluation.sample_size < MIN_MEANINGFUL_SAMPLE:
        notes.append(
            f"サンプル数 {evaluation.sample_size} 件は統計的な結論を出せる水準"
            f"（{MIN_MEANINGFUL_SAMPLE}件以上）に達していません。参考値として扱ってください"
        )
    notes.append("方向のみの評価で騰落幅は見ていません。因果の特定もできません")
    evaluation.note = " / ".join(notes)
    return evaluation
