# 02. Bedrock モデル選定と接続仕様

調査日: 2026-11-22
主な出典:
- https://platform.claude.com/docs/en/build-with-claude/claude-in-amazon-bedrock
- https://platform.claude.com/docs/en/build-with-claude/claude-on-amazon-bedrock-legacy

## 1. 最重要: エンドポイントは 2 系統ある

Bedrock で Claude を呼ぶ経路は 2 つあり、**採用すべきものが変わっている**。**[確認済]**

| | `bedrock-mantle`（本線・採用） | `bedrock-runtime`（レガシー） |
|---|---|---|
| API | Messages API 互換 `/anthropic/v1/messages` | `InvokeModel` / `Converse` |
| リクエスト形状 | Claude API と同一 | 独自。`anthropic_version: bedrock-2023-05-31` をボディに埋める |
| ストリーミング | 標準 SSE | AWS event-stream エンコーディング |
| Python クライアント | `AnthropicBedrockMantle` | `AnthropicBedrock` / boto3 |
| モデルID | `anthropic.claude-opus-5` | `global.anthropic.claude-opus-4-6-v1` 等（推論プロファイル接頭辞が必要） |

**本プロジェクトは `bedrock-mantle` を採用する。**

web 上の記事や過去の知識に多く出てくる「boto3 の `bedrock-runtime` クライアントで JSON ボディを手組みし、`anthropic_version` を指定する」方式はレガシー経路である。Messages API と同形状で書ける mantle 側を使えば、構造化出力・Pydantic 検証がそのまま使え、ローカル（Claude API 直）と Lambda（Bedrock）でコードをほぼ共有できる。

> 補足: `bedrock-runtime` は推論プロファイル接頭辞（`global.` / `us.` / `eu.` 等）を要求し、ベースモデルIDをそのまま渡すと HTTP 400 になるという罠がある。mantle 側ではこの接頭辞は不要。

## 2. Python クライアント

```bash
pip install -U "anthropic[bedrock]"
```

```python
from anthropic import AnthropicBedrockMantle

client = AnthropicBedrockMantle(aws_region="us-east-1")

message = client.messages.create(
    model="anthropic.claude-opus-5",
    max_tokens=16000,
    messages=[{"role": "user", "content": "..."}],
)
```

**[確認済]** リージョンはコンストラクタ引数、または `AWS_REGION` 環境変数（なければ `AWS_DEFAULT_REGION`）から解決される。認証情報はコンストラクタ引数 → `AWS_BEARER_TOKEN_BEDROCK` → 通常の AWS クレデンシャルチェーン（`AWS_ACCESS_KEY_ID` 等、`~/.aws/credentials`、Lambda 実行ロール）の順。

つまり **Lambda 上では実行ロールが自動で使われ、認証情報のコードは一切不要**。ローカルでは `AWS_PROFILE` で切り替えられる。

## 3. モデルID 一覧（bedrock-mantle）

モデルIDには **`anthropic.` 接頭辞が必須**。ファーストパーティの `claude-opus-5` のような ID をそのまま渡すと 400 エラーになる。**[確認済]**

| モデル | Bedrock モデルID | Bedrock でのアクセス可否 |
|---|---|---|
| Claude Opus 5.5 | `anthropic.claude-opus-5-5` | 要個別確認 |
| **Claude Opus 5（★採用）** | `anthropic.claude-opus-5` | 要個別確認 |
| Claude Opus 4.8 | `anthropic.claude-opus-4-8` | **全顧客にオープン** |
| Claude Opus 4.7 | `anthropic.claude-opus-4-7` | **全顧客にオープン** |
| Claude Sonnet 5.5 | `anthropic.claude-sonnet-5-5` | 要個別確認 |
| Claude Sonnet 5 | `anthropic.claude-sonnet-5` | **全顧客にオープン** |
| Claude Haiku 5.5 | `anthropic.claude-haiku-5-5` | 要個別確認 |
| Claude Haiku 4.5 | `anthropic.claude-haiku-4-5` | **全顧客にオープン** |
| Claude Fable 5.1 | `anthropic.claude-fable-5-1` | **全顧客にオープン** |
| Claude Fable 5 | `anthropic.claude-fable-5` | **全顧客にオープン** |

