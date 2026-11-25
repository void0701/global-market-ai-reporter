#!/usr/bin/env bash
#
# Lambda デプロイパッケージ（zip）を作る。
#
# 使い方:
#   scripts/build_lambda.sh [出力先zip]
#
# 設計上の注意（docs/06 §4-5）:
#   - アーキテクチャは x86_64。arm64 は単価が約 20% 安いが Lambda は無料枠内で
#     $0 なので削減額はゼロ。クロスアーキテクチャのホイール取得という
#     複雑さだけが増える。
#   - ランタイムは Python 3.12（A-138）。ローカルと揃える。
#   - 設定 TOML はパッケージに同梱されているため（A-149）、別途コピー不要。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-${ROOT}/build/lambda.zip}"
STAGE="${ROOT}/build/lambda"
PYTHON_VERSION=3.12
PLATFORM=manylinux2014_x86_64

# ビルドに使う python。リポジトリの .venv を優先する。
# macOS の `python3` は 3.9 のことがあり、`--python-version 3.12` を指定しても
# pip 自身の要件判定で弾かれる。
PY_BIN="${PYTHON:-}"
if [ -z "${PY_BIN}" ] && [ -x "${ROOT}/.venv/bin/python" ]; then
  PY_BIN="${ROOT}/.venv/bin/python"
fi
PY_BIN="${PY_BIN:-python3}"
echo "==> pip: $("${PY_BIN}" -V) (${PY_BIN})"

echo "==> クリーンアップ"
rm -rf "${STAGE}" "${OUT}"
mkdir -p "${STAGE}" "$(dirname "${OUT}")"

echo "==> 依存をインストール（${PLATFORM} / py${PYTHON_VERSION}）"
"${PY_BIN}" -m pip install \
  --platform "${PLATFORM}" \
  --only-binary=:all: \
  --python-version "${PYTHON_VERSION}" \
  --target "${STAGE}" \
  --requirement "${ROOT}/requirements-lambda.txt" \
  --quiet

echo "==> アプリケーションコードをコピー"
cp -R "${ROOT}/src/market_report" "${STAGE}/market_report"

echo "==> 不要物を削除"
# テスト・型情報・バイトコードは実行に不要。zip を小さく保つ
find "${STAGE}" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
find "${STAGE}" -type d -name 'tests' -prune -exec rm -rf {} + 2>/dev/null || true
find "${STAGE}" -type d -name '*.dist-info' -prune -exec rm -rf {} + 2>/dev/null || true
find "${STAGE}" -type f -name '*.pyc' -delete 2>/dev/null || true

# 同梱 TOML が消えていないことを確認する（これが無いと起動時に落ちる / R-1）
for f in settings.toml feeds.toml; do
  test -f "${STAGE}/market_report/resources/${f}" || {
    echo "ERROR: ${f} が zip に含まれていません" >&2
    exit 1
  }
done

echo "==> zip を作成"
( cd "${STAGE}" && zip -qr "${OUT}" . -x '*.DS_Store' )

UNPACKED=$(du -sk "${STAGE}" | cut -f1)
ZIPPED=$(du -k "${OUT}" | cut -f1)
echo
echo "==> 完成: ${OUT}"
printf '    展開後 %6.1f MB  (上限 250MB)\n' "$(echo "${UNPACKED} / 1024" | bc -l)"
printf '    zip    %6.1f MB  (直接アップロード上限 50MB / A-139)\n' "$(echo "${ZIPPED} / 1024" | bc -l)"

if [ "${ZIPPED}" -gt 51200 ]; then
  echo "ERROR: zip が 50MB を超えました。S3 経由デプロイへの切り替えが必要です" >&2
  exit 1
fi
