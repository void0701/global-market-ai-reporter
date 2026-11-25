"""S3Publisher（AWS を呼ばずにスタブで検証）."""

from __future__ import annotations

import json

from fake_aws import FakeS3

from market_report.publishing.s3 import S3Publisher


def _pub(**kwargs):
    client = FakeS3(**kwargs)
    return S3Publisher("bkt", client=client), client


def test_put_and_get_roundtrip():
    pub, _ = _pub()
    location = pub.put_text("data/daily/2026-11-25.json", '{"a":1}', "application/json")
    assert location == "s3://bkt/data/daily/2026-11-25.json"
    assert pub.get_text("data/daily/2026-11-25.json") == '{"a":1}'


def test_missing_key_returns_none_not_raise():
    """``NoSuchKey`` は ``None``。botocore の例外クラスに依存せず判定する."""
    pub, _ = _pub()
    assert pub.get_text("nope.json") is None
    assert pub.exists("nope.json") is False


def test_non_ascii_survives_the_roundtrip():
    pub, _ = _pub()
    body = json.dumps({"見出し": "日経平均が急騰"}, ensure_ascii=False)
    pub.put_text("data/x.json", body, "application/json")
    assert pub.get_text("data/x.json") == body


def test_list_keys_pages_through_truncated_results():
    """1000 件超でも全件返す（運用 4 年目に ``raw/`` が 1000 を超える）."""
    pub, client = _pub(page_size=10)
    for i in range(25):
        pub.put_text(f"raw/news/2026-{i:02d}.json", "{}", "application/json")
    pub.put_text("reports/x.html", "<p/>")
    keys = pub.list_keys("raw/news")
    assert len(keys) == 25
    assert keys == sorted(keys)
    assert all(k.startswith("raw/news/") for k in keys)


def test_get_many_reads_every_key_including_missing_ones():
    pub, client = _pub()
    pub.put_text("a.json", "1", "application/json")
    pub.put_text("b.json", "2", "application/json")
    got = pub.get_many(["a.json", "b.json", "missing.json"])
    assert got == {"a.json": "1", "b.json": "2", "missing.json": None}
    assert client.get_calls == 3


def test_get_many_with_no_keys_makes_no_calls():
    pub, client = _pub()
    assert pub.get_many([]) == {}
    assert client.get_calls == 0