**[要確認]** Opus 5 / Opus 5.5 / Sonnet 5.5 / Haiku 5.5 は「全顧客にオープン」とは明記されておらず、AWS コンソールの Bedrock > モデルアクセス で個別に基準を確認する必要がある。**Phase 1 の最初の作業として、ここでのアクセス有効化を行う。**

**[要確認]** Claude Opus 5.5 / Sonnet 5.5 / Haiku 5.5 は手元の単価情報が無い新世代モデル。選択肢として存在することは確認済みだが、価格・挙動を確認できていないため現時点では推奨に含めない。

### 採用: `anthropic.claude-opus-5` 【確定 2026-11-22】

理由:

1. **タスクが推論密度に依存する。** 本件の要求は「事実と市場予想とのズレ」「S&P500 への波及メカニズム」「影響の時間軸分類」といった、単純な要約ではない因果推論である。出力品質がそのまま成果物の価値になる。
2. **コスト差が絶対額として小さい。** 1 日 1 回の実行であり、Opus 5 と Sonnet 5 の月額差は $4 程度（[03](03-cost-and-tokens.md) 参照）。品質を落として節約する合理性が薄い。
3. **適応的思考（adaptive thinking）がデフォルト ON。** Opus 5 は `thinking` パラメータを渡さなくても適応的思考が働く。深い分析タスクに向く。

### モデル差し替えを前提とした設計

将来より新しいモデルへ移行することを前提に、**モデルIDをコードに埋め込まない**。

- Terraform 変数 `bedrock_model_id` → Lambda 環境変数 `BEDROCK_MODEL_ID` として注入する
- IAM の `bedrock-mantle:CreateInference` の Resource も同じ Terraform 変数群（`allowed_model_ids`）から生成する

これにより差し替えは **tfvars の変更 + `terraform apply` のみ**で完了し、Python コードの変更も Lambda zip の再ビルドも不要になる。

> **落とし穴:** Lambda の環境変数だけ新モデルに変えて IAM の Resource が旧モデル ARN に固定されていると `AccessDeniedException` になる。環境変数と IAM を同一の変数から生成することでこれを防ぐ。

差し替え手順のチェックリストは [06 §6](06-cicd-and-environments.md) を参照。

### API パラメータの注意点 **[確認済]**

- `thinking` パラメータは **渡さない**。Opus 5 はデフォルトで adaptive thinking が有効。`{type: "enabled", budget_tokens: N}` は 400 エラーになる（旧世代の書き方なので注意）。
- `temperature` / `top_p` / `top_k` は **使えない**（400 エラー）。
- `output_config.effort` のデフォルトは `high`。本タスクでは `high` が妥当なので明示指定は不要。
- アシスタントメッセージの prefill は **400 エラー**。出力形式の制御は構造化出力で行う。

## 4. 認証と IAM

**[確認済]** 認証経路は 3 つあり、推奨順は以下。

| 方式 | 用途 | 必要な IAM アクション |
|---|---|---|
| Bedrock サービスロール | 本番の長期アクセス（最も安全） | `iam:PassRole` + `bedrock-mantle:CreateInference` |
| IAM 引き受けロール | ID フェデレーション（最大12時間セッション） | `bedrock-mantle:CreateInference` |
| ベアラートークン | リクエスト署名ができない環境のみ | `bedrock-mantle:CallWithBearerToken` + `bedrock-mantle:CreateInference` |

### Lambda 実行ロールに必要な権限（Phase 2）

```
bedrock-mantle:CreateInference   # 対象モデル ARN に絞る
s3:PutObject                     # レポート用バケットのみ
logs:CreateLogStream, logs:PutLogEvents
secretsmanager:GetSecretValue    # Phase 3。SSM 採用時は ssm:GetParameter + kms:Decrypt
```

