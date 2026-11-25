# 01. アーキテクチャとディレクトリ構成

## 1. システム全体構成（最終形 / Phase 3 完了時）

```
EventBridge Scheduler (07:00 JST, Asia/Tokyo)
        │
        ▼
AWS Lambda (Python 3.13)
        │
        ├─ 1. RSS 取得 ────────► Reuters / CNBC / Yahoo Finance 等
        ├─ 2. 重複排除（ローカル処理）
        ├─ 3. 市況取得 ────────► Yahoo Finance (^GSPC / ^TNX / ^VIX)
        ├─ 4. AI 解析 ─────────► Amazon Bedrock (bedrock-mantle)
        │                           └─ 構造化出力 (JSON)
        ├─ 5. HTML 生成（Jinja2）
        ├─ 6. 公開 ────────────► S3 ──► CloudFront ──► 閲覧者
        └─ 7. SNS 投稿 ────────► X API v2
                                  └─ 認証情報: Secrets Manager
                                              or SSM Parameter Store
```

### 時刻設計

07:00 JST = 22:00 UTC（前日）= 17:00 ET（前日）。米国市場の引け（16:00 ET）後 1 時間の時点で実行されるため、「前日終値」は常に確定済みの値を取得できる。

- ニュース対象窓: 直近 24 時間（22:00 UTC 前々日 〜 22:00 UTC 前日）
- 市況の「前日」: 直近の米国営業日の終値

EventBridge **Scheduler**（旧 Events Rule ではない）は `ScheduleExpressionTimezone` に `Asia/Tokyo` を指定できるため、タイムゾーン指定で記述する。UTC 換算をコードや cron 式に埋め込まない。**[確認済]**

### 実行スケジュール（日次 + 週次 + 月次 + 年次）

| レポート | タイミング | cron（`Asia/Tokyo`） |
|---|---|---|
| 日次 | **火〜土 07:00** | `cron(0 7 ? * TUE-SAT *)` |
| 週次 | **月曜 07:00** | `cron(0 7 ? * MON *)` |
| 月次 | 毎月 1 日 07:30 | `cron(30 7 1 * ? *)` |
| 年次 | 1 月 1 日 08:00 | `cron(0 8 1 1 ? *)` |

**日曜は実行しない。** 07:00 JST = 17:00 ET（前日）という時刻設計上、日曜・月曜の 24 時間窓には米国の取引セッションが 1 つも含まれないため（検証結果は [09 §2-1](09-periodic-reports.md)）。空く月曜の枠に週次レポートを充てる。

4 本の Scheduler が**同一の Lambda を異なるペイロード（`report_type`）で呼ぶ**。詳細は [09](09-periodic-reports.md)。

## 2. ディレクトリ構成案

設計の最優先事項は **「Phase 1 のローカルスクリプトが、Phase 2 で Lambda ハンドラからそのまま呼ばれる」** こと。そのため I/O（データ取得・成果物公開）を差し替え可能な境界として切り出している。

```
s_and_p/
├── README.md
├── pyproject.toml              # 依存をグループ分け（core / dev / lambda）
├── .env.example                # AWS_REGION, AWS_PROFILE 等
├── .gitignore
├── docs/                       # 本ドキュメント群
├── src/
│   └── market_report/
│       ├── resources/          # ★設定はパッケージに同梱する（A-149）
│       │   ├── feeds.toml      #   RSS フィード定義（追加はコード変更不要）
│       │   └── settings.toml   #   モデルID・閾値・銘柄シンボル・記事上限
│       ├── config.py           # tomllib + importlib.resources でロード
│       ├── models.py           # ★AI出力契約(Pydantic) + 内部DTO
│       ├── pipeline.py         # ★オーケストレーション（Lambdaから呼ぶ中核）
│       ├── cli.py              # ローカル実行: python -m market_report.cli
│       ├── collectors/
│       │   ├── rss.py          # feedparser / 24時間窓フィルタ
│       │   └── market.py       # MarketDataProvider（抽象 + yfinance実装）
│       ├── processing/
│       │   ├── normalize.py    # タイトル正規化・本文トリム
│       │   └── dedupe.py       # 類似度クラスタリング（代表記事を選抜）
│       ├── analysis/
│       │   ├── client.py       # AnthropicBedrockMantle のラッパ
│       │   ├── prompts.py      # system / user プロンプト
│       │   └── analyzer.py     # 構造化出力呼び出し + 検証 + リトライ
│       ├── rendering/
│       │   ├── html.py         # Jinja2
│       │   └── templates/
│       │       ├── report.html.j2
│       │       └── archive.html.j2
│       ├── publishing/
│       │   ├── base.py         # Publisher プロトコル（put / list）
│       │   ├── local.py        # Phase 1: ./output へ書き出し
│       │   ├── s3.py           # Phase 2
│       │   └── x_api.py        # Phase 3
│       └── utils/
│           ├── logging.py      # JSON構造化ログ（CloudWatch前提）
│           └── text.py         # 全角文字数カウント等
├── lambda_handler.py           # Phase 2: pipeline を呼ぶだけの薄い層
├── infra/                      # Phase 2: Terraform（bootstrap / modules / envs）
├── .github/workflows/          # Phase 1: ci.yml / Phase 2: deploy-*.yml
├── tests/
│   ├── fixtures/               # サンプルRSS・Bedrockレスポンス
│   ├── test_dedupe.py
│   ├── test_analyzer.py        # Bedrock はモック（課金なしでCI可）
│   └── test_text.py            # 140字判定
└── output/                     # .gitignore（ローカル生成HTML）
```

