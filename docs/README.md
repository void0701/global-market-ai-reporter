# ドキュメント索引

米国・世界ニュース AI 解析＆自動レポート配信パイプラインの設計ドキュメント群。

| # | ドキュメント | 内容 |
|---|---|---|
| **14** | **[運用手順（ランブック）](14-runbook.md)** | **毎朝これを叩く。実行コマンド・週次月次年次・トラブル対応・課金なしで試す方法** |
| **00** | **[決定が必要な事項の一覧](00-open-items.md)** | **Phase 2 開始前は 0 件（決着済）。Phase 3 で 3 件 / 運用後 5 件 / 検証項目** |
| 01 | [アーキテクチャとディレクトリ構成](01-architecture.md) | システム全体構成、時刻設計、ディレクトリ構成案、拡張性の設計意図 |
| 02 | [Bedrock モデル選定と接続仕様](02-bedrock-selection.md) | モデルID、エンドポイント、認証・IAM、リージョン、モデル差し替え設計 |
| 03 | [運用コストとトークン試算](03-cost-and-tokens.md) | トークン内訳、モデル別コスト比較、AWS 課金内訳 |
| 04 | [Phase 1 実装計画](04-phase1-plan.md) | ライブラリ選定、出力契約、処理フロー、完了条件 |
| 05 | [決定事項と未確定事項](05-decisions.md) | 確定した設計判断（**169 件**）、任意採用候補、検証待ち項目 |
| 06 | [CI/CD と環境分離](06-cicd-and-environments.md) | GitHub Actions（OIDC）、dev/prod 分離、Terraform 構成、セキュリティ強化 |
| **07** | **[準備が必要なもの](07-prerequisites.md)** | **AWS アカウント、権限、X Developer 登録などのユーザー作業チェックリスト** |
| 08 | [RSS フィード検証結果](08-rss-feeds.md) | 20 フィードの実取得検証、採用 11 本、除外理由 |
| 09 | [週次・月次・年次レポート](09-periodic-reports.md) | 階層的ロールアップ、予測の的中率集計、スケジュール見直し |
| 10 | [スキーマと実現できることの対応表](10-schema-capabilities.md) | 可逆性マトリクス、現状で出せる集計13種、追加候補カタログ |
| **11** | **[データ保持方針（最大保持）](11-data-retention.md)** | **3層データモデル、全件保存、再解析CLI、推論要約の保存** |
| 12 | [オルカン（全世界株式）対応](12-global-index.md) | ACWI+USDJPY+2559.T、円建てリターンの分解、2本のセンチメント |
| **13** | **[Phase 2 実行計画と作業分担](13-phase2-plan.md)** | **誰が何をやるか、§8 に着手前レビュー（R-1〜R-12）、残る手作業** |

## 記述の信頼度について

各ドキュメント内で以下のラベルを使い分けています。混同しないよう注意してください。

- **[確認済]** — 公式ドキュメントまたは実機で確認した事実。出典URLを併記。
- **[試算]** — 仮定を明示した上での計算結果。仮定が変われば結果も変わる。
- **[推定]** — 根拠が弱い想定。実装・運用前に検証が必要。
- **[要確認]** — 未検証。Phase を進める前に確認すべき項目。

## 確定した主要構成

| 項目 | 決定 |
|---|---|
| AI モデル | **Claude Opus 5**（Claude API 直接。`provider` で Bedrock に切替可） |
| 呼び出し先 | **Claude API 直接**（Bedrock は AWS Marketplace 不可で契約できず / A-119） |
| AWS リージョン | **`ap-southeast-2`**（プロジェクト方式では変更不可 / A-120） |
| IaC | **Terraform**（環境別ディレクトリ構成） |
| 秘密情報 | **Secrets Manager**（+ CMK / リソースポリシー / CloudTrail） |
| 環境 | **`dev` / `prod`**（単一アカウント + リソース分離） |
| CI/CD | **GitHub Actions + OIDC**（アクセスキーを保存しない） |
| セキュリティ CI | **Checkov / Gitleaks / pip-audit / Dependabot** |
| RSS フィード | **11 本採用**（Fed / CNBC×3 / NYT×2 / MarketWatch / BBC / FT / SeekingAlpha / Guardian） |
| 対象指数 | **S&P500 + 全世界株式（オルカン・円建て）** |
| データ方針 | **最大保持**（生記事全件 / 市況40系列 / プロンプト / 推論要約） |
| レポート種別 | **日次（火〜土）/ 週次（月）/ 月次 / 年次** |
| 公開方式 | 完全公開。**AWS Amplify Hosting** の既定ドメイン（A-131。CloudFront は自前作成不可） |
| 想定運用コスト | **約 $10.5/月**（Anthropic $7.72 + AWS $2.80） |

## 現在のステータス

- 最終更新: 2026-11-26
- フェーズ: **Phase 1 稼働中**（手動実行。手順は [14](14-runbook.md)）/ **Phase 2 はコード完成・`terraform apply` 待ち**
- 確定した設計判断: **169 件**（[05](05-decisions.md) A-1〜A-169）
- 残る判断: **Phase 2 開始前は 0 件**（[00](00-open-items.md)）

### 次のアクション

| | 内容 |
|---|---|
| **ken さん** | **`infra/bootstrap` の `terraform apply`**（人間が 1 回だけ / [13 §2](13-phase2-plan.md)）→ 出力されたロール ARN の共有 → GitHub Environments `prod` の設定 |
| **Claude** | ken さんの apply 待ち。Phase 2 のコード（Lambda ハンドラ / Terraform / CI）は完成済み |

**毎朝のレポート生成は [14](14-runbook.md) の手順で手動実行**しています。
`terraform apply` が済むと EventBridge Scheduler が自動化します。
