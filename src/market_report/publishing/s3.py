"""S3 への公開（Phase 2）.

:class:`~market_report.publishing.base.Publisher` の実装。``pipeline`` は
プロトコルにのみ依存するため、この差し替えで中核ロジックは無変更
（docs/01 §3-1）。

``rebuild_index`` は過去の全日次レコードを読むため、読み取りも実装する。
日数に比例して GET が増えるので **並列化が前提**（docs/13 §8 R-5）。
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ..utils import logging as log

logger = logging.getLogger(__name__)

# S3 は 1 回の ListObjectsV2 で最大 1000 件返す
_PAGE_SIZE = 1000


class S3Publisher:
    """1 バケットに ``raw/`` ``data/`` ``reports/`` をそのままのキーで置く."""

    def __init__(self, bucket: str, client: Any | None = None, *, max_workers: int = 16):
        if client is None:
            import boto3  # Lambda ランタイム同梱（A-150）

            client = boto3.client("s3")
        self.bucket = bucket
        self.client = client
        self.max_workers = max_workers

    # ---- 書き込み ----

    def put_text(self, key: str, text: str, content_type: str = "text/html") -> str:
        body = text.encode("utf-8")
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentType=f"{content_type}; charset=utf-8",
        )
        log.info(logger, "S3 書き出し", key=key, bytes=len(body))
        return f"s3://{self.bucket}/{key}"

    # ---- 読み込み ----

    def get_text(self, key: str) -> str | None:
        try:
            resp = self.client.get_object(Bucket=self.bucket, Key=key)
        except Exception as exc:  # noqa: BLE001 - botocore の例外型を import せず判定する
            if _is_missing(exc):
                return None
            raise
        return resp["Body"].read().decode("utf-8")

    def get_many(self, keys: list[str]) -> dict[str, str | None]:
        """複数キーを並列に読む.

        ``rebuild_index`` の逐次 GET が日数に比例して遅くなるのを避けるための
        追加 API（``Publisher`` プロトコルには無い）。
        """
        if not keys:
            return {}
        workers = min(self.max_workers, len(keys))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            texts = list(pool.map(self.get_text, keys))
        return dict(zip(keys, texts, strict=True))

    def list_keys(self, prefix: str) -> list[str]:
        keys: list[str] = []
        token: str | None = None
        while True:
            kwargs: dict[str, Any] = {
                "Bucket": self.bucket,
                "Prefix": prefix,
                "MaxKeys": _PAGE_SIZE,
            }
            if token:
                kwargs["ContinuationToken"] = token
            resp = self.client.list_objects_v2(**kwargs)
            keys.extend(obj["Key"] for obj in resp.get("Contents", []))
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
        return sorted(keys)

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
        except Exception as exc:  # noqa: BLE001 - 同上
            if _is_missing(exc):
                return False
            raise
        return True

    @property
    def location(self) -> str:
        return f"s3://{self.bucket}"


def _is_missing(exc: Exception) -> bool:
    """「鍵が無い」系のエラーか.

    ``botocore.exceptions.ClientError`` を import せずに判定する。boto3 は
    Lambda ランタイム同梱でバージョンを固定できないため、例外クラスに
    依存しないほうが壊れにくい。
    """
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    code = str(response.get("Error", {}).get("Code", ""))
    status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return code in {"NoSuchKey", "NotFound", "404"} or status == 404
