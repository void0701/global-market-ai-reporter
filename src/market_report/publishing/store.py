"""3 層データモデルのキー設計と読み書き（docs/11 §3）.

``raw/`` 生データ → ``data/`` 解析結果 → HTML。
``Publisher`` を介するため、Phase 2 で S3 に差し替えても変更不要。
"""

from __future__ import annotations

import json
import logging
from datetime import date

from pydantic import BaseModel, ValidationError

from ..models import DailyRecord, PeriodRecord, PeriodType
from ..utils import logging as log
from .base import Publisher

logger = logging.getLogger(__name__)

JSON_CT = "application/json"


def raw_news_key(d: date) -> str:
    return f"raw/news/{d.isoformat()}.json"


def raw_market_key(d: date) -> str:
    return f"raw/market/{d.isoformat()}.json"


def raw_llm_key(d: date, kind: str = "daily", suffix: str = "") -> str:
    tail = f"-{suffix}" if suffix else ""
    return f"raw/llm/{d.isoformat()}-{kind}{tail}.json"


def daily_key(d: date, suffix: str = "") -> str:
    tail = f"-{suffix}" if suffix else ""
    return f"data/daily/{d.isoformat()}{tail}.json"


def period_key(period_type: PeriodType, label: str, suffix: str = "") -> str:
    tail = f"-{suffix}" if suffix else ""
    return f"data/{period_type}/{label}{tail}.json"


def report_key(d: date) -> str:
    return f"reports/{d.isoformat()}.html"


def period_report_key(period_type: PeriodType, label: str) -> str:
    folder = {"weekly": "weekly", "monthly": "monthly", "yearly": "yearly"}[period_type]
    return f"{folder}/{label}.html"


def dump(publisher: Publisher, key: str, model: BaseModel) -> str:
    return publisher.put_text(
        key, json.dumps(model.model_dump(mode="json"), ensure_ascii=False, indent=2), JSON_CT
    )


def load_daily(publisher: Publisher, d: date, suffix: str = "") -> DailyRecord | None:
    return _load(publisher, daily_key(d, suffix), DailyRecord)


def load_period(
    publisher: Publisher, period_type: PeriodType, label: str, suffix: str = ""
) -> PeriodRecord | None:
    return _load(publisher, period_key(period_type, label, suffix), PeriodRecord)


def _load(publisher: Publisher, key: str, model: type[BaseModel]):
    text = publisher.get_text(key)
    if text is None:
        return None
    try:
        return model.model_validate_json(text)
    except ValidationError as exc:
        # 未知の schema_version は警告してスキップする（docs/09 §7-2 / A-79）
        log.warn(logger, "保存データの検証に失敗したためスキップ", key=key, error=str(exc)[:200])
        return None


def _get_many(publisher: Publisher, keys: list[str]) -> dict[str, str | None]:
    """``get_many`` を持つ Publisher（S3）では並列に読む.

    プロトコルを広げずにダックタイピングで拾う。``rebuild_index`` は過去の
    全日次レコードを読むため、逐次 GET だと日数に比例して遅くなる
    （docs/13 §8 R-5）。
    """
    getter = getattr(publisher, "get_many", None)
    if callable(getter):
        result: dict[str, str | None] = getter(keys)
        return result
    return {key: publisher.get_text(key) for key in keys}


def _parse(key: str, text: str | None, model: type[BaseModel]):
    if text is None:
        return None
    try:
        return model.model_validate_json(text)
    except ValidationError as exc:
        log.warn(logger, "保存データの検証に失敗したためスキップ", key=key, error=str(exc)[:200])
        return None


def load_daily_many(publisher: Publisher, dates: list[date]) -> list[DailyRecord]:
    """``dates`` の日次レコードをまとめて読む（取得できたものだけ・日付順）."""
    keys = [daily_key(d) for d in dates]
    texts = _get_many(publisher, keys)
    out: list[DailyRecord] = []
    for key in keys:
        record = _parse(key, texts.get(key), DailyRecord)
        if isinstance(record, DailyRecord):
            out.append(record)
    return out


def load_period_many(
    publisher: Publisher, period_type: PeriodType, labels: list[str]
) -> list[PeriodRecord]:
    """``labels`` の期間レコードをまとめて読む（取得できたものだけ・ラベル順）."""
    keys = [period_key(period_type, label) for label in labels]
    texts = _get_many(publisher, keys)
    out: list[PeriodRecord] = []
    for key in keys:
        record = _parse(key, texts.get(key), PeriodRecord)
        if isinstance(record, PeriodRecord):
            out.append(record)
    return out


def list_daily_dates(publisher: Publisher) -> list[date]:
    out: list[date] = []
    for key in publisher.list_keys("data/daily"):
        stem = key.rsplit("/", 1)[-1].removesuffix(".json")
        try:
            out.append(date.fromisoformat(stem))
        except ValueError:
            continue  # 再解析の suffix 付きファイルは対象外
    return sorted(out)


def list_period_labels(publisher: Publisher, period_type: PeriodType) -> list[str]:
    labels = []
    for key in publisher.list_keys(f"data/{period_type}"):
        stem = key.rsplit("/", 1)[-1].removesuffix(".json")
        if stem:
            labels.append(stem)
    return sorted(labels)
