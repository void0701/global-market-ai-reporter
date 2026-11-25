"""Amplify デプロイヤ（署名付き URL の PUT もスタブ化）."""

from __future__ import annotations

import io
import zipfile

import pytest
from fake_aws import FakeAmplify

from market_report.publishing import amplify
from market_report.publishing.amplify import (
    AmplifyDeployer,
    AmplifyDeployError,
    build_zip,
)

PAGES = {
    "index.html": "<h1>index</h1>",
    "archive.html": "<h1>archive</h1>",
    "reports/2026-11-25.html": "<h1>日次</h1>",
    "weekly/2026-W48.html": "<h1>週次</h1>",
}


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(amplify.time, "sleep", lambda _s: None)


@pytest.fixture
def captured_uploads(monkeypatch):
    uploads: list[tuple[str, bytes]] = []
    monkeypatch.setattr(amplify, "_put", lambda url, payload: uploads.append((url, payload)))
    return uploads


def test_zip_keeps_the_key_structure():
    payload = build_zip(PAGES)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert sorted(archive.namelist()) == sorted(PAGES)
        assert archive.read("reports/2026-11-25.html").decode("utf-8") == "<h1>日次</h1>"


def test_zip_actually_compresses():
    """HTML は 3 倍程度に縮む（実測 96KB → 33KB）."""
    pages = {f"reports/d{i}.html": "<p>" + "market report " * 500 + "</p>" for i in range(30)}
    raw = sum(len(v.encode("utf-8")) for v in pages.values())
    assert len(build_zip(pages)) < raw / 3


def test_deploy_sends_the_whole_site_and_waits(no_sleep, captured_uploads):
    client = FakeAmplify(running_polls=2)
    result = AmplifyDeployer("d123", client=client).deploy(PAGES)

    assert result.ok and result.status == "SUCCEED"
    assert result.page_count == 4
    assert client.started == [result.job_id]
    # zip は 1 回だけ、全ページ分を送る（手動デプロイは全置き換え / R-3）
    assert len(captured_uploads) == 1
    with zipfile.ZipFile(io.BytesIO(captured_uploads[0][1])) as archive:
        assert sorted(archive.namelist()) == sorted(PAGES)


def test_deploy_raises_when_the_job_fails(no_sleep, captured_uploads):
    client = FakeAmplify(fail=True, running_polls=0)
    with pytest.raises(AmplifyDeployError, match="FAILED"):
        AmplifyDeployer("d123", client=client).deploy(PAGES)


def test_deploy_raises_on_timeout(no_sleep, captured_uploads):
    client = FakeAmplify(running_polls=10**6)  # 永遠に RUNNING
    deployer = AmplifyDeployer("d123", client=client, timeout_sec=0.0)
    with pytest.raises(AmplifyDeployError, match="秒で終わりませんでした"):
        deployer.deploy(PAGES)


def test_empty_site_is_refused():
    """空の zip を送るとサイト全体が消えるため、デプロイさせない."""
    client = FakeAmplify()
    with pytest.raises(AmplifyDeployError, match="ページがありません"):
        AmplifyDeployer("d123", client=client).deploy({})
    assert client.deployments == []


def test_skipping_the_wait_returns_without_polling(captured_uploads):
    client = FakeAmplify()
    result = AmplifyDeployer("d123", client=client, wait=False).deploy(PAGES)
    assert result.status == "RUNNING"
    assert client._polls == {}


def test_default_url():
    client = FakeAmplify()
    url = AmplifyDeployer("d123", client=client).default_url()
    assert url == "https://main.d123.amplifyapp.com"
