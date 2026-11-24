"""X 投稿の文字数管理（docs/04 §3）."""

from __future__ import annotations

import pytest

from market_report.analysis.analyzer import compose_x_post
from market_report.utils.text import (
    X_WEIGHT_LIMIT,
    fullwidth_len,
    strip_html,
    truncate_chars,
    truncate_weighted,
    x_weighted_len,
)


@pytest.mark.parametrize(
    ("text", "weight"),
    [("あ" * 140, 280), ("a" * 280, 280), ("あa", 3), ("", 0), ("％", 2)],
)
def test_x_weighted_len(text, weight):
    assert x_weighted_len(text) == weight


def test_fullwidth_len_matches_requirement():
    """要件「全角140文字以内」= 重み 280."""
    assert fullwidth_len("あ" * 140) == 140.0
    assert x_weighted_len("あ" * 140) == X_WEIGHT_LIMIT


def test_truncate_weighted_never_exceeds_limit():
    for n in range(0, 400, 7):
        out = truncate_weighted("あ" * n, 100)
        assert x_weighted_len(out) <= 100


def test_truncate_weighted_keeps_short_text_untouched():
    assert truncate_weighted("短い", 100) == "短い"


def test_compose_x_post_fits_limit_even_with_long_body():
    """本文が長すぎてもプレフィックス込みで上限に収まる."""
    prefix = "【11/20 米株 -3.70% / 全世界株(円) -3.29%】"
    out = compose_x_post(prefix, "あ" * 500, X_WEIGHT_LIMIT)
    assert out.startswith(prefix)
    assert x_weighted_len(out) <= X_WEIGHT_LIMIT


def test_compose_x_post_preserves_prefix_when_body_empty():
    prefix = "【11/20 米株 -3.70% / 全世界株(円) -3.29%】"
    assert compose_x_post(prefix, "", X_WEIGHT_LIMIT).strip() == prefix


def test_strip_html_removes_tags_and_entities():
    assert strip_html("<p>A &amp; <b>B</b>\n C</p>") == "A & B C"


def test_truncate_chars():
    assert truncate_chars("あ" * 300, 200).__len__() == 200
    assert truncate_chars("短文", 200) == "短文"
