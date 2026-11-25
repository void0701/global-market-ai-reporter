"""設定の読み込み経路のテスト.

主目的は **Lambda の展開レイアウトで `load_settings()` が動くこと**の回帰防止。
かつて `_project_root()` がリポジトリ構成からの相対パス（`parents[2]`）で TOML を
探していたため、`/var/task/market_report/` に展開される Lambda では確実に
`FileNotFoundError` になった（docs/13 §8 R-1 / A-149）。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

from market_report.config import (
    _packaged_config_dir,
    _writable_base,
    in_lambda,
    load_settings,
)

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "src" / "market_report"


def test_toml_is_packaged_alongside_the_code():
    cdir = _packaged_config_dir()
    assert cdir.is_dir()
    assert (cdir / "settings.toml").is_file()
    assert (cdir / "feeds.toml").is_file()
    # パッケージ配下にあること。リポジトリのレイアウトに依存していない証拠
    assert PACKAGE_DIR in cdir.parents or cdir.parent == PACKAGE_DIR


def test_load_settings_reads_the_packaged_toml(tmp_path):
    settings = load_settings(output_dir=tmp_path / "out")
    assert settings.feeds, "feeds.toml が読めていない"
    assert settings.symbols, "settings.toml の銘柄が読めていない"


def test_config_dir_can_be_overridden_by_env(tmp_path, monkeypatch):
    custom = tmp_path / "cfg"
    custom.mkdir()
    src = _packaged_config_dir()
    shutil.copy(src / "settings.toml", custom / "settings.toml")
    shutil.copy(src / "feeds.toml", custom / "feeds.toml")
    monkeypatch.setenv("MARKET_REPORT_CONFIG_DIR", str(custom))
    assert load_settings(output_dir=tmp_path / "out").feeds


def test_in_lambda_follows_the_runtime_env(monkeypatch):
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    assert in_lambda() is False
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "s-and-p-prod-daily")
    assert in_lambda() is True


def test_relative_output_dir_lands_in_tmp_on_lambda(monkeypatch):
    """Lambda は ``/tmp`` 以外が読み取り専用なので、相対指定はそこへ逃がす."""
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "s-and-p-prod-daily")
    assert _writable_base() == Path("/tmp")  # noqa: S108
    settings = load_settings(output_dir="output")
    assert settings.output_dir.is_absolute()
    # macOS では /tmp が /private/tmp に解決されるため resolve() 後の値で比較する
    assert settings.output_dir == (Path("/tmp") / "output").resolve()  # noqa: S108


def test_load_settings_works_in_the_lambda_layout(tmp_path):
    """``/var/task/market_report/`` 相当に展開して別プロセスで読み込む.

    リポジトリのファイル（``pyproject.toml`` など）が一切無い状態を再現する。
    これが R-1 の回帰テスト本体。
    """
    task_root = tmp_path / "task"
    task_root.mkdir()
    shutil.copytree(
        PACKAGE_DIR,
        task_root / "market_report",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )

    script = textwrap.dedent("""
        import sys
        sys.path.insert(0, ".")
        from market_report.config import load_settings
        s = load_settings()
        print(len(s.feeds), len(s.symbols), s.provider)
    """)
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script],
        cwd=task_root,
        capture_output=True,
        text=True,
        env={"AWS_LAMBDA_FUNCTION_NAME": "s-and-p-prod-daily", "PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 0, f"Lambda レイアウトで失敗:\n{proc.stderr}"
    feeds, symbols, provider = proc.stdout.split()
    assert int(feeds) > 0
    assert int(symbols) > 0
    assert provider == "anthropic"
