#!/usr/bin/env python3
"""Bedrock 診断 v4 — モデルアクセスの付与状況を AWS に直接問い合わせる.

これまでの経過:
  ① モデルは ap-southeast-2 に存在する（コントロールプレーンで確認）
  ② au. / global. の推論プロファイルが ACTIVE
  ③ mantle: 接頭辞あり/なし いずれも 404 "does not exist"
  ④⑤⑥ bedrock-runtime: Converse も InvokeModel も 400 "Operation not allowed"
  ※ 認証は通っている（401/403 ではない）。コントロールプレーンの読み取りも成功

残る可能性は「モデルアクセスが実際には未付与」か「SCP による制限」。
GetFoundationModelAvailability はまさにその状態を返すので、推測せずに確定させる。
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

REGION = os.environ.get("AWS_REGION") or "ap-southeast-2"
TOKEN = os.environ.get("AWS_BEARER_TOKEN_BEDROCK")

TARGETS = [
    "anthropic.claude-opus-5",
    "anthropic.claude-sonnet-5",
    "anthropic.claude-haiku-4-5-20251001-v1:0",
]


def _get(path: str):
    url = f"https://bedrock.{REGION}.amazonaws.com{path}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:  # noqa: S310
            return resp.status, resp.read().decode(errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")
    except Exception as exc:  # noqa: BLE001
        return 0, f"{type(exc).__name__}: {exc}"


def main() -> int:
    if not TOKEN:
        print("AWS_BEARER_TOKEN_BEDROCK が未設定です。")
        return 1
    print(f"リージョン: {REGION}\n")

    print("=== ⑦ モデルアクセスの付与状況（GetFoundationModelAvailability）===")
    verdicts = []
    for model in TARGETS:
        status, body = _get(f"/foundation-model-availability/{urllib.parse.quote(model, safe='')}")
        print(f"\n  [{status}] {model}")
        if status == 200:
            try:
                d = json.loads(body)
            except json.JSONDecodeError:
                print(f"        {body[:200]}")
                continue
            for key in (
                "agreementAvailability",
                "authorizationStatus",
                "entitlementAvailability",
                "regionAvailability",
            ):
                val = d.get(key)
                if isinstance(val, dict):
                    val = val.get("status") or val
                print(f"        {key:<26} {val}")
            verdicts.append((model, d))
        else:
            msg = body
            with contextlib.suppress(json.JSONDecodeError, AttributeError):
                msg = json.loads(body).get("message", body)
            print(f"        {' '.join(str(msg).split())[:220]}")

    print("\n=== 判定 ===")
    if not verdicts:
        print("  この API も呼べませんでした。")
        print("  → ベアラートークンの権限、または SCP による制限の可能性。")
        return 0

    for model, d in verdicts:
        ent = d.get("entitlementAvailability")
        auth = d.get("authorizationStatus")
        if ent == "AVAILABLE" and auth == "AUTHORIZED":
            print(f"  ✅ {model}: アクセス付与済み")
            print("     → 付与済みなのに呼べない = SCP による制限が濃厚")
        else:
            print(f"  ❌ {model}: entitlement={ent} / authorization={auth}")
            print("     → モデルアクセスが未付与。Bedrock コンソールで有効化してください")
    return 0


if __name__ == "__main__":
    sys.exit(main())
