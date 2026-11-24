# 米国・世界ニュース AI 解析＆自動レポート配信パイプライン

毎朝、米国および世界の経済ニュースと主要市況を自動収集し、Amazon Bedrock で
**S&P500** と **円建て全世界株式（オルカン）** への影響を解析して、Web レポートを
生成します。

**現在 Phase 1（ローカル実行）が完成しています。** 設計の全体像と判断の経緯は
[`docs/`](docs/README.md) を参照してください。

| Phase | 内容 | 状態 |
|---|---|---|
| 1 | ローカル実行可能なコアパイプライン | ✅ **完了** |
| 2 | AWS インフラ構築と Lambda 化（Terraform） | 未着手 |
| 3 | X API v2 連携 | 未着手 |

---

## クイックスタート

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# AWS 不要・課金なしで全段を検証
.venv/bin/market-report run --dry-run
open output/index.html
```

実際に解析するには AWS の認証情報と Bedrock のモデルアクセスが必要です
（[docs/07-prerequisites.md](docs/07-prerequisites.md)）。設定できたかは `doctor` で検証できます。

```bash
export AWS_PROFILE=<プロファイル名>
.venv/bin/market-report doctor   # 使えるモデルまで判定する
.venv/bin/market-report run
```

---

## コマンド

| コマンド | 課金 | 内容 |
|---|---|---|
| `doctor` | ほぼなし | **セットアップ検証**（使えるモデルまで判定。`--quick` で AWS 省略） |
| `run --dry-run` | **なし** | Bedrock を呼ばず擬似データで全段を検証 |
| `run` | あり | 日次レポートを生成 |
| `run --type weekly --period 2026-W47` | あり | 週次レポート（月次・年次も同様） |
| `collect` | **なし** | 収集と重複排除だけ実行して結果を表示 |
| `market --all` | **なし** | 市況 40 系列を取得して表示 |
| `recluster --date D --threshold 0.7` | **なし** | 保存済み記事を別パラメータで再クラスタリング |
| `reanalyze --from A --to B` | あり | 保存済み記事を別スキーマ・別プロンプトで再解析 |
| `reindex` | **なし** | index / archive を再生成 |

設定の優先順は **CLI オプション > 環境変数 > `config/*.toml`**。

```bash
# モデルを差し替えて品質を比較する
.venv/bin/market-report run --model anthropic.claude-sonnet-5

# 再解析のコストだけ見積もる
.venv/bin/market-report reanalyze --from 2026-11-01 --to 2026-11-30 --dry-run-cost
```

---

## 何をするのか

```
RSS 11本 ─┬─► 全件保存（重複排除前）        ← トークン 0
          └─► 重複排除 ─► 上位15件 ─┐
                                    ├─► Bedrock（構造化出力）─► 解析JSON ─► HTML
市況 40系列 ┬─► 全系列保存 ────────┘                              │
            └─► 主要15系列 ──────┘                                │
                                                                  ▼
                                        週次 ← 日次 / 月次 ← 週次 / 年次 ← 月次
```

- **保存は最大限・AI への入力は選別**（[docs/11](docs/11-data-retention.md)）。
  保存はストレージ費だけで年 $0.02、入力を増やすと分析の焦点がぼやけるため。
- **記事を全件保存するので後から再解析できる**。スキーマやプロンプトを変えて
  `reanalyze` すれば、過去分にも新しい分析軸を適用できます。
- **期間レポートは階層的ロールアップ**。年次で日次 250 本を 1 回の呼び出しに
  入れるのは現実的でないため、1 つ下の階層だけを入力にします。
- **統計値は AI に計算させない**。センチメント平均・騰落率・的中率は決定的に
  集計し、その結果を AI への入力に含めます。

---

## ディレクトリ

```
config/           feeds.toml（RSS 11本）/ settings.toml（市況40系列・閾値）
src/market_report/
  collectors/     rss.py（鮮度チェック付き）/ market.py（Provider 抽象）
  processing/     normalize.py / dedupe.py（標準ライブラリのみ）
  analysis/       client.py / prompts.py / analyzer.py / aggregate.py / scoring.py
  rendering/      html.py + templates/（Jinja2・JavaScript 不要）
  publishing/     base.py（Protocol）/ local.py / store.py
  pipeline.py     オーケストレーション（AWS 非依存。Phase 2 の Lambda が呼ぶ）
  cli.py
output/           下記 3 層（.gitignore）
tests/            44 件
docs/             設計ドキュメント 13 本
```

### 出力の 3 層データモデル

```
output/
├── raw/        生データ（AI に渡さないものも全件）
│   ├── news/      RSS 全記事 + 重複排除の判定結果
│   ├── market/    40 系列の OHLCV
│   └── llm/       プロンプト全文・推論要約・usage
├── data/       解析結果（構造化・期間レポートの入力）
│   ├── daily/ weekly/ monthly/ yearly/
├── reports/ weekly/ monthly/ yearly/   日付別 HTML
└── index.html / weekly.html / monthly.html / archive.html
```

---

## 設計上の要点

| 項目 | 内容 |
|---|---|
| Bedrock | `bedrock-mantle`（Messages API 互換）。旧 `InvokeModel` は使わない |
| モデル | `anthropic.claude-opus-5`（Terraform 変数で差し替え可能な設計） |
| ストリーミング | 必須。非ストリーミングは `max_tokens` 21,333 超で SDK が拒否する |
| 推論要約 | `thinking.display="summarized"` で保存。**課金は display 設定によらず同一** |
| 市況の基準日 | **レポート日の前日**（07:00 JST = 前日 17:00 ET） |
| 系列の日付 | 全系列を米国株セッションに揃える（為替は24時間取引のため） |
| 重複排除 | Jaccard + difflib。媒体あたりの選抜上限で多様性を担保 |
| X 投稿 | プレフィックスをコード側で付与し、140 全角をコードで判定 |

詳細と判断の理由は [`docs/05-decisions.md`](docs/05-decisions.md)（確定事項 105 件）。

---

## 開発

```bash
.venv/bin/python -m pytest tests/ -q      # 44 件（Bedrock はモック・課金なし）
.venv/bin/ruff check src tests
.venv/bin/ruff format src tests
```

---

## 注意

本システムが生成するレポートは AI による自動生成であり、**投資判断の根拠として
用いるものではありません**。記事の原文は転載せず、要約・分析のみを掲載します。

的中率を表示しますが、**ベンチマーク（常に上昇/下落と判定した場合）との差
（edge）でしか解釈できません**。S&P500 には長期的な上昇バイアスがあるため、
「常に上昇」だけで 50% を超えます。
