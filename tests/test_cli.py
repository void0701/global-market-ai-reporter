"""CLI のオプション解決."""

from __future__ import annotations

import pytest

from market_report.cli import build_parser
from market_report.config import load_settings


def _opt(argv, name):
    return getattr(build_parser().parse_args(argv), name, None)


@pytest.mark.parametrize(
    "argv",
    [
        ["--provider", "bedrock", "doctor", "--quick"],
        ["doctor", "--quick", "--provider", "bedrock"],
    ],
)
def test_common_option_works_before_and_after_subcommand(argv):
    """サブコマンドの前に書いた共通オプションが消えないこと.

    argparse の既定値 None はサブパーサ側で上書きされ、前置きの指定が
    黙って無視される。SUPPRESS で回避している。
    """
    assert _opt(argv, "provider") == "bedrock"


@pytest.mark.parametrize(
    "argv",
    [
        ["--model", "claude-sonnet-5", "run", "--dry-run"],
        ["run", "--dry-run", "--model", "claude-sonnet-5"],
    ],
)
def test_model_override_position_independent(argv):
    assert _opt(argv, "model") == "claude-sonnet-5"


def test_unspecified_options_are_absent_not_none():
    args = build_parser().parse_args(["doctor", "--quick"])
    assert not hasattr(args, "provider")
    assert not hasattr(args, "model")


def test_provider_normalizes_model_prefix(tmp_path):
    """provider と model_id の接頭辞の食い違いは 404 の原因になるため揃える."""
    out = tmp_path / "o"
    assert (
        load_settings(
            provider="anthropic", model_id="anthropic.claude-opus-5", output_dir=out
        ).model_id
        == "claude-opus-5"
    )
    assert (
        load_settings(provider="bedrock", model_id="claude-opus-5", output_dir=out).model_id
        == "anthropic.claude-opus-5"
    )


def test_default_provider_is_anthropic(tmp_path):
    assert load_settings(output_dir=tmp_path / "o").provider == "anthropic"
