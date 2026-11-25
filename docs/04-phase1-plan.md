# 04. Phase 1 実装計画 ✅ 完了（2026-11-23）

> **実装完了。** Python 27 ファイル 4,114 行 / テンプレート 5 本 / テスト 44 件。
> 実装で判明した事項は [00 §G](00-open-items.md)、追加の確定事項は [05](05-decisions.md) A-106〜A-113。

**Phase 1 のゴール:** RSS・市況データの取得 → 重複排除 → Bedrock による JSON 解析 → HTML レポート生成までを、ローカルの Python スクリプトとして動作確認できる状態にする。

**週次・月次・年次レポート（[09](09-periodic-reports.md)）も Phase 1 で実装する。** 入力が S3 ではなくローカルの日次 JSON になるだけでロジックは同一のため、フィクスチャで完全に検証できる。定期実行（EventBridge Scheduler）だけが Phase 2。

> ⚠️ **日次 JSON のスキーマを Phase 1 で確定させる必要がある。** 期間レポートは過去の日次 JSON を集計するため、後からスキーマを変えるとそれ以前のデータが使えなくなる（[09 §7-2](09-periodic-reports.md)）。

## 1. 実行環境

| 項目 | 内容 |
|---|---|
| ローカル Python | **3.14.2**（確認済） |
| Lambda ターゲット | **Python 3.13** を想定 **[要確認: Phase 2 で Lambda のランタイム提供状況を確認]** |
| 仮想環境 | `venv`（`.venv/`） |

**[注意]** ローカル 3.14 と Lambda 3.13 のバージョン差は、純 Python パッケージのみなら問題にならない。ただし C 拡張を含むパッケージ（pandas / numpy）はターゲット環境向けのホイールが必要になる。これは [01](01-architecture.md) の `MarketDataProvider` 抽象化で回避する設計になっている。

## 2. ライブラリ選定

| 用途 | 採用 | 選定理由 |
|---|---|---|
| Bedrock 呼び出し | **`anthropic[bedrock]`** | `AnthropicBedrockMantle` で Messages API をそのまま使える。構造化出力・Pydantic 検証が標準で効く。boto3 で JSON を手組みする必要がない |
| RSS 取得 | **`feedparser`** | 純 Python。Lambda zip に素で入る。デファクト標準 |
| 市況データ | **`yfinance`**（抽象化の裏に隠す） | 指示どおり採用。ただし Phase 2 で軽量実装に差し替え可能にする |
| 重複排除 | **標準ライブラリのみ**（`difflib` + トークン集合 Jaccard） | 100〜200 件規模なら十分高速。`rapidfuzz` は C 拡張のため Lambda 配布が面倒になる。依存を増やす価値がない |
| HTML 生成 | **`jinja2`** | 純 Python。テンプレートをコードから分離できる |
| 設定読込 | **`tomllib`（標準）+ `pydantic-settings`** | PyYAML 依存を増やさない。`tomllib` は 3.11+ 標準 |
| スキーマ / 検証 | **`pydantic` v2** | AI 出力契約をそのまま型にできる。`anthropic` SDK も内部で使用 |
| テスト | **`pytest`** + `respx` または手製モック | Bedrock をモックして課金なしで CI を回す |

### 依存グループ（`pyproject.toml`）

```
[core]    anthropic[bedrock], feedparser, jinja2, pydantic, pydantic-settings
[market]  yfinance            # Phase 1 のみ。Phase 2 で外す候補
[dev]     pytest, ruff, mypy
```

`market` を分けておくのは、Phase 2 で Lambda パッケージからこれを除外できるようにするため。

## 3. AI 出力契約（構造化出力）

`output_config.format` で JSON Schema を強制し、Pydantic で受ける。要件の各項目をそのまま型にする。

```python
# src/market_report/models.py（抜粋）
from typing import Literal
from pydantic import BaseModel, Field

Horizon    = Literal["immediate", "short_term", "long_term"]
Direction  = Literal["up", "down", "volatility"]
Confidence = Literal["high", "medium", "low"]

class NewsAnalysis(BaseModel):
    headline: str
    summary: str          # 事実と市場予想とのズレ
    horizon: Horizon      # immediate(当日〜翌日) / short_term(2〜5日) / long_term(数週間〜)
    direction: Direction  # 上昇要因 / 下落要因 / ボラティリティ拡大
    confidence: Confidence
    transmission: str     # S&P500 への波及メカニズム
    source_urls: list[str]

class WatchItem(BaseModel):
    label: str            # 監視対象の指標・イベント名
    when: str             # いつ（日付や「今週後半」等）
    why: str              # なぜ重要か

class DailyAnalysis(BaseModel):
    sentiment_score: int = Field(ge=-5, le=5)
    sentiment_summary: str
    news_analysis: list[NewsAnalysis]   # 最大 5 件
    watch_list: list[WatchItem]
    report_markdown: str
    x_post_text: str
```

