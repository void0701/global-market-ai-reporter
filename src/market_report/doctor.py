"""セットアップ検証（``market-report doctor``）.

AWS の設定は「どこで間違えたか」が分かりにくいため、1 コマンドで切り分けられる
ようにする。特に **Bedrock のモデルアクセスはリージョンごと**に有効化が必要で、
別リージョンで有効化しても呼び出しは失敗する、という事故が起きやすい。
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .collectors.rss import USER_AGENT as RSS_USER_AGENT
from .config import Settings

CRED_HINT = "provider=anthropic なら ANTHROPIC_API_KEY、bedrock なら AWS_BEARER_TOKEN_BEDROCK"

OK = "✅"
NG = "❌"
WARN = "⚠️ "
SKIP = "⏭ "

CANDIDATE_MODELS = [
    ("claude-opus-5", "推奨（本番で使う）"),
    ("claude-opus-5-5", "より新しい世代（使えれば候補）"),
    ("claude-sonnet-5", "代替（安価）"),
    ("claude-haiku-4-5", "最安"),
]


def _for_provider(model: str, provider: str) -> str:
    return f"anthropic.{model}" if provider == "bedrock" else model


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    hint: str = ""


def _fmt(check: Check) -> str:
    line = f"  {check.status} {check.name}"
    if check.detail:
        line += f"\n       {check.detail}"
    if check.hint:
        line += f"\n       → {check.hint}"
    return line


def check_local(settings: Settings) -> list[Check]:
    checks: list[Check] = []
    import sys

    checks.append(Check("Python", OK, f"{sys.version.split()[0]}（Lambda ターゲットは 3.13）"))
    try:
        import anthropic

        has_mantle = hasattr(anthropic, "AnthropicBedrockMantle")
        checks.append(
            Check(
                "anthropic SDK",
                OK if has_mantle else NG,
                f"{anthropic.__version__} / AnthropicBedrockMantle={has_mantle}",
                "" if has_mantle else "pip install -U 'anthropic[bedrock]'",
            )
        )
    except ImportError:
        checks.append(Check("anthropic SDK", NG, "未インストール", "pip install -e ."))

    out = Path(settings.output_dir)
    try:
        out.mkdir(parents=True, exist_ok=True)
        probe = out / ".doctor"
        probe.write_text("ok")
        probe.unlink()
        checks.append(Check("出力ディレクトリ", OK, str(out)))
    except OSError as exc:
        checks.append(Check("出力ディレクトリ", NG, f"{out}: {exc}"))
    return checks


def check_credentials(settings: Settings) -> list[Check]:
    checks: list[Check] = []
    checks.append(
        Check(
            "プロバイダ",
            OK,
            "Claude API を直接呼び出し" if settings.provider == "anthropic" else "Amazon Bedrock",
            f"provider = {settings.provider}"
            "（settings.toml / MARKET_REPORT_PROVIDER / --provider で変更）",
        )
    )
    if settings.provider == "anthropic":
        has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
        # ant CLI でログイン済みなら ~/.config/anthropic/ にプロファイルがあり、
        # SDK はそれを自動で読む（API キーは不要）
        cli_profile = Path.home() / ".config" / "anthropic"
        has_cli = cli_profile.exists() and any(cli_profile.iterdir())
        if has_key:
            detail, hint = "ANTHROPIC_API_KEY", ""
        elif has_cli:
            detail, hint = f"ant CLI のプロファイル（{cli_profile}）", "API キーより安全な経路です"
        else:
            detail, hint = (
                "見つかりません",
                "ant auth login（推奨）または export ANTHROPIC_API_KEY='sk-ant-...' "
                "（docs/07 §A-0-C）",
            )
        checks.append(Check("認証情報", OK if (has_key or has_cli) else NG, detail, hint))
        return checks

    sources = [
        v
        for v in ("AWS_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_BEARER_TOKEN_BEDROCK")
        if os.environ.get(v)
    ]
    shared = Path.home() / ".aws" / "credentials"
    config = Path.home() / ".aws" / "config"
    found = sources + [str(p) for p in (shared, config) if p.exists()]
    checks.append(
        Check(
            "AWS 認証情報の候補",
            OK if found else WARN,
            ", ".join(found) if found else "環境変数も ~/.aws も見つかりません",
            ""
            if found
            else "最短は Bedrock API キー: export AWS_BEARER_TOKEN_BEDROCK=...（docs/07 §A-0-F）",
        )
    )
    if os.environ.get("AWS_BEARER_TOKEN_BEDROCK"):
        # AWS_BEARER_TOKEN_BEDROCK は通常の AWS クレデンシャルチェーンより優先される
        checks.append(
            Check(
                "認証方式",
                OK,
                "Bedrock API キー（ベアラートークン）",
                "通常の AWS 認証情報より優先されます。Bedrock 以外には使えません",
            )
        )
    env_region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    checks.append(
        Check(
            "リージョン",
            OK,
            f"{settings.region}"
            + (f"（環境変数 {env_region} が優先）" if env_region else "（settings.toml）"),
            "Bedrock のモデルアクセスは**このリージョン**で有効化してください",
        )
    )
    return checks


def check_bedrock(settings: Settings, *, quick: bool = False) -> list[Check]:
    """各候補モデルに最小リクエストを投げてアクセス可否を判定する."""
    label = "Claude API" if settings.provider == "anthropic" else "Bedrock"
    if quick:
        return [Check(f"{label} 接続", SKIP, "--quick のため省略")]

    checks: list[Check] = []
    try:
        if settings.provider == "bedrock":
            from anthropic import AnthropicBedrockMantle

            # provider により実型が変わるため Any で受ける（client.py と同じ扱い）
            client: Any = AnthropicBedrockMantle(aws_region=settings.region)
        else:
            from anthropic import Anthropic

            # API キーが無くても ant CLI のプロファイルで動くため、ここでは
            # 事前チェックせず実際に呼んで判定する
            client = Anthropic()
    except Exception as exc:  # noqa: BLE001 - 認証の失敗形は環境により様々
        return [
            Check(
                f"{label} クライアント生成",
                NG,
                f"{type(exc).__name__}: {str(exc)[:160]}",
            )
        ]

    accessible: list[str] = []
    for base, note in CANDIDATE_MODELS:
        model = _for_provider(base, settings.provider)
        try:
            client.messages.create(
                model=model, max_tokens=4, messages=[{"role": "user", "content": "hi"}]
            )
            accessible.append(model)
            checks.append(Check(f"モデル {model}", OK, note))
        except Exception as exc:  # noqa: BLE001
            text = str(exc)
            lowered = text.lower()
            if "credential" in lowered:
                checks.append(Check(f"モデル {model}", NG, "認証情報を解決できません", CRED_HINT))
                break
            if "accessdenied" in lowered.replace(" ", "") or "not authorized" in lowered:
                checks.append(
                    Check(
                        f"モデル {model}",
                        NG,
                        f"アクセス不可（{note}）",
                        f"AWS コンソール > Bedrock（{settings.region}）> モデルアクセス で有効化",
                    )
                )
            elif "not_found" in lowered or "does not exist" in lowered:
                # 認証は通っているがモデルが見えない。モデルアクセス未有効化が最有力
                checks.append(
                    Check(
                        f"モデル {model}",
                        NG,
                        f"このリージョンで見つかりません（{note}）",
                        f"認証は通っています。**{settings.region} で**モデルアクセスを"
                        "有効化してください。状態確認: "
                        "python scripts/probe_bedrock.py（docs/07 §A-0-P）",
                    )
                )
            else:
                checks.append(Check(f"モデル {model}", NG, f"{type(exc).__name__}: {text[:140]}"))

    if accessible:
        current = settings.model_id
        if current in accessible:
            checks.append(Check("設定中のモデル", OK, current))
        else:
            checks.append(
                Check(
                    "設定中のモデル",
                    WARN,
                    f"{current} は使えません",
                    f"--model {accessible[0]} を指定するか settings.toml を変更してください",
                )
            )
    return checks


def check_sources(settings: Settings, *, quick: bool = False) -> list[Check]:
    if quick:
        return [Check("外部データ取得", SKIP, "--quick のため省略")]
    checks: list[Check] = []

    ok_feeds, bad = 0, []
    for feed in settings.feeds:
        try:
            # 収集側と同じ UA を使う。別 UA だと Fed のように 403 を返す配信元があり、
            # 実際には動くのに doctor だけ失敗する偽陽性になる
            req = urllib.request.Request(feed.url, headers={"User-Agent": RSS_USER_AGENT})
            with urllib.request.urlopen(req, timeout=12) as resp:  # noqa: S310
                if 200 <= getattr(resp, "status", 200) < 300:
                    ok_feeds += 1
                else:
                    bad.append(feed.name)
        except (urllib.error.URLError, OSError):
            bad.append(feed.name)
    checks.append(
        Check(
            "RSS フィード",
            OK if not bad else WARN,
            f"{ok_feeds}/{len(settings.feeds)} 本が応答"
            + (f" / 失敗: {', '.join(bad)}" if bad else ""),
            "一部の失敗は許容されます（1本の障害で全体は落ちません）" if bad else "",
        )
    )

    try:
        from .collectors.market import HttpChartProvider, market_asof_for

        rows = HttpChartProvider(timeout=12).history("^GSPC", 5)
        latest = rows[-1] if rows else None
        checks.append(
            Check(
                "市況データ",
                OK if latest else NG,
                f"^GSPC 最新 {latest.date} close={latest.close:,.2f}" if latest else "取得できず",
            )
        )
        checks.append(
            Check(
                "基準日の解決",
                OK,
                f"レポート日 {datetime.now().date()} → 市況 asof "
                f"{market_asof_for(datetime.now().date())}（07:00 JST = 前日 17:00 ET）",
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(Check("市況データ", NG, f"{type(exc).__name__}: {str(exc)[:120]}"))
    return checks


def run(settings: Settings, *, quick: bool = False) -> int:
    groups = [
        ("ローカル環境", check_local(settings)),
        ("認証", check_credentials(settings)),
        (
            "Claude API" if settings.provider == "anthropic" else "Bedrock",
            check_bedrock(settings, quick=quick),
        ),
        ("外部データソース", check_sources(settings, quick=quick)),
    ]
    print("\n=== セットアップ検証 ===")
    failures = 0
    for title, checks in groups:
        print(f"\n[{title}]")
        for check in checks:
            print(_fmt(check))
            if check.status == NG:
                failures += 1

    warnings = sum(1 for _, cs in groups for c in cs if c.status == WARN)
    skipped = sum(1 for _, cs in groups for c in cs if c.status == SKIP)

    print()
    if failures:
        print(f"{NG} {failures} 件の問題があります。上記の → を確認してください。")
    elif warnings:
        print(f"{WARN}{warnings} 件の要確認があります。上記の → を確認してください。")
    elif skipped:
        print(f"{OK} 確認した範囲では問題ありません。")
    else:
        print(f"{OK} すべて問題ありません。`market-report run` を実行できます。")

    if skipped:
        print(f"   {skipped} 項目を省略しました。全項目を確認するには --quick を外してください。")
    if failures or warnings:
        print("   AWS を使わずに動作を見るには: market-report run --dry-run")
    return 0 if failures == 0 else 1
