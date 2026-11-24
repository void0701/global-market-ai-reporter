"""設定の読み込み.

優先順は **CLI オプション > 環境変数 > config/*.toml**（docs/04 §6）。
Phase 2 で Terraform が Lambda 環境変数 ``BEDROCK_MODEL_ID`` を注入するため、
この順序を Phase 1 の時点で実装しておく。
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _project_root() -> Path:
    # src/market_report/config.py -> リポジトリルート
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class FeedConfig:
    name: str
    url: str
    weight: float = 1.0
    category: str = ""
    paywall: bool = False
    has_summary: bool = True


@dataclass(frozen=True)
class SymbolConfig:
    symbol: str
    label: str
    group: str


@dataclass
class Settings:
    # Bedrock
    model_id: str
    region: str
    max_tokens: int
    thinking_display: str

    # 収集
    window_hours: int
    max_articles_to_analyze: int
    max_per_feed: int
    summary_truncate_chars: int
    stale_feed_warn_hours: int
    fetch_timeout_sec: int
    max_workers: int

    # 重複排除
    similarity_threshold: float

    # 出力
    max_news_analysis: int
    x_post_max_fullwidth_chars: int
    output_dir: Path

    # 市況
    ai_symbols: list[str]
    symbols: dict[str, SymbolConfig]

    feeds: list[FeedConfig] = field(default_factory=list)
    project_root: Path = field(default_factory=_project_root)

    # ---- 派生 ----

    @property
    def all_symbols(self) -> list[str]:
        return list(self.symbols)

    def label(self, symbol: str) -> str:
        sc = self.symbols.get(symbol)
        return sc.label if sc else symbol

    def group(self, symbol: str) -> str:
        sc = self.symbols.get(symbol)
        return sc.group if sc else ""

    def symbols_in_group(self, group: str) -> list[str]:
        return [s for s, c in self.symbols.items() if c.group == group]

    @property
    def x_weight_limit(self) -> int:
        """X の重み上限（全角 1 文字 = 重み 2）."""
        return self.x_post_max_fullwidth_chars * 2


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"設定ファイルが見つかりません: {path}")
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _env(key: str) -> str | None:
    v = os.environ.get(key)
    return v if v not in (None, "") else None


def load_settings(
    config_dir: Path | None = None,
    *,
    model_id: str | None = None,
    region: str | None = None,
    output_dir: Path | str | None = None,
) -> Settings:
    """設定を読み込む。引数（CLI 由来）が最優先."""
    root = _project_root()
    cdir = Path(config_dir) if config_dir else root / "config"

    s = _load_toml(cdir / "settings.toml")
    f = _load_toml(cdir / "feeds.toml")

    bedrock = s.get("bedrock", {})
    collection = s.get("collection", {})
    dedupe = s.get("dedupe", {})
    output = s.get("output", {})
    market = s.get("market", {})

    resolved_model = model_id or _env("BEDROCK_MODEL_ID") or bedrock["model_id"]
    resolved_region = (
        region or _env("AWS_REGION") or _env("AWS_DEFAULT_REGION") or bedrock["region"]
    )
    resolved_out = (
        Path(output_dir)
        if output_dir
        else Path(_env("MARKET_REPORT_OUTPUT_DIR") or output.get("dir", "./output"))
    )
    if not resolved_out.is_absolute():
        resolved_out = (root / resolved_out).resolve()

    symbols = {
        sym: SymbolConfig(symbol=sym, label=cfg.get("label", sym), group=cfg.get("group", ""))
        for sym, cfg in market.get("symbols", {}).items()
    }
    ai_symbols = [s for s in market.get("ai_symbols", []) if s in symbols]

    feeds = [
        FeedConfig(
            name=item["name"],
            url=item["url"],
            weight=float(item.get("weight", 1.0)),
            category=item.get("category", ""),
            paywall=bool(item.get("paywall", False)),
            has_summary=bool(item.get("has_summary", True)),
        )
        for item in f.get("feed", [])
    ]

    return Settings(
        model_id=resolved_model,
        region=resolved_region,
        max_tokens=int(bedrock.get("max_tokens", 24000)),
        thinking_display=bedrock.get("thinking_display", "summarized"),
        window_hours=int(collection.get("window_hours", 24)),
        max_articles_to_analyze=int(collection.get("max_articles_to_analyze", 15)),
        max_per_feed=int(collection.get("max_per_feed", 4)),
        summary_truncate_chars=int(collection.get("summary_truncate_chars", 200)),
        stale_feed_warn_hours=int(collection.get("stale_feed_warn_hours", 48)),
        fetch_timeout_sec=int(collection.get("fetch_timeout_sec", 20)),
        max_workers=int(collection.get("max_workers", 8)),
        similarity_threshold=float(dedupe.get("similarity_threshold", 0.75)),
        max_news_analysis=int(output.get("max_news_analysis", 5)),
        x_post_max_fullwidth_chars=int(output.get("x_post_max_fullwidth_chars", 140)),
        output_dir=resolved_out,
        ai_symbols=ai_symbols,
        symbols=symbols,
        feeds=feeds,
        project_root=root,
    )
