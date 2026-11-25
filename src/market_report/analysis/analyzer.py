"""解析のオーケストレーション.

X 投稿文の文字数管理をここで行う（docs/04 §3）。

* プレフィックス（日付 + 騰落率）は**コード側で付与**する。Error 187（重複投稿）
  回避の確実性と、残り文字数をコードが完全に管理できるようにするため
* 140 全角の判定も**コード側**。モデルに文字数を数えさせるのは不確実
* 超過時は「短縮を 1 回依頼 → それでも超えたら決定的に切り詰め」
"""

from __future__ import annotations

import logging
from datetime import date

from pydantic import BaseModel, Field

from ..config import Settings
from ..models import (
    AiDailyOutput,
    AiPeriodOutput,
    Article,
    MarketSnapshot,
    PeriodStats,
    PeriodType,
)
from ..utils import logging as log
from ..utils.text import truncate_weighted, x_weighted_len
from . import fixtures, prompts
from .client import BedrockAnalyzer, LlmResult, estimate_cost

logger = logging.getLogger(__name__)

PREFIX_SEPARATOR = " "


class _ShortText(BaseModel):
    """短縮依頼用の最小スキーマ."""

    text: str = Field(description="短縮した本文のみ")


def daily_prefix(snapshot: MarketSnapshot, target_date: date) -> str:
    """``【11/21 米株 -3.70% / 全世界株(円) -1.32%】``.

    日付と 2 つの騰落率が毎日変わるため、Error 187（重複投稿）を構造的に回避する。
    """
    spx = snapshot.change_pct("^GSPC")
    jpy = snapshot.global_view.jpy_theoretical_change_pct
    ref = snapshot.reference_date or target_date.isoformat()
    md = f"{int(ref[5:7])}/{int(ref[8:10])}" if len(ref) >= 10 else target_date.strftime("%-m/%-d")
    spx_s = f"{spx:+.2f}%" if spx is not None else "—"
    jpy_s = f"{jpy:+.2f}%" if jpy is not None else "—"
    return f"【{md} 米株 {spx_s} / 全世界株(円) {jpy_s}】"


def period_prefix(period_type: PeriodType, label: str, stats: PeriodStats) -> str:
    kind = {"weekly": "週次", "monthly": "月次", "yearly": "年次"}[period_type]
    spx = stats.spx_period_change_pct
    jpy = stats.jpy_global_period_change_pct
    spx_s = f"{spx:+.2f}%" if spx is not None else "—"
    jpy_s = f"{jpy:+.2f}%" if jpy is not None else "—"
    return f"【{label} {kind} 米株 {spx_s} / 全世界株(円) {jpy_s}】"


def body_budget(settings: Settings, prefix: str) -> int:
    """プレフィックスを引いた本文に使える重み."""
    return settings.x_weight_limit - x_weighted_len(prefix + PREFIX_SEPARATOR)


def compose_x_post(prefix: str, body: str, limit: int) -> str:
    """プレフィックスを付けて全体を上限内に収める."""
    budget = limit - x_weighted_len(prefix + PREFIX_SEPARATOR)
    return prefix + PREFIX_SEPARATOR + truncate_weighted(body.strip(), max(1, budget))