## 3. 拡張性のための 2 つの抽象境界

この 2 つが構成の要。他は素直なレイヤ分割にすぎない。

### 3-1. `publishing/base.py` の `Publisher` プロトコル

```
Phase 1: LocalPublisher  → ./output/ に書き出し
Phase 2: S3Publisher     → S3 PutObject + CloudFront invalidation
```

`pipeline.py` は `Publisher` インターフェースにのみ依存するため、Phase 2 で公開先を差し替えても中核ロジックは無変更。ローカルで HTML を目視確認してから S3 に載せる、という検証順序が自然に取れる。

### 3-2. `collectors/market.py` の `MarketDataProvider`

これは **Lambda のパッケージサイズ問題に対する先手**である。

`yfinance` は pandas / numpy を推移的に引き込むため、デプロイパッケージが 60MB を超える。Lambda の zip 直接アップロード上限（50MB 圧縮 / 250MB 展開）に抵触しうるうえ、manylinux ホイールの取得も必要になり、Lambda Layer またはコンテナイメージが事実上必須となる。**[推定: 実測は Phase 2 で行う]**

一方、本システムが市況データに求めるのは **3 銘柄の終値と前日比だけ** であり、pandas の DataFrame 機能は一切不要。Yahoo Finance の chart エンドポイントを標準ライブラリ `urllib` で直接呼べば依存ゼロで足りる。

```
Phase 1: YFinanceProvider        → yfinance（指示どおり。ローカル検証が容易）
Phase 2: HttpChartProvider       → urllib のみ。依存ゼロ・軽量
```

**この分離を Phase 1 の時点で入れておかないと、Phase 2 で確実に手戻りが発生する。** 逆に言えば、インターフェースさえ切っておけば Phase 2 での差し替えは 1 ファイルの追加で済む。

## 4. Phase 2 以降で追加するリソース

全リソースは **`ap-northeast-1`（東京）** に配置し、`dev` / `prod` の 2 環境を `${project}-${env}-` プレフィックスで分離する。環境分離・Terraform 構成・CI/CD の詳細は [06](06-cicd-and-environments.md) を参照。


| リソース | 用途 | 備考 |
|---|---|---|
| S3 バケット | 静的 HTML / JSON | パブリックアクセスはブロックし、OAC 経由で CloudFront のみ許可 |
| CloudFront ディストリビューション | 配信 | ACM 証明書は無料（独自ドメイン使用時） |
| Lambda 関数 | 実行基盤 | メモリ 1024MB / **タイムアウト 600s**（LLM 実測 153s + 取得 20s に対し 300s では余裕が不足 → [03 §0](03-cost-and-tokens.md)） |
| EventBridge Scheduler | 定期実行 | `Asia/Tokyo` タイムゾーン指定 |
| IAM ロール | Lambda 実行ロール | `bedrock-mantle:CreateInference`, `s3:PutObject`, CloudWatch Logs |
| Secrets Manager | X API 認証情報 | **Phase 3 / prod のみ。** dev には作成しない（投稿事故の構造的防止 → [06 §1-1](06-cicd-and-environments.md)） |
| KMS 顧客管理キー | state / シークレットの暗号化 | Phase 2。1 キーを共用 |

### 出力ファイルのレイアウト（S3 / CloudFront）

```
/index.html                      最新の日次レポート
/weekly.html                     最新の週次レポート
/monthly.html                    最新の月次レポート
/archive.html                    全レポートの目次

/reports/YYYY-MM-DD.html         日次アーカイブ
/weekly/YYYY-Www.html            週次アーカイブ（ISO週番号）
/monthly/YYYY-MM.html            月次アーカイブ
/yearly/YYYY.html                年次アーカイブ

/data/daily/YYYY-MM-DD.json      日次の構造化データ
/data/weekly/YYYY-Www.json
/data/monthly/YYYY-MM.json
/data/yearly/YYYY.json
```

JSON も併せて置く理由は 2 つある。

1. HTML テンプレートを後から変更したときに過去分を再生成できる
2. **週次・月次・年次レポートの入力になる**（[09](09-periodic-reports.md)）— 期間レポートは RSS を再取得せず、保存済みの日次 JSON を集計する

AI 解析は 1 回しか走らないため、構造化データを捨てない設計にしておく価値が高い。**この判断が結果的に期間レポートの基盤になった。**

> ⚠️ **日次 JSON のスキーマは Phase 1 で確定させる必要がある。** 後から変更すると、それ以前に生成した日次データが集計できなくなる。`schema_version` フィールドを持たせること（[09 §7-2](09-periodic-reports.md)）。
