"""AWS クライアントの最小スタブ（boto3 を使わずに Phase 2 を検証する）.

本物の API は呼ばない。Lambda ハンドラ・S3Publisher・Amplify デプロイヤが
**AWS なしで通ること**を確かめるのが目的（docs/13 §6 の「1〜4 は AWS なし」）。
"""

from __future__ import annotations

from typing import Any


class FakeS3:
    """メモリ上の 1 バケット。``list_objects_v2`` のページングも再現する."""

    def __init__(self, page_size: int = 1000):
        self.objects: dict[str, bytes] = {}
        self.page_size = page_size
        self.put_calls = 0
        self.get_calls = 0

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str = "") -> dict:  # noqa: N803
        self.objects[Key] = Body
        self.put_calls += 1
        return {"ETag": f'"{len(Body)}"'}

    def get_object(self, *, Bucket: str, Key: str) -> dict:  # noqa: N803
        self.get_calls += 1
        if Key not in self.objects:
            raise _missing_key(Key)
        return {"Body": _Body(self.objects[Key])}

    def head_object(self, *, Bucket: str, Key: str) -> dict:  # noqa: N803
        if Key not in self.objects:
            raise _missing_key(Key)
        return {"ContentLength": len(self.objects[Key])}

    def list_objects_v2(self, **kwargs: Any) -> dict:
        prefix = kwargs.get("Prefix", "")
        token = kwargs.get("ContinuationToken")
        keys = sorted(k for k in self.objects if k.startswith(prefix))
        start = int(token) if token else 0
        page = keys[start : start + self.page_size]
        end = start + len(page)
        resp: dict[str, Any] = {"Contents": [{"Key": k} for k in page]}
        if end < len(keys):
            resp["IsTruncated"] = True
            resp["NextContinuationToken"] = str(end)
        else:
            resp["IsTruncated"] = False
        return resp


class _Body:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data


def _missing_key(key: str) -> Exception:
    exc = Exception(f"NoSuchKey: {key}")
    # botocore の ClientError と同じ形に寄せる（s3.py は例外クラスに依存しない）
    exc.response = {  # type: ignore[attr-defined]
        "Error": {"Code": "NoSuchKey", "Message": "The specified key does not exist."},
        "ResponseMetadata": {"HTTPStatusCode": 404},
    }
    return exc


class FakeAmplify:
    """``create_deployment`` → zip PUT → ``start_deployment`` → ``get_job`` を再現."""

    def __init__(self, *, fail: bool = False, running_polls: int = 1):
        self.deployments: list[dict[str, Any]] = []
        self.uploads: list[bytes] = []
        self.started: list[str] = []
        self.fail = fail
        self.running_polls = running_polls
        self._polls: dict[str, int] = {}

    def create_deployment(self, *, appId: str, branchName: str) -> dict:  # noqa: N803
        job_id = str(len(self.deployments) + 1)
        self.deployments.append({"appId": appId, "branchName": branchName, "jobId": job_id})
        return {"jobId": job_id, "zipUploadUrl": f"https://upload.invalid/{job_id}"}

    def start_deployment(self, *, appId: str, branchName: str, jobId: str) -> dict:  # noqa: N803
        self.started.append(jobId)
        return {"jobSummary": {"status": "PENDING"}}

    def get_job(self, *, appId: str, branchName: str, jobId: str) -> dict:  # noqa: N803
        seen = self._polls.get(jobId, 0)
        self._polls[jobId] = seen + 1
        if seen < self.running_polls:
            return {"job": {"summary": {"status": "RUNNING"}}}
        status = "FAILED" if self.fail else "SUCCEED"
        return {"job": {"summary": {"status": status}}}

    def get_app(self, *, appId: str) -> dict:  # noqa: N803
        return {"app": {"defaultDomain": f"{appId}.amplifyapp.com"}}


class FakeSecrets:
    def __init__(self, values: dict[str, str] | None = None):
        self.values = values or {}
        self.calls = 0

    def get_secret_value(self, *, SecretId: str) -> dict:  # noqa: N803
        self.calls += 1
        if SecretId not in self.values:
            raise KeyError(SecretId)
        return {"SecretString": self.values[SecretId]}


class FakeSns:
    def __init__(self):
        self.published: list[dict[str, str]] = []

    def publish(self, *, TopicArn: str, Subject: str, Message: str) -> dict:  # noqa: N803
        self.published.append({"topic": TopicArn, "subject": Subject, "message": Message})
        return {"MessageId": str(len(self.published))}