`bedrock-mantle:CreateInference` は Resource をモデル ARN で絞り込める。ワイルドカードにせず、使用するモデルだけに限定する。

**[確認済]** ベアラートークン（Amazon Bedrock API キー）は AWS 自身が「可能な限り一時的な AWS 認証情報での署名を推奨」としており、3 方式のうち最も推奨度が低い。Lambda では実行ロールが使えるので、ベアラートークンを使う理由はない。ローカル開発でも `AWS_PROFILE` で足りる。

## 5. リージョン

**[確認済]** Claude in Amazon Bedrock は主要リージョンで利用可能。本件の候補:

| リージョン | ロケーション | エンドポイント種別 |
|---|---|---|
| `us-east-1` | US East (N. Virginia) | Global, US, 単一リージョン直指定可 |
| `ap-northeast-1` | Asia Pacific (東京) | Global, JP, 単一リージョン直指定可 |

グローバルエンドポイントは全リージョンへ動的ルーティングされ **価格プレミアムなし**。リージョナルエンドポイント（データ所在地要件向け）は **10% の価格プレミアム** が付く。**[確認済]**

本件は公開ニュースの解析でありデータ所在地要件がないため、**グローバル（プレミアムなし）で問題ない**。

### リージョン選択の考慮

- S3 / CloudFront / Lambda も含めた構成全体のレイテンシとデータ転送を考えると、**全リソースを同一リージョンに揃える** のが単純。
- 1 日 1 回のバッチ処理なのでレイテンシは実質無関係。
- `ap-northeast-1`（東京）に揃えると、AWS コンソールでの運用・CloudWatch ログ確認が日本時間の感覚と一致して扱いやすい。

→ **`ap-northeast-1` を採用**【確定 2026-11-22】。全リソース（S3 / CloudFront のエッジ以外 / Lambda / Secrets Manager / Terraform state）を東京に揃える。

## 6. クォータ **[確認済]**

- デフォルト: **200 万入力トークン/分（TPM）**
- 申請により Anthropic の追加承認なしで 500 万入力 TPM / 50 万出力 TPM まで引き上げ可能
- RPM（リクエスト毎分）制限は AWS 側で管理。調整は AWS サポート経由

本件の消費は 1 日あたり入力 5 千トークン程度（[03](03-cost-and-tokens.md)）なので、**クォータは一切問題にならない**。

## 7. Bedrock で使えない機能（本件への影響） **[確認済]**

| 非対応機能 | 本件への影響 |
|---|---|
| サーバーサイドツール（web search / web fetch / code execution） | **影響あり。** AI に Web 検索させる設計は採れない。ニュースは RSS で自前収集する現設計のままで正しい |
| Message Batches API | 影響なし（1日1回の単発呼び出し） |
| Models API | 影響なし |
| Files API / URL ソース | 影響なし |
| サーバーサイド fallback（`fallbacks` パラメータ） | 影響小。必要ならクライアント側 fallback パターンで代替 |
| Agent Skills / MCP connector / Managed Agents | 影響なし |

**使える機能のうち本件で重要なもの:** Messages API、**構造化出力**、thinking、ツール使用、プロンプトキャッシュ。構造化出力が Bedrock でサポートされていることが、本設計の前提として最も重要な確認事項だった。

## 8. プロンプトキャッシュは使わない

**[確認済 + 判断]** Bedrock はプロンプトキャッシュをサポートするが、**本件では採用しない**。

キャッシュの TTL は 5 分または 1 時間。本システムは **1 日 1 回しか実行されない** ため、次回実行時にはキャッシュが必ず失効しておりヒット率は構造的に 0% になる。実装コストを払う意味がない。

（将来、同一実行内でリトライや複数モデル比較を行うようになった場合のみ再検討する余地がある。）

## 9. ログと監視 **[確認済]**

Claude in Amazon Bedrock は CloudWatch と CloudTrail の両方にログを出力する。Anthropic は **30 日ローリングでのアクティビティログ保持** を推奨している。Phase 2 で CloudWatch Logs の保持期間を 30 日に設定する。
