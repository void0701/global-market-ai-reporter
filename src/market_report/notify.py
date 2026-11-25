"""失敗通知（SNS → メール / docs/05 A-53, A-148）.

**2 層で検知する設計の片方**。こちらはハンドラ内で捕まえた業務エラーを
原因付きで送る。ハンドラに到達しない異常（タイムアウト・OOM・import 失敗）は
CloudWatch アラーム（``Errors`` メトリクス）が拾う。前者だけでは
**タイムアウトが無通知**になる（docs/13 §8 R-6）。

通知先アドレスはここにもインフラコードにも持たない。Terraform は SNS
トピックだけを作り、購読登録は人間が 1 回だけ行う（A-143。リポジトリが
public であり、SNS の email 購読は受信者の確認クリックが必須のため）。
"""

from __future__ import annotations

import logging
import traceback
from typing import Any

from .utils import logging as log

logger = logging.getLogger(__name__)

MAX_SUBJECT = 100  # SNS の Subject 上限
MAX_BODY = 4000


class Notifier:
    """SNS トピックへ通知する。トピック未設定ならログだけ出す."""

    def __init__(self, topic_arn: str | None, client: Any | None = None):
        self.topic_arn = topic_arn
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            import boto3  # Lambda ランタイム同梱（A-150）

            self._client = boto3.client("sns")
        return self._client

    @property
    def enabled(self) -> bool:
        return bool(self.topic_arn)

    def failure(self, context: str, exc: BaseException) -> None:
        subject = f"[s-and-p] 失敗: {context}"
        body = "\n".join(
            [
                f"処理: {context}",
                f"例外: {type(exc).__name__}: {exc}",
                "",
                "--- トレースバック ---",
                "".join(traceback.format_exception(exc)),
            ]
        )
        self._send(subject, body)

    def warning(self, context: str, message: str) -> None:
        self._send(f"[s-and-p] 警告: {context}", message)

    def _send(self, subject: str, body: str) -> None:
        if not self.topic_arn:
            # dev 環境ではトピックを作らない。通知できないこと自体は異常ではない
            log.warn(logger, "通知先が未設定のため送信しません", subject=subject)
            return
        try:
            self.client.publish(
                TopicArn=self.topic_arn,
                Subject=subject[:MAX_SUBJECT],
                Message=body[:MAX_BODY],
            )
            log.info(logger, "通知を送信", subject=subject[:MAX_SUBJECT])
        except Exception as send_error:  # noqa: BLE001 - 通知の失敗で本処理を潰さない
            log.error(logger, "通知の送信に失敗", error=str(send_error)[:200])
