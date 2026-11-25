"""AWS Amplify Hosting への配信（Phase 2 / docs/05 A-131）.

CloudFront を自前で作れないアカウント制約の回避策として採用した
（docs/06 §8-9）。内部では AWS 管理の CloudFront + S3 が使われる。

> ### ⚠️ 手動デプロイはサイト全体の置き換え
>
> ``--file-map`` に宣言してアップロードしなかったファイルは**消える**
> （実機検証済み / docs/13 §8 R-3）。md5 は「変更なしなので据え置き」の
> 印にはならない。したがって **毎回サイト全体を zip で送る**。
> 実測で HTML 96KB → zip 33KB（約 3 倍圧縮）、5 年分でも zip 約 13MB。
"""

from __future__ import annotations

import io
import logging
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from typing import Any

from ..utils import logging as log

logger = logging.getLogger(__name__)

# CreateDeployment から StartDeployment までの猶予は 8 時間（AWS 仕様）。
# ここでのポーリングはジョブ完了待ちで、実測 16〜24 秒で SUCCEED する。
POLL_INTERVAL_SEC = 5.0
POLL_TIMEOUT_SEC = 300.0
_TERMINAL = {"SUCCEED", "FAILED", "CANCELLED"}


class AmplifyDeployError(RuntimeError):
    """デプロイが失敗、またはタイムアウトした."""


@dataclass
class DeployResult:
    job_id: str
    status: str
    page_count: int
    zip_bytes: int
    elapsed_sec: float

    @property
    def ok(self) -> bool:
        return self.status == "SUCCEED"


def build_zip(pages: dict[str, str]) -> bytes:
    """``{キー: HTML}`` を Amplify が受け取れる zip にする.

    ディレクトリ構造はキーのまま（``reports/2026-11-25.html`` など）。
    実機検証でサブディレクトリが 200 を返すことを確認済み。
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for key in sorted(pages):
            archive.writestr(key, pages[key].encode("utf-8"))
    return buffer.getvalue()


class AmplifyDeployer:
    """手動デプロイでサイト全体を差し替える."""

    def __init__(
        self,
        app_id: str,
        branch: str = "main",
        client: Any | None = None,
        *,
        wait: bool = True,
        timeout_sec: float = POLL_TIMEOUT_SEC,
    ):
        if client is None:
            import boto3  # Lambda ランタイム同梱（A-150）

            client = boto3.client("amplify")
        self.app_id = app_id
        self.branch = branch
        self.client = client
        self.wait = wait
        self.timeout_sec = timeout_sec

    def deploy(self, pages: dict[str, str]) -> DeployResult:
        if not pages:
            raise AmplifyDeployError("デプロイ対象のページがありません")

        payload = build_zip(pages)
        started = time.monotonic()

        created = self.client.create_deployment(appId=self.app_id, branchName=self.branch)
        job_id = str(created["jobId"])
        _put(created["zipUploadUrl"], payload)
        self.client.start_deployment(appId=self.app_id, branchName=self.branch, jobId=job_id)
        log.info(
            logger,
            "Amplify デプロイを開始",
            job_id=job_id,
            pages=len(pages),
            zip_bytes=len(payload),
        )

        status = "RUNNING"
        if self.wait:
            status = self._wait_for(job_id)

        result = DeployResult(
            job_id=job_id,
            status=status,
            page_count=len(pages),
            zip_bytes=len(payload),
            elapsed_sec=round(time.monotonic() - started, 1),
        )
        if self.wait and not result.ok:
            raise AmplifyDeployError(f"Amplify デプロイが {status} で終了しました（job {job_id}）")
        log.info(
            logger,
            "Amplify デプロイ完了",
            job_id=job_id,
            status=status,
            elapsed_sec=result.elapsed_sec,
        )
        return result

    def _wait_for(self, job_id: str) -> str:
        deadline = time.monotonic() + self.timeout_sec
        status = "PENDING"
        while time.monotonic() < deadline:
            job = self.client.get_job(appId=self.app_id, branchName=self.branch, jobId=job_id)
            status = str(job["job"]["summary"]["status"])
            if status in _TERMINAL:
                return status
            time.sleep(POLL_INTERVAL_SEC)
        raise AmplifyDeployError(
            f"Amplify デプロイが {self.timeout_sec:.0f} 秒で終わりませんでした"
            f"（job {job_id} / 最後の状態 {status}）"
        )

    def default_url(self) -> str:
        app = self.client.get_app(appId=self.app_id)
        return f"https://{self.branch}.{app['app']['defaultDomain']}"


def _put(url: str, payload: bytes) -> None:
    """署名付き URL へ zip を PUT する.

    boto3 ではなく標準ライブラリを使う。署名付き URL は認証不要で、
    ``requests`` を足す理由がない。
    """
    request = urllib.request.Request(url, data=payload, method="PUT")
    request.add_header("Content-Type", "application/zip")
    with urllib.request.urlopen(request, timeout=120) as resp:  # noqa: S310 - AWS 発行の https URL
        code = getattr(resp, "status", None)
    if code is not None and not (200 <= int(code) < 300):
        raise AmplifyDeployError(f"zip のアップロードに失敗しました: HTTP {code}")