class Analyzer:
    """日次・期間の解析を実行する."""

    def __init__(self, settings: Settings, *, dry_run: bool = False):
        self.settings = settings
        self.dry_run = dry_run
        self._bedrock: BedrockAnalyzer | None = None

    @property
    def bedrock(self) -> BedrockAnalyzer:
        if self._bedrock is None:
            self._bedrock = BedrockAnalyzer(
                model=self.settings.model_id,
                region=self.settings.region,
                max_tokens=self.settings.max_tokens,
                thinking_display=self.settings.thinking_display,
                provider=self.settings.provider,
            )
        return self._bedrock

    # ------------------------------------------------------------------
    # 日次
    # ------------------------------------------------------------------

    def analyze_daily(
        self, target_date: date, snapshot: MarketSnapshot, articles: list[Article]
    ) -> tuple[AiDailyOutput, LlmResult | None, str, str]:
        """``(解析結果, LLM 呼び出し記録, systemプロンプト, userプロンプト)``."""
        prefix = daily_prefix(snapshot, target_date)
        budget = body_budget(self.settings, prefix)
        system = prompts.daily_system(self.settings, int(budget / 2))
        user = prompts.daily_user(self.settings, target_date, snapshot, articles)

        if self.dry_run:
            output = fixtures.daily_output(
                target_date, snapshot, articles, self.settings.max_news_analysis
            )
            log.info(logger, "ドライラン（Bedrock 未呼び出し）", articles=len(articles))
            return self._finish_daily(output, prefix, budget), None, system, user

        result = self.bedrock.run(system, user, AiDailyOutput)
        parsed = result.parsed
        assert isinstance(parsed, AiDailyOutput)
        output = self._trim_news(parsed)
        return self._finish_daily(output, prefix, budget), result, system, user

    def _trim_news(self, output: AiDailyOutput) -> AiDailyOutput:
        """``max_news_analysis`` を超えた分を落とす（スキーマでは強制できない）."""
        limit = self.settings.max_news_analysis
        if len(output.news_analysis) > limit:
            log.warn(
                logger,
                "news_analysis が上限を超えたため切り詰め",
                got=len(output.news_analysis),
                limit=limit,
            )
            output.news_analysis = output.news_analysis[:limit]
        return output

    def _finish_daily(self, output: AiDailyOutput, prefix: str, budget: int) -> AiDailyOutput:
        output.x_post_text = self._fit_body(output.x_post_text, budget, "daily")
        return output

    # ------------------------------------------------------------------
    # 期間
    # ------------------------------------------------------------------

    def analyze_period(
        self,
        period_type: PeriodType,
        label: str,
        start: date,
        end: date,
        stats: PeriodStats,
        source_summaries: list[str],
    ) -> tuple[AiPeriodOutput, LlmResult | None, str, str]:
        prefix = period_prefix(period_type, label, stats)
        budget = body_budget(self.settings, prefix)
        system = prompts.period_system(period_type, int(budget / 2))
        user = prompts.period_user(
            period_type, label, start.isoformat(), end.isoformat(), stats, source_summaries
        )

        if self.dry_run:
            output = fixtures.period_output(period_type, label, stats)
            log.info(logger, "ドライラン（Bedrock 未呼び出し）", period=label)
            output.x_post_text = self._fit_body(output.x_post_text, budget, period_type)
            return output, None, system, user

        result = self.bedrock.run(system, user, AiPeriodOutput)
        parsed = result.parsed
        assert isinstance(parsed, AiPeriodOutput)
        parsed.x_post_text = self._fit_body(parsed.x_post_text, budget, period_type)
        return parsed, result, system, user

    # ------------------------------------------------------------------
    # X 投稿文の文字数
    # ------------------------------------------------------------------

    def _fit_body(self, body: str, budget: int, kind: str) -> str:
        body = body.strip()
        weight = x_weighted_len(body)
        if weight <= budget:
            return body
        log.warn(
            logger,
            "X投稿本文が上限超過。短縮を依頼",
            kind=kind,
            weight=weight,
            budget=budget,
            over_fullwidth=round((weight - budget) / 2, 1),
        )
        if not self.dry_run:
            shortened = self._request_shorter(body, budget)
            if shortened and x_weighted_len(shortened) <= budget:
                return shortened
            log.warn(logger, "短縮依頼でも収まらず決定的に切り詰め", kind=kind)
        return truncate_weighted(body, budget)

    def _request_shorter(self, body: str, budget: int) -> str | None:
        """短縮を 1 回だけ依頼する（レポート全体は作り直さない）."""
        chars = int(budget / 2)
        system = (
            "あなたは編集者です。与えられた日本語の文章を、意味を保ったまま指定の"
            "文字数以内に短縮してください。ハッシュタグとURLは追加しないでください。"
        )
        user = f"全角 {chars} 文字以内に短縮してください。\n\n---\n{body}"
        try:
            result = self.bedrock.run(system, user, _ShortText)
            text = result.parsed.text  # type: ignore[attr-defined]
            log.info(logger, "短縮依頼成功", cost_usd=result.cost_usd)
            return str(text).strip()
        except Exception as exc:  # 短縮失敗は致命的でない
            log.warn(logger, "短縮依頼に失敗", error=f"{type(exc).__name__}: {exc}")
            return None


def estimate_reanalyze_cost(model: str, days: int) -> float:
    """再解析の概算コスト（docs/11 §7 — 一括実行前に提示する）."""
    return round(estimate_cost(model, 5200, 9400) * days, 2)
