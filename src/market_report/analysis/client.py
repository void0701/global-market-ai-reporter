"""Bedrock クライアント.

``bedrock-mantle`` エンドポイント（Messages API 互換）を ``AnthropicBedrockMantle``
経由で使う（docs/02 §1）。旧 ``bedrock-runtime`` の ``InvokeModel`` は使わない。

* ``thinking`` は渡さない — Opus 5 は adaptive thinking が既定 ON。
  旧式の ``budget_tokens`` 指定は 400 になる（docs/02）。
* ``temperature`` 等のサンプリングパラメータは使えない（400）。
* ``thinking.display="summarized"`` を指定して推論要約を保存する。
  **課金は display 設定によらず同一**のため追加コストはない（docs/11 §3）。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from ..utils import logging as log
from ..utils.text import sha1

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# ファーストパーティの参考単価（USD / 1M トークン）。
# Amazon Bedrock は AWS の別料金体系であり実単価は要確認（docs/03）。
PRICING: dict[str, tuple[float, float]] = {
    "anthropic.claude-opus-5": (5.0, 25.0),
    "anthropic.claude-opus-5-5": (5.0, 25.0),
    "anthropic.claude-opus-4-8": (5.0, 25.0),
    "anthropic.claude-sonnet-5": (2.0, 10.0),
    "anthropic.claude-sonnet-5-5": (2.0, 10.0),
    "anthropic.claude-haiku-4-5": (1.0, 5.0),
    "anthropic.claude-fable-5-1": (10.0, 50.0),
}
_DEFAULT_PRICE = (5.0, 25.0)


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    inp, out = PRICING.get(model, _DEFAULT_PRICE)
    return round(input_tokens * inp / 1_000_000 + output_tokens * out / 1_000_000, 6)


@dataclass
class LlmResult:
    parsed: BaseModel
    content_raw: str
    thinking_summary: str
    stop_reason: str | None
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: float
    schema_hash: str


class BedrockAnalysisError(RuntimeError):
    """解析呼び出しが最終的に失敗した."""


class CredentialsError(RuntimeError):
    """AWS 認証情報を解決できない（セットアップ未完了）."""


CREDENTIALS_HELP = """AWS の認証情報を解決できませんでした。

以下のいずれかを設定してください（docs/07-prerequisites.md）:

  1. IAM Identity Center（推奨 / 長期キーをディスクに置かない）
       aws configure sso
       export AWS_PROFILE=<プロファイル名>

  2. 環境変数
       export AWS_ACCESS_KEY_ID=...
       export AWS_SECRET_ACCESS_KEY=...
       export AWS_REGION=ap-northeast-1

AWS を使わずにパイプラインを検証するには --dry-run を付けてください（無料）:
  market-report run --dry-run"""


def dereference_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """``$defs`` / ``$ref`` を展開する.

    ``messages.parse`` が使えない環境で ``output_config.format`` に
    生スキーマを渡すときのフォールバック用。
    """
    defs = schema.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                target = defs.get(ref.split("/")[-1], {})
                merged = {k: v for k, v in node.items() if k != "$ref"}
                return walk({**target, **merged})
            return {k: walk(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    out = walk(schema)
    out.pop("$defs", None)
    return out


class BedrockAnalyzer:
    """構造化出力で 1 回の解析を行う."""

    def __init__(self, model: str, region: str, max_tokens: int, thinking_display: str):
        self.model = model
        self.region = region
        self.max_tokens = max_tokens
        self.thinking_display = thinking_display
        self._client: Any | None = None

    @property
    def client(self) -> Any:
        if self._client is None:
            from anthropic import AnthropicBedrockMantle

            try:
                self._client = AnthropicBedrockMantle(aws_region=self.region)
            except (RuntimeError, ValueError) as exc:
                raise CredentialsError(CREDENTIALS_HELP) from exc
        return self._client

    def _thinking(self) -> dict[str, str]:
        # type だけ指定し budget_tokens は渡さない（現世代では 400）
        return {"type": "adaptive", "display": self.thinking_display}

    def run(self, system: str, user: str, output_model: type[T]) -> LlmResult:
        schema = output_model.model_json_schema()
        schema_hash = sha1(json.dumps(schema, sort_keys=True, ensure_ascii=False))
        started = time.perf_counter()

        client = self.client
        common = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "thinking": self._thinking(),
        }
        try:
            # ストリーミングを使う理由: 非ストリーミングは max_tokens が約 21,333 を
            # 超えると SDK が ValueError で拒否する（10 分の HTTP タイムアウト対策）。
            # 日本語レポート + 思考トークンで 15K 前後になりうるため、余裕を持った
            # max_tokens を使えるストリーミングを既定とする。
            with client.messages.stream(output_format=output_model, **common) as stream:
                message = stream.get_final_message()
            parsed = getattr(message, "parsed_output", None)
        except (AttributeError, TypeError) as exc:
            # 構造化出力つきストリーミングが使えない場合のフォールバック（docs/00 E-2）
            log.warn(logger, "stream+output_format が使えないため代替経路", error=str(exc))
            with client.messages.stream(
                output_config={
                    "format": {"type": "json_schema", "schema": dereference_schema(schema)}
                },
                **common,
            ) as stream:
                message = stream.get_final_message()
            parsed = None
        except RuntimeError as exc:
            if "credential" in str(exc).lower():
                raise CredentialsError(CREDENTIALS_HELP) from exc
            raise

        latency_ms = int((time.perf_counter() - started) * 1000)
        text_blocks: list[str] = []
        thinking_parts: list[str] = []
        for block in message.content:
            btype = getattr(block, "type", "")
            if btype == "text":
                text_blocks.append(getattr(block, "text", "") or "")
            elif btype == "thinking":
                thinking_parts.append(getattr(block, "thinking", "") or "")
        content_raw = "".join(text_blocks)

        stop_reason = getattr(message, "stop_reason", None)
        if stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            raise BedrockAnalysisError(f"モデルが応答を拒否しました: {details}")
        if stop_reason == "max_tokens":
            raise BedrockAnalysisError(
                f"max_tokens({self.max_tokens}) に達して出力が途切れました。"
                "settings.toml の bedrock.max_tokens を増やしてください"
            )

        if parsed is None:
            try:
                parsed = output_model.model_validate_json(content_raw)
            except ValidationError as exc:
                raise BedrockAnalysisError(f"構造化出力の検証に失敗: {exc}") from exc

        usage = getattr(message, "usage", None)
        inp = int(getattr(usage, "input_tokens", 0) or 0)
        out = int(getattr(usage, "output_tokens", 0) or 0)
        cost = estimate_cost(self.model, inp, out)

        log.info(
            logger,
            "Bedrock 解析完了",
            model=self.model,
            input_tokens=inp,
            output_tokens=out,
            latency_ms=latency_ms,
            estimated_cost_usd=cost,
            stop_reason=stop_reason,
        )
        return LlmResult(
            parsed=parsed,
            content_raw=content_raw or parsed.model_dump_json(),
            thinking_summary="\n\n".join(p for p in thinking_parts if p),
            stop_reason=stop_reason,
            input_tokens=inp,
            output_tokens=out,
            latency_ms=latency_ms,
            cost_usd=cost,
            schema_hash=schema_hash,
        )
