"""構造化ログ. CloudWatch Logs Insights で集計できる JSON 1 行形式."""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "extra_fields", None)
        if extra:
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    """ローカル実行時の読みやすい形式."""

    def format(self, record: logging.LogRecord) -> str:
        extra = getattr(record, "extra_fields", None) or {}
        tail = " ".join(f"{k}={v}" for k, v in extra.items())
        head = f"{record.levelname:<7} {record.getMessage()}"
        line = f"{head}  {tail}" if tail else head
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def setup_logging(json_format: bool | None = None, level: str = "INFO") -> None:
    if json_format is None:
        # Lambda 上では AWS_LAMBDA_FUNCTION_NAME が入る
        json_format = bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter() if json_format else TextFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log(logger: logging.Logger, level: int, message: str, **fields: Any) -> None:
    logger.log(level, message, extra={"extra_fields": fields})


def info(logger: logging.Logger, message: str, **fields: Any) -> None:
    log(logger, logging.INFO, message, **fields)


def warn(logger: logging.Logger, message: str, **fields: Any) -> None:
    log(logger, logging.WARNING, message, **fields)


def error(logger: logging.Logger, message: str, **fields: Any) -> None:
    log(logger, logging.ERROR, message, **fields)