### 実装上の注意点

1. **`Enum` クラスではなく `Literal` を使う。** 構造化出力のスキーマは `additionalProperties: false` と全フィールド required を要求する。Pydantic の `Enum` は JSON Schema に `$defs` / `$ref` を生成するため、フラットな `Literal` の方が確実に通る。

2. **呼び出し方の第一候補と代替案。**
   - 第一候補: `client.messages.parse(output_format=DailyAnalysis)` — SDK が検証まで行う
   - 代替案: `client.messages.create(output_config={"format": {"type": "json_schema", "schema": ...}})` + `DailyAnalysis.model_validate_json()`
   
   `messages.parse()` が `AnthropicBedrockMantle` で利用可能かは **[要確認]**。最初の疎通確認時に実機で検証し、未対応なら代替案に切り替える。どちらでも結果は同等なので設計上のリスクはない。

3. **`max_tokens=16000` / 非ストリーミング。** レポート本文を含めても収まる想定（出力試算は約 7,800 トークン）。ストリーミングが必要になる規模ではない。

4. **件数・範囲の制約はコード側でも検証する。** `news_analysis` が 5 件を超えた場合、`sentiment_score` が範囲外の場合などは Pydantic で弾く。

### `x_post_text` の 140 全角文字制限

**モデルに文字数を数えさせるのは不確実なので、コード側で検証する。**

```
1. モデルには「本文のみ」を書かせる（プレフィックスは含めさせない）
2. コード側で全角換算の文字数をカウント
3. プレフィックス「【YYYY/MM/DD 米国市場AI解析 S&P500 ±X.XX%】」をコードで付与
4. 超過時: 本文のみを対象に再生成を 1 回 → それでも超過なら文末を決定的に切り詰め
```

プレフィックスをコード側で付与する理由は 2 つ:

- **Error 187（重複投稿）対策の確実性** — 日付と騰落率が必ず変わるため、プレフィックスがあれば投稿文が毎日必ず異なる。モデルの裁量に委ねない。
- **文字数計算をコードが完全に管理できる** — プレフィックス長は確定しているので、本文に許される残り文字数を正確に算出できる。

## 4. 処理フロー（`pipeline.py`）

```
1. 時刻解決      JST 07:00 基準 → 対象窓 = 直近24h(UTC)、「前日終値」= 前営業日ET終値
2. 収集          feeds.toml の各フィードを並列取得
                 （失敗フィードはスキップしてログ記録。1本の障害で全体を落とさない）
3. 正規化        タイトル正規化（記号・媒体名サフィックス除去）、窓外・本文空を除外
4. 重複排除      トークン集合Jaccard + difflib で閾値クラスタ化 → 各クラスタの代表1件
5. 市況取得      ^GSPC / ^TNX / ^VIX の終値・前日比
6. AI解析        Bedrock 構造化出力 → DailyAnalysis
7. 検証          Pydantic + x_post_text 文字数 + news_analysis ≤ 5
8. HTML生成      Jinja2 → report / index / archive
9. 公開          LocalPublisher → ./output/
```

### 各段の設計方針

**2. 収集** — フィード 1 本の障害で全体を落とさない。取得できたフィードだけで処理を続行し、失敗したフィードをログに残す。RSS は不安定なため、ここで落ちない設計が運用上重要。

**4. 重複排除** — タイトルを正規化してトークン集合の Jaccard 係数を取り、閾値以上を同一クラスタとみなす。各クラスタから 1 件（情報量が多いもの、または信頼度の高い媒体）を代表として選ぶ。閾値の初期値は **0.75** とし、実データを見て `settings.toml` で調整する **[推定: 実データで要調整]**。

**6. AI 解析** — Bedrock 呼び出しは例外ハンドリングを型別に分ける（レート制限 / 400系 / 接続エラー）。`APIStatusError` を一括で捕まえるとリトライ可否の判断がつかなくなる。

## 5. 設定ファイル

### `resources/feeds.toml`（当初 `config/feeds.toml`・A-149 で移動）

```toml
[[feed]]
name = "Reuters Business"
url = "https://..."
weight = 1.0        # 代表記事選抜時の媒体信頼度

[[feed]]
name = "CNBC Top News"
url = "https://..."
weight = 1.0
```

