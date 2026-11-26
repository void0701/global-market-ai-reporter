# 14. 運用手順（ランブック）

最終更新: 2026-11-26

**日々レポートを出すための手順書。** 設計の理由は他のドキュメントにあるので、
ここは「何を叩くか」に絞っています。

> 現在は **Phase 1（ローカル実行）** の運用です。Phase 2 の AWS 構成は
> コードが揃っていますが `terraform apply` が済んでいないため、
> **毎朝の実行は手動** です（[13](13-phase2-plan.md)）。

---

## 1. 毎朝これだけ

```bash
cd /Users/ken/work/s_and_p
.venv/bin/market-report run
open output/index.html
```

| | |
|---|---|
| 所要 | **約 2〜3 分**（うち LLM が 130〜155 秒） |
| 費用 | **約 $0.24〜0.28**（[03 §0](03-cost-and-tokens.md)） |
| 出力 | `output/reports/YYYY-MM-DD.html` と `index.html` / `archive.html` |

実行すると最後に X 投稿文が表示されます。Phase 3 まで自動投稿はしないので、
使う場合は手でコピーしてください。

### いつ実行するか

**07:00 JST 以降**であれば、その日のレポートが前営業日の米国市場を対象にします。

```
07:00 JST = 前日 17:00 EST / 18:00 EDT  → 米国の引け（16:00 ET）を含む
```

| 曜日 | 実行 | 対象 |
|---|---|---|
| 火〜土 | **日次** | 前営業日の米国セッション |
| 月 | **週次**（日次は出さない） | 前週 |
| 日 | なし | 米国のセッションが無い |

日曜・月曜の朝は米国の取引セッションを 1 つも含まないため、日次を出しても
空振りになります（[09 §2](09-periodic-reports.md) / A-137）。

---

## 2. 週次・月次・年次

```bash
# 月曜の朝に前週分
.venv/bin/market-report run --type weekly

# ラベルを明示する場合
.venv/bin/market-report run --type weekly  --period 2026-W48
.venv/bin/market-report run --type monthly --period 2026-11
.venv/bin/market-report run --type yearly  --period 2026
```

日次データが足りないときは **エラーにならずスキップ**します（A-80）。

```
スキップ: 2026-W47（2026-11-16 〜 2026-11-22）の日次データが 0 件です。
```

運用開始直後はこれが正常な状態です。

---

## 3. 認証

`provider = anthropic`（既定）では **Claude API のキー**を使います。

```bash
# どちらか一方。ant CLI でログイン済みならキーは不要
export ANTHROPIC_API_KEY='sk-ant-...'
# または
ant auth login
```

`.env` に書く場合はリポジトリ直下に置いてください（`.gitignore` 済み）。

### 通るか確認する

```bash
.venv/bin/market-report doctor
```

候補モデルまで実際に判定します（極小リクエストを 1 回送るので費用はほぼゼロ）。
外部に出さずに設定だけ見たいときは `--quick` を付けます。

---

## 4. 課金なしで試す

| コマンド | 内容 |
|---|---|
| `run --dry-run` | **LLM を呼ばず**擬似データで全段を通す。テンプレート調整やデバッグ用 |
| `collect` | 収集と重複排除だけ実行して結果を表示 |
| `market --all` | 市況 40 系列を取得して表示 |
| `recluster --date D --threshold 0.7` | 保存済み記事を別の閾値で再クラスタリング |
| `reindex` | `index` / `archive` を作り直す |

```bash
# 本物のデータを壊さずに試す
.venv/bin/market-report --output-dir /tmp/try run --dry-run
```

---

## 5. 後から解析をやり直す

生データを全件保存しているため（[11](11-data-retention.md)）、**同じ入力に対して
別のモデル・別のプロンプトで解析し直せます**。

```bash
# 費用の見積もりだけ
.venv/bin/market-report reanalyze --from 2026-11-01 --to 2026-11-30 --dry-run-cost

# 実行
.venv/bin/market-report reanalyze --from 2026-11-01 --to 2026-11-30
```

