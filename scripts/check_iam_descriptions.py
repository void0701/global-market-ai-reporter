#!/usr/bin/env python3
"""IAM の `description` が AWS の文字種制約を満たすか検査する.

`terraform plan` はサーバー側の検証を通らないため、**apply して初めて
400 で落ちる**（実際に踏んだ / docs/13 §8 R-13）。CI で先に止める。

IAM の `roleDescriptionType` の pattern は、TAB / LF / CR と
**ASCII 印字可能文字（U+0020〜U+007E）+ Latin-1 補助（U+00A1〜U+00FF）**
のみを許す。日本語は入らない。

botocore のモデルで確認した範囲では、この制約があるのは **IAM だけ**
（KMS / Secrets Manager / CloudWatch / Scheduler / Lambda / Amplify の
description には文字種の制約が無い）。

使い方:
    python3 scripts/check_iam_descriptions.py infra
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# 許される文字集合。正規表現のエスケープを書かずコードポイントで組む
ALLOWED_CHARS = frozenset(
    [chr(0x09), chr(0x0A), chr(0x0D)]
    + [chr(c) for c in range(0x20, 0x7F)]
    + [chr(c) for c in range(0xA1, 0x100)]
)

# `resource "aws_iam_role" "x" { ... }` を拾う。
# IAM で description を持てるのは role / policy / instance_profile など
IAM_RESOURCE = re.compile(r'^resource\s+"(aws_iam_[a-z_]+)"\s+"([^"]+)"\s*\{', re.M)
DESCRIPTION = re.compile(r'^\s*description\s*=\s*"([^"]*)"', re.M)


def _block_after(text: str, start: int) -> str:
    """`{` の直後から対応する `}` までを返す."""
    depth = 1
    i = start
    while i < len(text) and depth:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        i += 1
    return text[start:i]


def check(root: Path) -> list[str]:
    problems: list[str] = []
    for path in sorted(root.rglob("*.tf")):
        text = path.read_text(encoding="utf-8")
        for match in IAM_RESOURCE.finditer(text):
            kind, name = match.group(1), match.group(2)
            block = _block_after(text, match.end())

            for desc in DESCRIPTION.finditer(block):
                value = desc.group(1)
                bad = sorted({c for c in value if c not in ALLOWED_CHARS})
                if not bad:
                    continue
                line = text[: match.end() + desc.start()].count("\n") + 1
                problems.append(
                    f"{path}:{line}  {kind}.{name}\n"
                    f"      IAM が受け付けない文字: {''.join(bad)!r}\n"
                    f"      値: {value}\n"
                    f"      → 英語に直し、日本語の説明は HCL のコメントに残してください"
                )
    return problems


def main() -> int:
    roots = [Path(a) for a in sys.argv[1:]] or [Path("infra")]
    problems: list[str] = []
    for root in roots:
        if not root.exists():
            print(f"対象が存在しません: {root}", file=sys.stderr)
            return 2
        problems.extend(check(root))

    if problems:
        print("IAM の description に使えない文字があります:\n", file=sys.stderr)
        for p in problems:
            print(f"  {p}\n", file=sys.stderr)
        return 1

    print("IAM の description: 問題なし")
    return 0


if __name__ == "__main__":
    sys.exit(main())