フィードの追加・削除がコード変更なしでできることを重視。

### `resources/settings.toml`（当初 `config/settings.toml`・A-149 で移動）

```toml
[bedrock]
# 環境変数 BEDROCK_MODEL_ID / AWS_REGION で上書きされる（Phase 2 で Terraform が注入）
model_id = "anthropic.claude-opus-5"
region = "ap-northeast-1"
max_tokens = 16000

[collection]
window_hours = 24
max_articles_to_analyze = 15

[dedupe]
similarity_threshold = 0.75

[market]
symbols = ["^GSPC", "^TNX", "^VIX"]

[output]
max_news_analysis = 5
x_post_max_fullwidth_chars = 140
```

## 6. CLI

```bash
# 通常実行（Bedrock を呼ぶ = 課金あり）
python -m market_report.cli

# Bedrock を呼ばずフィクスチャで全段を検証（課金なし）
python -m market_report.cli --dry-run

# 収集・重複排除だけ実行して結果を確認
python -m market_report.cli --stage collect

# モデルを上書きして実行（差し替え前の品質比較用）
python -m market_report.cli --model anthropic.claude-sonnet-5

# 期間レポートの生成（保存済みの日次JSONを入力にする）
python -m market_report.cli --type weekly  --period 2026-W47
python -m market_report.cli --type monthly --period 2026-11
python -m market_report.cli --type yearly  --period 2026
```

`--model` オプションは **モデル差し替え時の品質比較**のために用意する（[05](05-decisions.md) A-10）。同じニュースセットに対して複数モデルの出力を並べて確認できるようにしておくと、将来の移行判断が実データに基づいて行える。

設定値の優先順は **CLI オプション > 環境変数 > `settings.toml`** とする。Phase 2 で Terraform が Lambda 環境変数 `BEDROCK_MODEL_ID` を注入するため、この順序を Phase 1 の時点で実装しておく必要がある。

`--dry-run` は重要。HTML テンプレートの調整やデバッグのたびに Bedrock を呼ぶのは無駄なので、保存済みの解析結果 JSON を使ってレンダリング以降だけを回せるようにする。

## 7. Phase 1 の完了条件

- [ ] AWS コンソールで Bedrock のモデルアクセスを有効化し、対象モデルへの疎通を確認
- [ ] `python -m market_report.cli` で以下が生成される
  - `output/reports/YYYY-MM-DD.html`
  - `output/index.html`（最新レポート）
  - `output/archive.html`（アーカイブ一覧・4種別のセクション構成）
  - `output/data/daily/YYYY-MM-DD.json`（`schema_version` 付き）
- [ ] **日次 JSON のスキーマを確定**し、`schema_version` を付与する
- [ ] `--type weekly/monthly/yearly` で期間レポートが生成される（フィクスチャの日次 JSON を入力に）
- [ ] **統計サマリ（`PeriodStats`）の集計が AI を使わずに動く**
- [ ] **予測の的中率集計**が動く（`immediate` 判定 vs 翌取引日の S&P500）
- [ ] 日次 JSON が不足している期間では、期間レポートを生成せず正常終了する
- [ ] `--dry-run` で Bedrock を呼ばずに全段を検証できる
- [ ] `pytest` が Bedrock モックで通る（課金なし）
- [ ] 消費トークン数と推定コストがログに出る
- [ ] **[03](03-cost-and-tokens.md) のトークン試算を実測値で更新する**
- [ ] `^TNX` の値のスケール（利回りそのものか ×10 か）を実データで確認
- [ ] `git init` + GitHub リポジトリ作成、`main` のブランチ保護を設定
- [ ] `.github/workflows/ci.yml` の `python` ジョブ（lint / 型チェック / テスト）が通る
      ※ AWS 認証不要・Bedrock モックのため CI で課金は発生しない

## 8. Phase 1 で意図的にやらないこと

スコープを守るため、以下は Phase 1 では扱わない。

- S3 / CloudFront へのアップロード（Phase 2）
- EventBridge Scheduler 4 本の定義・日次の火〜土化（Phase 2）
- Terraform の記述・OIDC / デプロイワークフロー（Phase 2 → [06](06-cicd-and-environments.md)）
- Lambda へのパッケージング（Phase 2）
- X API 連携（Phase 3）
- 記事本文のクロール（トークン節約のため RSS summary のみを使う。品質不足が実測で確認されたら再検討）
- 複数指数・複数市場への拡張
