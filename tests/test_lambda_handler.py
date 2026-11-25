"""Lambda ハンドラ（AWS を一切呼ばずに検証する）.

docs/13 §6 の「1〜4 は AWS なしで作れる」を実際に成立させるためのテスト。
S3 / Amplify / Secrets Manager / SNS はすべて ``tests/fake_aws.py`` のスタブ。
"""

from __future__ import annotations

import json
import os

import pytest
from fake_aws import FakeAmplify, FakeS3, FakeSecrets, FakeSns

from market_report import lambda_handler as lh
from market_report.publishing.amplify import AmplifyDeployer
from market_report.publishing.s3 import S3Publisher


@pytest.fixture
def aws(monkeypatch):
    """ハンドラが掴む各クライアントをスタブに差し替える."""
    s3 = FakeS3()
    amplify = FakeAmplify(running_polls=0)
    sns = FakeSns()
    secrets = FakeSecrets({"s-and-p/anthropic": "sk-ant-test-value"})

    monkeypatch.setattr(lh, "_publisher", lambda bucket, settings: S3Publisher("bkt", client=s3))
    monkeypatch.setattr(lh, "_deployer", lambda: AmplifyDeployer("d123", client=amplify, wait=True))
    monkeypatch.setattr(lh.Notifier, "client", property(lambda self: sns))
    monkeypatch.setattr(lh, "_load_api_key", lambda secret_id, client=None: None)
    monkeypatch.setenv("REPORT_BUCKET", "bkt")
    monkeypatch.setenv("AMPLIFY_APP_ID", "d123")
    monkeypatch.setenv("ALERT_TOPIC_ARN", "arn:aws:sns:ap-southeast-2:1:alerts")
    monkeypatch.setattr(lh, "_secret_loaded", False)

    class Bundle:
        pass

    bundle = Bundle()
    bundle.s3, bundle.amplify, bundle.sns, bundle.secrets = s3, amplify, sns, secrets
    return bundle


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    from market_report.publishing import amplify as amplify_mod

    monkeypatch.setattr(amplify_mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(amplify_mod, "_put", lambda url, payload: None)


# ---------------------------------------------------------------- 正常系


def test_daily_writes_three_layers_to_s3_and_deploys(aws, fake_rss, fake_market):
    out = lh.handler({"report_type": "daily", "date": "2026-11-21", "dry_run": True})

    assert out["ok"] is True
    assert out["report"] == "daily"
    assert out["date"] == "2026-11-21"

    keys = set(aws.s3.objects)
    assert "raw/news/2026-11-21.json" in keys
    assert "raw/market/2026-11-21.json" in keys
    assert "raw/llm/2026-11-21-daily.json" in keys
    assert "data/daily/2026-11-21.json" in keys
    assert "reports/2026-11-21.html" in keys
    assert "index.html" in keys and "archive.html" in keys

    assert out["site"]["deployed"] is True
    assert out["site"]["status"] == "SUCCEED"
    assert aws.amplify.started == ["1"]


def test_deployed_site_contains_every_report_page(aws, fake_rss, fake_market):
    """Amplify は全置き換えなので、過去分も毎回含まれていないといけない（R-3）."""
    for day in ("2026-11-20", "2026-11-21"):
        lh.handler({"report_type": "daily", "date": day, "dry_run": True})

    pages = lh.pipeline.build_site(lh.load_settings(), S3Publisher("bkt", client=aws.s3))
    assert "reports/2026-11-20.html" in pages
    assert "reports/2026-11-21.html" in pages
    assert "index.html" in pages
    assert "archive.html" in pages


def test_report_shows_the_recorded_timestamp_not_render_time(aws, fake_rss, fake_market):
    """A-142。毎朝描き直すので、レンダリング時刻を出すと過去分が今日の日時になる."""
    lh.handler({"report_type": "daily", "date": "2026-11-21", "dry_run": True})
    record = json.loads(aws.s3.objects["data/daily/2026-11-21.json"].decode("utf-8"))
    generated = record["generated_at"][:10]

    html = aws.s3.objects["reports/2026-11-21.html"].decode("utf-8")
    assert f"生成: {generated}" in html


def test_weekly_with_too_little_data_is_not_an_error(aws, fake_rss, fake_market):
    """A-80。運用開始直後は正常終了させる."""
    out = lh.handler({"report_type": "weekly", "period": "2026-W47"})
    assert out["ok"] is True
    assert "skipped" in out
    assert aws.sns.published == []


# ---------------------------------------------------------------- 異常系


def test_failure_notifies_sns_and_reraises(aws):
    """通知した上で再送出する。Errors メトリクスを立てるため（A-148）."""
    with pytest.raises(ValueError, match="未知の report_type"):
        lh.handler({"report_type": "quarterly"})

    assert len(aws.sns.published) == 1
    note = aws.sns.published[0]
    assert "失敗" in note["subject"]
    assert "未知の report_type" in note["message"]
    assert "ValueError" in note["message"]


def test_empty_site_is_not_deployed(aws, monkeypatch):
    """ページ 0 件で配信すると既存サイトを消してしまう."""
    monkeypatch.setattr(lh.pipeline, "build_site", lambda settings, pub: {})
    result = lh._publish_site(lh.load_settings(), S3Publisher("bkt", client=aws.s3))
    assert result["deployed"] is False
    assert aws.amplify.deployments == []


def test_missing_amplify_app_id_skips_delivery_without_failing(aws, monkeypatch):
    """dev で Amplify を置かない場合でも、日次の生成自体は成功させる."""
    monkeypatch.delenv("AMPLIFY_APP_ID", raising=False)
    monkeypatch.undo()  # aws fixture の _deployer 差し替えを戻し、本物の分岐を通す
    monkeypatch.delenv("AMPLIFY_APP_ID", raising=False)

    assert lh._deployer() is None


# ---------------------------------------------------------------- シークレット


def test_api_key_comes_from_secrets_manager_not_env(monkeypatch):
    """Lambda の環境変数にキーを置かない設計（docs/06 §3-0）."""
    monkeypatch.delenv(lh.API_KEY_ENV, raising=False)
    monkeypatch.setattr(lh, "_secret_loaded", False)
    secrets = FakeSecrets({"s-and-p/anthropic": "  sk-ant-from-secrets  "})

    lh._load_api_key("s-and-p/anthropic", client=secrets)

    assert secrets.calls == 1
    assert os.environ[lh.API_KEY_ENV] == "sk-ant-from-secrets"


def test_secret_is_fetched_once_per_execution_environment(monkeypatch):
    monkeypatch.delenv(lh.API_KEY_ENV, raising=False)
    monkeypatch.setattr(lh, "_secret_loaded", False)
    secrets = FakeSecrets({"sid": "value"})

    lh._load_api_key("sid", client=secrets)
    lh._load_api_key("sid", client=secrets)

    assert secrets.calls == 1


def test_existing_env_key_is_respected(monkeypatch):
    """ローカル実行やテストで既に入っている値を上書きしない."""
    monkeypatch.setenv(lh.API_KEY_ENV, "sk-ant-local")
    monkeypatch.setattr(lh, "_secret_loaded", False)
    secrets = FakeSecrets({"sid": "sk-ant-remote"})

    lh._load_api_key("sid", client=secrets)

    assert secrets.calls == 0
    assert os.environ[lh.API_KEY_ENV] == "sk-ant-local"


def test_empty_secret_is_rejected(monkeypatch):
    monkeypatch.delenv(lh.API_KEY_ENV, raising=False)
    monkeypatch.setattr(lh, "_secret_loaded", False)

    class Empty:
        def get_secret_value(self, *, SecretId):  # noqa: N803
            return {"SecretString": ""}

    with pytest.raises(RuntimeError, match="シークレットが空です"):
        lh._load_api_key("sid", client=Empty())


# ------------------------------------------------- build_site の呼び出し回数


def test_site_is_built_only_once_per_invocation(aws, fake_rss, fake_market, monkeypatch):
    """``build_site`` は全レコードを読むので、1 回の実行で 2 回呼ばない（A-160）."""
    calls = []
    original = lh.pipeline.build_site

    def counting(settings, pub):
        calls.append(1)
        return original(settings, pub)

    monkeypatch.setattr(lh.pipeline, "build_site", counting)
    lh.handler({"report_type": "daily", "date": "2026-11-21", "dry_run": True})

    assert len(calls) == 1


def test_root_pages_still_reach_s3(aws, fake_rss, fake_market):
    """index を配信側に寄せても、S3 のトップページは更新され続ける."""
    lh.handler({"report_type": "daily", "date": "2026-11-21", "dry_run": True})
    for key in ("index.html", "archive.html"):
        assert key in aws.s3.objects, key
