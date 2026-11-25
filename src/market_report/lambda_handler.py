"""Lambda エントリポイント（Phase 2）.

ハンドラ指定は ``market_report.lambda_handler.handler``。

役割は 3 つだけで、分析の中核は :mod:`market_report.pipeline` にある
（AWS 非依存のまま保つ / docs/01 §3-1）。

1. **シークレットの取得** — ``ANTHROPIC_API_KEY`` を Secrets Manager から
   取り、プロセス内の環境変数に載せる。**Lambda の環境変数には置かない**
   （コンソールで誰でも見えるため / docs/06 §3-0）。
2. **公開先の組み立て** — ``raw/`` ``data/`` は S3、HTML の配信は Amplify。
3. **失敗通知** — 業務エラーを SNS に流して再送出する。再送出するのは
   Lambda の ``Errors`` メトリクスを立て、CloudWatch アラーム側の検知
   （A-148 の 2 層目）も機能させるため。

イベント例::

    {"report_type": "daily"}
    {"report_type": "weekly", "period": "2026-W47"}
    {"report_type": "daily", "dry_run": true}      # スモークテスト用
"""

from __future__ import annotations

import logging
import os
from datetime import date
from typing import Any

from . import pipeline
from .config import Settings, load_settings
from .models import PeriodType
from .notify import Notifier
from .publishing.amplify import AmplifyDeployer
from .publishing.base import Publisher
from .publishing.s3 import S3Publisher
from .utils import logging as log
from .utils.logging import setup_logging

logger = logging.getLogger(__name__)

API_KEY_ENV = "ANTHROPIC_API_KEY"
PERIOD_TYPES: tuple[str, ...] = ("weekly", "monthly", "yearly")

# コールドスタート 1 回だけ取得する。1 日 1 回の実行では毎回コールドスタートに
# なるためキャッシュはほぼ効かないが、同一実行環境が再利用されたときに
# GetSecretValue を重ねないため（docs/06 §3-2）。
_secret_loaded = False


def _load_api_key(secret_id: str | None, client: Any | None = None) -> None:
    """Secrets Manager から API キーを取り、環境変数に載せる."""
    global _secret_loaded
    if _secret_loaded or not secret_id:
        return
    if os.environ.get(API_KEY_ENV):
        # ローカル実行やテストで既に入っている場合は尊重する
        _secret_loaded = True
        return
    if client is None:
        import boto3  # Lambda ランタイム同梱（A-150）

        client = boto3.client("secretsmanager")
    resp = client.get_secret_value(SecretId=secret_id)
    secret = resp.get("SecretString")
    if not secret:
        raise RuntimeError(f"シークレットが空です: {secret_id}")
    os.environ[API_KEY_ENV] = secret.strip()
    _secret_loaded = True
    log.info(logger, "API キーを Secrets Manager から取得", secret_id=secret_id)


def _publisher(bucket: str | None, settings: Settings) -> Publisher:
    if bucket:
        return S3Publisher(bucket)
    # バケット未設定はローカル実行とみなす（テスト・手元検証）
    from pathlib import Path

    from .publishing.local import LocalPublisher

    return LocalPublisher(Path(settings.output_dir))


def _deployer() -> AmplifyDeployer | None:
    app_id = os.environ.get("AMPLIFY_APP_ID")
    if not app_id:
        return None
    return AmplifyDeployer(app_id, os.environ.get("AMPLIFY_BRANCH", "main"))


def _publish_site(settings: Settings, pub: Publisher) -> dict[str, Any]:
    """サイト全体を 1 回だけ描き直し、S3 と Amplify の両方に反映する.

    ``build_site()`` は過去の全レコードを読むので安くはない。``run_daily``
    側の ``rebuild_index`` を止めて（``publish_index=False``）、ここで
    1 回にまとめている（A-160）。
    """
    pages = pipeline.build_site(settings, pub)
    if not pages:
        # レポートが 1 本も無い状態で空のサイトを配信すると既存を消してしまう
        log.warn(logger, "ページが 0 件のため配信をスキップします")
        return {"deployed": False, "reason": "ページが 0 件"}

    # S3 側のトップページも更新する（reports/ は生成時に書いてある）
    for key in ("index.html", "weekly.html", "monthly.html", "archive.html"):
        html = pages.get(key)
        if html is not None:
            pub.put_text(key, html)

    deployer = _deployer()
    if deployer is None:
        log.warn(logger, "AMPLIFY_APP_ID が未設定のため配信をスキップします")
        return {"deployed": False, "reason": "AMPLIFY_APP_ID 未設定", "pages": len(pages)}

    result = deployer.deploy(pages)
    return {
        "deployed": True,
        "job_id": result.job_id,
        "status": result.status,
        "pages": result.page_count,
        "zip_bytes": result.zip_bytes,
        "elapsed_sec": result.elapsed_sec,
    }


def handler(event: dict[str, Any] | None, context: Any = None) -> dict[str, Any]:
    setup_logging(json_format=True, level=os.environ.get("LOG_LEVEL", "INFO"))
    event = event or {}
    report_type = str(event.get("report_type") or "daily")
    dry_run = bool(event.get("dry_run", False))
    notifier = Notifier(os.environ.get("ALERT_TOPIC_ARN"))

    try:
        if not dry_run:
            _load_api_key(os.environ.get("ANTHROPIC_SECRET_ID"))

        settings = load_settings()
        pub = _publisher(os.environ.get("REPORT_BUCKET"), settings)
        target = date.fromisoformat(event["date"]) if event.get("date") else None

        if report_type == "daily":
            record = pipeline.run_daily(
                settings,
                target_date=target,
                dry_run=dry_run,
                publisher=pub,
                # index の更新は _publish_site が担う（A-160）
                publish_index=False,
            )
            summary: dict[str, Any] = {"report": "daily", "date": record.date}
        elif report_type in PERIOD_TYPES:
            ptype: PeriodType = report_type  # type: ignore[assignment]
            try:
                period = pipeline.run_period(
                    settings,
                    ptype,
                    label=event.get("period"),
                    target_date=target,
                    dry_run=dry_run,
                    publisher=pub,
                    publish_index=False,
                )
            except pipeline.InsufficientDataError as exc:
                # データ不足は異常としない（docs/09 §7-3 / A-80）
                log.info(logger, "期間レポートをスキップ", period_type=report_type, reason=str(exc))
                return {"ok": True, "skipped": str(exc), "report": report_type}
            summary = {"report": report_type, "label": period.period_label}
        else:
            raise ValueError(f"未知の report_type: {report_type}")

        summary["dry_run"] = dry_run
        summary["site"] = _publish_site(settings, pub)
        summary["ok"] = True
        log.info(logger, "完了", **{k: v for k, v in summary.items() if k != "site"})
        return summary

    except Exception as exc:
        # 通知してから再送出する。再送出で Errors メトリクスが立ち、
        # CloudWatch アラーム側の検知（A-148）も働く
        notifier.failure(f"{report_type} レポート", exc)
        log.error(logger, "失敗", report_type=report_type, error=f"{type(exc).__name__}: {exc}")
        raise