モデルを変えて品質を比べる場合:

```bash
.venv/bin/market-report run --model claude-sonnet-5
```

---

## 6. 困ったとき

### フィードが古いと警告が出る

```
WARNING フィードが古い（停止している可能性）
        feed=Federal Reserve latest_age_hours=86.8 threshold_hours=48
```

**週末や祝日では正常です。** Fed や一部の媒体は営業日しか配信しません。
平日に連続して出る場合はフィードの停止を疑ってください
（[08](08-rss-feeds.md) に各フィードの状態があります）。

### 全フィードが空

```
AllFeedsEmptyError
```

ネットワークか、収集ウィンドウの設定を確認してください。この場合は
レポートを生成せずに終了します（前日分の `index.html` を壊しません / A-54）。

### 認証エラー

```bash
.venv/bin/market-report doctor
```

`ANTHROPIC_API_KEY` も `ant auth login` も無い状態だとここで分かります。

### 市況の日付がずれているように見える

**2559.T（オルカン ETF・円建て）は 1 営業日遅れます。** 東証の引けは
15:30 JST なので、07:00 JST 時点で取得できるのは前営業日の値です。
レポートには理論値（`jpy_theoretical_change_pct`）も併記してあり、
そちらが当日の米国セッションに対応します（[12](12-global-index.md)）。

---

## 7. 設定を変える

設定は **パッケージに同梱**されています（A-149）。

```
src/market_report/resources/settings.toml   モデル・閾値・銘柄 40 系列
src/market_report/resources/feeds.toml      RSS フィード 11 本
```

優先順は **CLI オプション > 環境変数 > TOML** です。

| 環境変数 | 用途 |
|---|---|
| `MARKET_REPORT_PROVIDER` | `anthropic` / `bedrock` |
| `BEDROCK_MODEL_ID` | モデル ID（名前は歴史的経緯。Claude API でも読む） |
| `MARKET_REPORT_OUTPUT_DIR` | 出力先 |
| `MARKET_REPORT_CONFIG_DIR` | TOML を別の場所から読む |

別の設定で試したいだけなら、TOML を書き換えずに済みます。

```bash
cp -R src/market_report/resources /tmp/cfg
# /tmp/cfg/settings.toml を編集
MARKET_REPORT_CONFIG_DIR=/tmp/cfg .venv/bin/market-report run --dry-run
```

---

## 8. 開発時の確認

```bash
.venv/bin/ruff check src tests
.venv/bin/ruff format src tests
.venv/bin/mypy src tests
.venv/bin/pytest -q
TZ=UTC .venv/bin/pytest -q     # ★ CI と同じタイムゾーンで
./scripts/build_lambda.sh      # Lambda zip のビルド（50MB 超過で失敗する）
```

CI（[`.github/workflows/ci.yml`](../.github/workflows/ci.yml)）と同じ内容です。
push する前にこれが全部通っていれば、CI も通ります。

> ### ⚠️ `TZ=UTC` を忘れないでください
>
> **開発機は JST、GitHub のランナーは UTC です。** タイムゾーン依存のバグは
> 片方でしか再現しません。実際に、レコードの `generated_at`（実行環境の
> ローカル時刻）と JST 表示を文字列で比較していたテストが、ローカルでは
> 通るのに CI で落ちました。CI 側でも両方のタイムゾーンで pytest を回して
> いますが、手元で先に気づけるほうが速いです。

---

## 9. Phase 2 以降（自動化後）

`terraform apply` が済むと、EventBridge Scheduler が上記 §1〜2 を自動実行します。
手順は [13](13-phase2-plan.md)、設計は [06](06-cicd-and-environments.md) にあります。

手動で動かしたいときは Lambda を直接呼びます。

```bash
aws lambda invoke \
  --function-name s-and-p-prod-report \
  --payload '{"report_type":"daily"}' \
  --cli-binary-format raw-in-base64-out \
  /dev/stdout
```

`{"report_type":"daily","dry_run":true}` にすると LLM を呼ばずに配線だけ確認できます。
