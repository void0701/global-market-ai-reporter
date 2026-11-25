# 06. CI/CD（GitHub Actions）と環境分離

最終更新: 2026-11-22
対象フェーズ: **Phase 2 以降**（ただし Python の CI ジョブのみ Phase 1 から有効化できる）

## 0. 前提: 未整備の項目

> ### 🔴 着手前に必ず読む: アカウント制約の検証
>
> 本プロジェクトの AWS アカウントは **「Sign up for AWS (new)」（プロジェクト方式）** です。
> AWS が管理する SCP / RCP が適用されており、**こちらからは変更できません**。
> これまでに **2 回**、この制約で作業が止まっています（AWS Marketplace が使えず Bedrock の
> モデル契約を結べない／リージョンを変更できない）。
>
> **未検証の権限が残っています。**
>
> | 操作 | 必要な権限 | 状態 |
> |---|---|---|
> | OIDC プロバイダの作成 | `iam:CreateOpenIDConnectProvider` | ❓ 未検証 |
> | IAM ロールの作成 | `iam:CreateRole` | ❓ 未検証 |
> | KMS CMK の作成 | `kms:CreateKey` | ❓ 未検証 |
> | S3 バケットポリシー | `s3:PutBucketPolicy` | ❓ 未検証 |
>
> **本格的な HCL を書く前に、§2-0 の検証スパイクを実施してください。**
> 500 行書いてから弾かれるのが最悪のパターンです。

### このアカウント方式による前提の変更

| 項目 | 本章での扱い |
|---|---|
| IAM Identity Center | **自前構築は不可かつ不要**（AWS が自動構成）。§0 の準備項目から除外 |
| IAM ユーザー / ロール | **プログラマティックアクセス専用**。GitHub OIDC・Lambda 実行ロールは該当するので利用可（ただし上記の未検証事項あり） |
| リージョン | **`ap-southeast-2` 固定**（変更不可） |
| AWS Organizations | AWS 管理。自前利用は不可（単一アカウント設計のため影響なし） |
| クロスリージョン機能 | **非対応。** CloudFront の独自ドメイン用 ACM 証明書は `us-east-1` 必須のため、**独自ドメイン移行時に問題になる可能性**（当面デフォルトドメインのため影響なし） |


| 項目 | 現状 | 必要な作業 |
|---|---|---|
| git リポジトリ | **未初期化**（確認済） | `git init` |
| GitHub リモート | **未設定** | リポジトリ作成 + `git remote add` |
| Terraform | **未インストール**（確認済） | `brew install terraform` |
| AWS CLI | **未インストール**（確認済） | `brew install awscli` |

---

## 1. 環境分離の方針

### 1-1. 環境は 2 つ

| 環境 | 用途 | スケジュール実行 | X 投稿 |
|---|---|---|---|
| **`dev`** | 動作検証・インフラ変更の事前確認 | **無効**（手動実行のみ） | **無効** |
| **`prod`** | 本番。毎朝 07:00 JST に自動実行 | 有効 | 有効 |

**最重要の設計判断: `dev` では X 投稿を構造的に不可能にする。**

X への投稿は本システムで唯一の「外部に公開され、取り消しが効かない」操作である。設定フラグ 1 つで切り替える方式は事故が起きやすいため、**`dev` 環境には X API のシークレットそのものを作成しない**。Lambda は認証情報を取得できず、投稿処理は起動時点で無効化される。

```
dev:  Secrets Manager にシークレット無し → X Publisher は初期化時に無効化
prod: シークレット有り → 通常動作
```

これにより「dev の設定ミスで本番アカウントに投稿してしまう」という事故が、設定レベルではなく**リソースレベルで**防がれる。

### 1-2. AWS アカウントを分けるか

| 選択肢 | 分離の強度 | 運用負荷 |
|---|---|---|
| **アカウント分離**（AWS Organizations） | 最強。IAM・課金・クォータが完全に独立 | 高。Organizations + IAM Identity Center のセットアップ、クロスアカウントロール設計 |
| **単一アカウント + リソース分離**（推奨） | 中〜強。IAM ロール・リソース名・Terraform state で分離 | 低 |

**推奨: 単一アカウント + リソース分離。**

理由は、本システムが扱うデータが **公開ニュースと公開レポート** であり、漏洩時の被害が限定的であること。保護すべき資産は実質的に「X API の認証情報」1 点に集約される。これは Secrets Manager の権限設計（後述 3 節）で十分に守れる。

アカウント分離の主な価値は「本番データへの意図しないアクセスの防止」だが、本システムには保護すべき本番データが存在しない。運用負荷に見合わない。

ただし以下は厳格に分離する:
- **IAM ロール**: dev と prod で別ロール。prod ロールは dev のワークフローから引き受け不可
- **Terraform state**: 別の state ファイル（同一バケット内で key を分離）
- **リソース名**: 全リソースに `${project}-${env}-` プレフィックス
- **シークレット**: prod のみに存在

> **アップグレードパス:** 将来アカウント分離に移行する場合、Terraform を環境別ディレクトリ構成（下記 2 節）にしておけば、provider の `assume_role` 設定を差し替えるだけで移行できる。今の構成がそのまま移行の土台になる。

### 1-3. 環境ごとのリソース差分

| リソース | dev | prod | 備考 |
|---|---|---|---|
| S3（レポート） | あり | あり | 別バケット |
| CloudFront | **あり** | あり | 構成差を作らないため dev にも置く（下記参照） |
| Lambda | あり | あり | 別関数 |
| EventBridge Scheduler | **あり（無効状態）** | あり（有効） | `state = "DISABLED"` で作成 |
| Secrets Manager | **なし** | あり | **Phase 2 から**（`ANTHROPIC_API_KEY`）、Phase 3 で X 用を追加。下記 |
| CloudWatch Logs | あり（保持 7日） | あり（保持 30日） | dev は短く |
| AWS Budgets アラート | なし | あり | |

**dev にシークレットを一切置かない理由:** `ANTHROPIC_API_KEY` が Phase 2 から必要になりますが、
**dev には置きません。** dev の目的は S3 / OAC / IAM / Scheduler の**配線の検証**であり、
スモークテストは `dry_run: true` で行うため LLM を呼びません。実際の解析品質の検証は
ローカル実行で足ります（Phase 1 で実施済み）。これにより [05](05-decisions.md) A-28 の
「dev はシークレットを持たない」という性質が Phase 2 以降も保たれ、コストも抑えられます。

**dev にも CloudFront を置く理由:** S3 の Block Public Access + OAC（Origin Access Control）+ バケットポリシーの組み合わせは設定ミスが起きやすい箇所で、ここを dev で検証できないと prod で初めて壊れる。構成の差分は検証価値を下げるため、同じモジュールを両環境に適用する。コストはどちらも無料枠内。

**ただし注意:** CloudFront の作成・更新は 5〜15 分かかる。dev での `terraform apply` が遅くなるため、**日常的な反復は Phase 1 の `--dry-run`（ローカル実行）で行い、`dev` 環境はインフラ変更の検証に限って使う**。この使い分けを守らないと開発速度が落ちる。

---

## 2. Terraform の構成

### 2-0. 🔴 最初にやること: 権限の検証スパイク（15分）

本番の構成を書く前に、**このアカウントで必要な操作が許可されるかを最小構成で確認**します。

```
infra/spike/        ← ローカル state。確認後に destroy する
  main.tf           S3 バケット1個 + KMS キー1個 + IAM ロール1個 + OIDC プロバイダ1個
```

| 結果 | 次の行動 |
|---|---|
| ✅ 全て apply できる | 本番の HCL を書き始めてよい |
| ❌ いずれかが拒否される | **その時点で方針判断**（高度な機能の有効化 / 別アカウント / 構成変更）。HCL を書く労力が無駄にならない |

確認が済んだら `terraform destroy` し、`infra/spike/` は削除します。

### 2-1. ディレクトリ構成

```
infra/
├── bootstrap/              # ★最初に1回だけ手動apply（state置き場を作る）
│   └── main.tf             #   state用S3バケット + OIDCプロバイダ + GHAロール
├── modules/
│   ├── report_site/        # S3 + CloudFront + OAC + バケットポリシー
│   ├── pipeline/           # Lambda + IAMロール + EventBridge Scheduler
│   └── secrets/            # Secrets Manager + CMK（prodのみ呼び出す）
└── envs/
    ├── dev/
    │   ├── main.tf         # modules を呼ぶ
    │   ├── backend.tf      # key = "dev/terraform.tfstate"
    │   ├── versions.tf
    │   └── terraform.tfvars
    └── prod/
        ├── main.tf
        ├── backend.tf      # key = "prod/terraform.tfstate"
        ├── versions.tf
        └── terraform.tfvars
```

### 2-2. workspace ではなく環境別ディレクトリを使う

| | 環境別ディレクトリ（採用） | `terraform workspace` |
|---|---|---|
| backend 設定 | 環境ごとに独立 | 全環境で共有 |
| 誤適用のリスク | ディレクトリが違えば起きない | **`workspace select` を忘れると本番に適用される** |
| provider 設定の差異 | 環境ごとに変えられる | 変えられない |
| 変更の影響範囲 | ディレクトリを見れば分かる | コードから読み取りにくい |

**workspace は採用しない。** 最大の理由は、`terraform workspace select` の実行漏れという**サイレントな事故**が起こりうること。CI でも人間の手元でも、「prod に適用するには prod ディレクトリに入る」という物理的な区別がある方が安全。

### 2-3. state の保管

```
S3 バケット: ${project}-tfstate-${account_id}
  ├── dev/terraform.tfstate
  └── prod/terraform.tfstate
```

**state バケットの必須設定:**

| 設定 | 理由 |
|---|---|
| バージョニング有効 | 誤った apply からの巻き戻し |
| Block Public Access（全4項目） | **state は機密情報を含む**（下記） |
| SSE-KMS 暗号化（CMK） | 同上 |
| `prevent_destroy` ライフサイクル | 誤削除防止 |
| state ロック | 同時 apply による破壊を防ぐ |

state ロックは **S3 ネイティブロック（`use_lockfile = true`）** を使う。Terraform 1.10 以降で利用可能で、DynamoDB テーブルが不要になる。**[要確認: 使用する Terraform バージョンでのサポート状況]** 未対応バージョンなら DynamoDB ロックテーブルを併用する。

> ### ⚠️ Terraform state には秘密情報が平文で保存される
>
> **これが B-4（秘密情報の保護）で最も重要な論点。**
>
> Terraform は管理対象リソースの属性値を state に書き込む。`aws_secretsmanager_secret_version` の `secret_string` を Terraform で管理すると、**X API の認証情報が state ファイルに平文で記録される**。Secrets Manager を選んで強固にしたつもりが、state 経由で漏れるという本末転倒が起きる。
>
> **対策（必ず守る）:**
> - Terraform は **シークレットの「容器」だけを作る**（`aws_secretsmanager_secret`）
> - **値は Terraform で管理しない**（`aws_secretsmanager_secret_version` を書かない）
> - 値の投入は AWS CLI またはコンソールから **1 回だけ手動で**行う
> - GitHub Secrets に X の認証情報を置いて Terraform に渡す、という設計は**採らない**
>
> ```bash
> # 値の投入は人間が1回だけ手動で行う（CI には通さない）
> aws secretsmanager put-secret-value \
>   --secret-id s-and-p-prod-x-api \
>   --secret-string file://./x-credentials.json
> ```
>
> 同じ理由で **state バケット自体も機密情報扱い**とし、上記の暗号化・アクセス制限を施す。

### 2-4. bootstrap（鶏と卵の解決）

state を S3 に置くには、まず S3 バケットが必要。`infra/bootstrap/` はローカル state で動く独立した構成として、以下を**人間が 1 回だけ** apply する。

1. state 用 S3 バケット + KMS キー
2. GitHub Actions 用の OIDC プロバイダ
3. GitHub Actions が引き受ける IAM ロール 3 種（後述 4 節）

bootstrap の state はローカルに残り、GitHub 上には置かない（`.gitignore`）。bootstrap を変更する頻度は年に数回あるかどうかなので、手動運用で問題ない。

### 2-5. バージョン固定

```hcl
terraform {
  required_version = "~> 1.10"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}
```

> ### ⚠️ `~> 5.0` では Amplify が無言でハングする【改訂 2026-11-25 / R-7】
>
> 当初 `~> 5.0` としていましたが、**プロバイダ 5.100.0 は `aws_amplify_app` の作成で
> 14 分間ハングしました**（出力なし）。実際にはレート制限のリトライを続けていただけで、
> 6.74.0 に上げると原因が即座に表示されます。
>
> ```
> BadRequestException: Rate exceeded while calling CreateApp, please slow down or try again later.
> ```
>
> 詳細は [13 §8 R-7](13-phase2-plan.md)。

`.terraform.lock.hcl` は**コミットする**。CI とローカルでプロバイダのバージョンが揺れるのを防ぐ。

---

## 3. セキュリティ強化（B-4 の実装）

B-4 で「できるだけ強固な方」という方針をいただいたため、Secrets Manager を採用する。ただし **Secrets Manager を選ぶだけでは強固にならない**。以下をセットで実装する。

### 3-0. Lambda 実行ロールの権限（Claude API 構成）

[05](05-decisions.md) A-119 で Bedrock から Claude API 直接に変更したため、**Lambda の IAM から
Bedrock 関連の権限が消え、設計が単純になりました**。

| 権限 | 用途 |
|---|---|
| `secretsmanager:GetSecretValue` | `ANTHROPIC_API_KEY` の取得（シークレットの完全 ARN に限定） |
| `kms:Decrypt` | 上記シークレットの復号（`secrets` 用 CMK に限定） |
| `s3:PutObject` | レポート用バケットのみ |
| `logs:CreateLogStream` / `logs:PutLogEvents` | CloudWatch Logs |
| **`s3:GetObject` / `s3:ListBucket`** | **`rebuild_index` が過去の全日次レコードを読む**（`pipeline.py:160`）。これなしでは 2 日目から `AccessDenied`【追加 2026-11-25 / [13 §8 R-2](13-phase2-plan.md)】 |
| **`amplify:CreateDeployment` / `StartDeployment` / `GetJob`** | HTML の配信（§9）【追加 2026-11-25】 |
| **`sns:Publish`** | 失敗通知（A-53）。検知方式は [13 §8 R-6](13-phase2-plan.md) |

> **消えたもの:** `bedrock-mantle:CreateInference`、およびモデル ARN を Resource に指定する設計。
> §6-2 で警告していた「IAM が旧モデル ARN のままで `AccessDenied`」という落とし穴も
> **構造的に発生しなくなりました**。

#### ⚠️ シークレットの読み込み方（要実装）

現在のコードは `ANTHROPIC_API_KEY` を**環境変数から**読みます。Lambda では:

- **Lambda の環境変数に直接入れてはいけません**（コンソールで誰でも見えます）
- **ハンドラのコールドスタート時に Secrets Manager から取得し、`os.environ` に設定**します

`lambda_handler.py` は未作成なので、Phase 2 でこの処理を実装します。

### 3-1. Secrets Manager の権限設計

| 対策 | 内容 |
|---|---|
| **顧客管理 KMS キー（CMK）** | デフォルトの `aws/secretsmanager` ではなく専用 CMK を使う。キーポリシーで復号できるプリンシパルを Lambda 実行ロールに限定 |
| **リソースポリシー** | シークレット側にもポリシーを付け、Lambda 実行ロール以外の `GetSecretValue` を明示的に拒否（IAM ロール側の権限と二重で守る） |
| **IAM 権限の最小化** | `secretsmanager:GetSecretValue` のみ。Resource はシークレットの完全 ARN。`*` やプレフィックスワイルドカードを使わない |
| **CloudTrail データイベント** | シークレットへのアクセスを記録。想定外の読み取りを検知できる |
| **環境変数に入れない** | Lambda の環境変数にシークレット値を置かない（コンソールで誰でも見える）。実行時に API で取得する |

### 3-2. ローテーションについての正直な評価

Secrets Manager の主要な付加価値は**自動ローテーション**だが、**X API の認証情報は自動ローテーションに対応していない**ため、この機能は本件では使えない。

したがって Secrets Manager を選ぶ実質的な根拠は、自動ローテーションではなく **リソースポリシー・CMK 連携・CloudTrail 統合による権限分離と監査性** にある（SSM Parameter Store にはリソースポリシーがない）。月 $0.40 はこの監査性への対価と位置づける。

ローテーションは**手動手順として文書化**し、カレンダーリマインダーで年 1 回実施する運用とする。自動化されていないことを明示的に認識しておくこと。

> **コスト上の補足:** 本システムは 1 日 1 回しか実行されないため Lambda の実行環境は毎回コールドスタートになり、シークレットのメモリキャッシュは効かない。`GetSecretValue` は月 30 回程度で、API 呼び出し課金は無視できる。

### 3-3. その他のセキュリティ設定

| リソース | 設定 |
|---|---|
| S3（レポート） | Block Public Access 全4項目有効。CloudFront OAC からのみアクセス可。バケットポリシーで `AWS:SourceArn` をディストリビューション ARN に限定 |
| S3（state） | 上記 + バージョニング + **`state` 用 CMK** で暗号化 + `prevent_destroy` |
| KMS | **用途ごとに 2 キー**（`state` 用 / `secrets` 用）。キーポリシーで復号できるプリンシパルを分離し、CI のデプロイロールが X 認証情報を復号できないようにする |
| CloudFront | HTTPS リダイレクト強制、最小 TLS を `TLSv1.2_2021`、レスポンスヘッダーポリシー（HSTS / `X-Content-Type-Options: nosniff` / `X-Frame-Options`） |
| Lambda | VPC 不要（プライベートリソースへアクセスしないため、VPC に入れると NAT コストが発生するだけ）。**予約済み同時実行数を 2 に設定**（暴走時のコスト上限） |
| IAM | `bedrock-mantle:CreateInference` の Resource をモデル ARN に限定。`s3:PutObject` は該当バケットのみ |
| CloudWatch Logs | prod は保持 30 日（Anthropic の推奨に合わせる）、dev は 7 日 |
| AWS Budgets | prod に月額 $20 の閾値でメール通知（想定の約 3 倍） |

### 3-4. IaC の静的スキャン（任意）

CI に `tfsec` または `checkov` を追加すると、上記設定の抜けを機械的に検出できる。導入は任意だが、Block Public Access の設定漏れのような典型的ミスを拾えるため推奨度は高い。**[Phase 2 で判断]**

---

## 4. GitHub Actions

### 4-1. 認証は OIDC を使う（アクセスキーを GitHub に置かない）

**最重要のセキュリティ判断。**

`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` を GitHub Secrets に保存する方式は**採らない**。長期有効な認証情報がリポジトリ設定に残り、漏洩時の被害が大きく、ローテーションも手動になる。

代わりに **GitHub OIDC プロバイダ** を使い、ワークフロー実行ごとに AWS STS から一時credentialsを取得する。GitHub 側に保存する秘密情報はゼロになる。

```yaml
permissions:
  id-token: write      # OIDC トークン取得に必須
  contents: read
```

### 4-2. 信頼ポリシーの設計（事故が起きやすい箇所）

> ### ⚠️ `sub` 条件を誤ると、任意の GitHub リポジトリから AWS を操作できる
>
> OIDC の信頼ポリシーで `sub` の条件を省略したり、`repo:OWNER/*` のような広いワイルドカードを使うと、**第三者のリポジトリからこのロールを引き受けられる**。OIDC 導入時の典型的な重大設定ミス。
>
> - `aud` は `StringEquals` で `sts.amazonaws.com` に固定する
> - `sub` はリポジトリとブランチ/環境まで**完全一致**で絞る

```json
{
  "Effect": "Allow",
  "Principal": {
    "Federated": "arn:aws:iam::<ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com"
  },
  "Action": "sts:AssumeRoleWithWebIdentity",
  "Condition": {
    "StringEquals": {
      "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
      "token.actions.githubusercontent.com:sub": "repo:<OWNER>/<REPO>:environment:prod"
    }
  }
}
```

### 4-3. IAM ロールは 3 つに分ける

| ロール | 信頼する `sub` | 権限 |
|---|---|---|
| `gha-plan` | `repo:OWNER/REPO:pull_request` | **読み取り専用** + state 読み取り。PR での `terraform plan` 用 |
| `gha-deploy-dev` | `repo:OWNER/REPO:ref:refs/heads/main` | dev リソースへの書き込み |
| `gha-deploy-prod` | `repo:OWNER/REPO:environment:prod` | prod リソースへの書き込み |

`gha-deploy-prod` の `sub` を **`environment:prod`** に紐付けるのが要点。GitHub Environments の保護ルール（必須レビュー）を通過しないとこの `sub` を持つトークンが発行されないため、**IAM レベルで承認ゲートが強制される**。ワークフローファイルを書き換えても迂回できない。

### 4-4. ワークフロー構成

```
.github/workflows/
├── ci.yml            # PR・push: lint / 型チェック / テスト / terraform plan
├── deploy-dev.yml    # main への push: dev へ自動デプロイ
└── deploy-prod.yml   # 手動実行 or タグ: prod へデプロイ（承認必須）
```

#### `ci.yml` — Phase 1 から有効化できる

| ジョブ | 内容 | AWS 認証 |
|---|---|---|
| `python` | `ruff check` / `ruff format --check` / `mypy` / `pytest` | **不要** |
| `security` | `gitleaks` / `pip-audit` / `checkov`（§4-6） | **不要** |
| `terraform` | `terraform fmt -check -recursive` / `init` / `validate` / `plan`（dev） | `gha-plan`（読み取り専用） |

**Phase 1 では `python` ジョブと、`security` ジョブのうち `gitleaks` / `pip-audit` を置く**（`checkov` は `infra/` が存在する Phase 2 から）。 `infra/` がまだ存在しないため `terraform` ジョブは Phase 2 で追加する。テストは Bedrock をモックするので CI で課金は発生しない。

**フォークからの PR について:** `pull_request` トリガーではフォークからの PR に OIDC トークンが発行されないため、`terraform` ジョブは失敗する（スキップ扱いにする）。`pull_request_target` は**使わない** — フォークの変更に対して権限付きで実行されるため、OIDC と組み合わせると危険。

#### `deploy-dev.yml`

```
main への push
  → Lambda zip をビルド
  → 成果物を S3 にアップロード
  → terraform apply（envs/dev、-auto-approve）
  → スモークテスト: aws lambda invoke --payload '{"dry_run": true}'
  → 終了コードとレスポンスを検証
```

スモークテストで `dry_run: true` を渡すのは、**デプロイ検証のたびに Bedrock を呼んで課金されるのを避ける**ため。配線（IAM、S3 書き込み、環境変数）の確認が目的なので、AI 解析は不要。

#### `deploy-prod.yml`

```
workflow_dispatch（手動）または v* タグの push
  → environment: prod  ← ここで必須レビュー承認が入る
  → Lambda zip をビルド
  → terraform plan を出力（ログに残す）
  → terraform apply
  → スモークテスト（dry_run）
```

**prod は自動デプロイしない。** GitHub Environments に必須レビュアーを設定し、人間の承認を挟む。1 日 1 回のバッチシステムであり、デプロイ頻度を上げる必要がないため、安全側に倒す。

### 4-5. Lambda のパッケージング【実測で更新 2026-11-24】

```bash
pip install \
  --platform manylinux2014_x86_64 \
  --only-binary=:all: \
  --python-version 3.13 \
  --target build/ \
  -r requirements-lambda.txt
```

#### ✅ 実測: zip 6.3MB。設計時の懸念は解消済み

| 項目 | 実測 | 上限 |
|---|---|---|
| 展開後 | **29 MB** | 250 MB |
| **zip 圧縮後** | **6.3 MB** | **50 MB（直接アップロード）** |

内訳: `anthropic` 17MB / `pydantic_core` 5MB / `pydantic` 2MB / 他。
**`pandas` / `numpy` は含まれません。**

> ### 設計時の懸念が 2 つとも解消しました
>
> **1. pandas 問題（[01](01-architecture.md) §3-2 / A-17）**
> 「`yfinance` が pandas を引き込み zip が 60MB を超える」ため `MarketDataProvider` を抽象化し
> Phase 2 で軽量実装に差し替える計画でした。**Phase 1 で `HttpChartProvider` を標準ライブラリ
> のみで実装したため、この差し替え作業は既に完了しています。** Phase 2 の作業項目から外れます。
>
> **2. S3 経由デプロイ（A-46）**
> 50MB 制限の回避のために S3 経由を選んでいましたが、**6.3MB なら直接アップロードで十分**です。
> Terraform の `aws_lambda_function` に `filename` + `source_code_hash` を指定する形で構いません。
> S3 経由にしても害はないので、**どちらでもよい**という位置づけに変更します。

**アーキテクチャは x86_64 を採用。** arm64 は単価が約 20% 安いですが Lambda コストは無料枠内で
$0 なので削減額はゼロ。クロスアーキテクチャのホイール取得という複雑さだけが増えます。

**[要確認]** ローカルの Python が 3.12 になったため（3.14 が Homebrew から消えた）、
Lambda ランタイムを **3.12 に揃えるか 3.13 のままにするか**は Phase 2 で判断します。
揃えた方が検証環境との差分が減ります。

### 4-6. セキュリティスキャン【確定 2026-11-22】

「安全・拡張性のある方」という方針により、以下 4 層を CI に組み込む。**すべて無料**で導入できる。

| 層 | ツール | 検出対象 | 実行タイミング |
|---|---|---|---|
| **IaC ポリシー** | **Checkov** | Terraform の設定不備（Block Public Access 漏れ、暗号化なし、過剰な IAM 権限など） | PR / push |
| **秘密情報の誤コミット** | **Gitleaks** | ソース・履歴に混入した API キー・トークン | PR / push + **pre-commit フック** |
| **Python 依存の脆弱性** | **pip-audit** | 依存パッケージの既知 CVE | PR / push + 定期 |
| **依存の更新追従** | **Dependabot**（GitHub 標準） | pip と **GitHub Actions 自体**のバージョン | 自動 PR |

#### ツール選定の理由

**Checkov を選ぶ理由（tfsec ではなく）:** `tfsec` は Trivy に統合される形で開発が移行した **[要確認: 現在の状況]**。Checkov は Terraform 向けポリシーの網羅性が高く、**カスタムポリシーを Python / YAML で書ける**ため、プロジェクト固有のルール（例: 「S3 バケットには必ず特定のタグを付ける」）を後から追加できる。拡張性の観点でこちらを採る。

**Gitleaks を pre-commit にも入れる理由:** CI で検知しても、**その時点で秘密情報は既に GitHub に push されている**。push された秘密は履歴から消すのが困難で、実務上は「漏洩したものとしてローテーションする」しかない。**コミット前に止めることが本質的に重要**。CI 側は pre-commit をバイパスされた場合の保険。

**Dependabot で GitHub Actions も対象にする理由:** Actions のバージョンが古いままだと、脆弱性が修正されたバージョンに追従できない。pip だけでなく `.github/workflows/` も監視対象にする。

#### サプライチェーン対策: Action をコミット SHA で固定する

```yaml
# ❌ タグ指定 — タグは後から書き換え可能
- uses: aws-actions/configure-aws-credentials@v4

# ✅ コミット SHA 指定 — 内容が固定される
- uses: aws-actions/configure-aws-credentials@<40桁のコミットSHA>  # v4.0.2
```

タグはリポジトリの所有者が任意のコミットに貼り替えられるため、**タグ指定は「将来の任意のコードを信頼する」ことに等しい**。OIDC で AWS の認証情報を扱うワークフローでは特に重要。Dependabot は SHA 指定でも更新 PR を作れる（コメントのバージョン表記を併せて更新してくれる）。

#### スキャン失敗時の扱い

| ツール | PR をブロックするか |
|---|---|
| Checkov | **ブロックする**（必須ステータスチェック） |
| Gitleaks | **ブロックする** |
| pip-audit | **警告のみ**（修正版が存在しない CVE で開発が止まるのを避ける） |

pip-audit だけ非ブロックにするのは、上流に修正版が出ていない脆弱性で作業が止まるのを防ぐため。ただし警告は必ず確認し、深刻なものは個別に対応する。

> **偽陽性への対処:** Checkov は本プロジェクトに当てはまらないルール（例: VPC 関連、マルチ AZ 要件）も指摘する。該当箇所に `#checkov:skip=CKV_AWS_xxx:理由` を書いて明示的に除外する。**理由を必ず書く**こと — 無記名の skip は後から判断できなくなる。

### 4-7. ブランチ保護

| 設定 | 値 |
|---|---|
| 保護対象 | `main` |
| 必須ステータスチェック | `ci.yml` の全ジョブ |
| PR 必須 | 有効（直接 push 禁止） |
| force push | 禁止 |

---

## 5. デプロイフロー全体像

```
ローカル開発
  │  python -m market_report.cli --dry-run     ← 反復はここ（AWS不要・課金なし）
  │  pytest
  ▼
PR 作成
  │  ci.yml: lint / mypy / pytest / terraform plan(dev)
  ▼
main へマージ
  │  deploy-dev.yml: dev へ自動 apply → スモークテスト
  │  （必要なら dev の Lambda を手動実行して実データで確認）
  ▼
手動トリガー（workflow_dispatch）
  │  deploy-prod.yml: ★レビュアー承認待ち★
  ▼
prod へ apply → スモークテスト
  │
  ▼
翌朝 07:00 JST に EventBridge Scheduler が本番実行
```

---

## 6. モデル差し替えの手順【Claude API 構成に全面改訂 2026-11-24】

「今後最新のモデルに差し替えられるようにしたい」という要件への対応です。

> **Bedrock 構成よりも単純になりました。** Claude API 直接では **IAM とモデル ID が無関係**
> なため、旧版（§6-2）で警告していた「IAM の Resource が旧モデル ARN のままで `AccessDenied`」
> という落とし穴が**構造的に発生しません**。

### 6-1. モデルIDは Terraform 変数で管理する

モデル ID をコードに埋め込まず、**Terraform 変数 → Lambda 環境変数 `BEDROCK_MODEL_ID`** として
注入します（環境変数名は歴史的な経緯でこの名前ですが、Claude API でも同じ変数を読みます）。
設定の優先順は **環境変数 > `settings.toml`** です。

```hcl
# envs/prod/terraform.tfvars
provider_name = "anthropic"      # MARKET_REPORT_PROVIDER
model_id      = "claude-opus-5"  # BEDROCK_MODEL_ID
```

**差し替えは tfvars の 1 行変更 + apply だけ**で完了します。Python コードの変更も
Lambda zip の再ビルドも不要です。

### 6-2. Bedrock に戻す場合

AWS 側の制約が解消した場合（[07 §A-0-P](07-prerequisites.md)）、同じ仕組みで戻せます。

```hcl
provider_name = "bedrock"
model_id      = "anthropic.claude-opus-5"
```

このとき **Lambda の IAM に `bedrock-mantle:CreateInference` を追加**する必要があります。
`provider` と `model_id` の接頭辞（`anthropic.`）は設定読み込み時に自動で整合させるため
（[05](05-decisions.md) A-121）、取り違えによる 404 は起きません。

### 6-3. 差し替え時のチェックリスト

1. **ローカルで新モデルを試す** — `market-report run --model <新モデルID>`
2. **出力品質を比較** — 同じニュースセットで旧モデルの出力と並べる。記事は全件保存して
   いるため `reanalyze` で**同一入力での比較**ができます（[11 §7](11-data-retention.md)）
3. **トークン消費の変化を確認** — 世代が変わるとトークナイザが変わることがあり、コストが
   変動します。[03](03-cost-and-tokens.md) を更新
4. **API の破壊的変更を確認** — 例: `budget_tokens` は現世代で 400 エラー、`temperature` も
   使用不可（[02](02-bedrock-selection.md)）
5. **`doctor` で疎通確認** — `market-report doctor` が候補モデルの可否を判定します
6. **prod の tfvars を変更して apply**

## 7. Phase ごとの作業範囲

| Phase | CI/CD 関連の作業 |
|---|---|
| **Phase 1** | `git init` + GitHub リポジトリ作成、`ci.yml` の `python` / `security`（gitleaks + pip-audit）ジョブ、pre-commit フック、Dependabot 設定、ブランチ保護設定 |
| **Phase 2** | **§2-0 の権限検証スパイク**（最初）、`infra/bootstrap` の手動 apply（OIDC + state バケット + KMS 2 キー + 3 ロール）、`modules/` と `envs/` の記述、**`lambda_handler.py`（Secrets Manager からの API キー取得を含む）**、prod に `ANTHROPIC_API_KEY` を手動投入、`ci.yml` に `terraform` / `checkov` 追加、`deploy-dev.yml`、`deploy-prod.yml` |
| **Phase 3** | prod に X 用シークレットを追加（2 個目）、X 認証情報を手動投入、CloudTrail データイベント設定 |

---

## 8. 🔬 S0 権限検証スパイクの結果（2026-11-25 実施）

`infra/spike/` で 17 リソースを実際に作成し、AWS 管理の SCP に拒否されないかを確認しました。

### 結果: 16/17 成功

| # | 検証対象 | 結果 |
|---|---|---|
| 1 | S3 バケット + Block Public Access + **バケットポリシー** | ✅ |
| 2 | **KMS CMK** + エイリアス | ✅ |
| 3 | **IAM ロール ×3** + インラインポリシー | ✅ |
| 4 | **GitHub OIDC プロバイダ** + 引き受けロール | ✅ |
| 5 | Secrets Manager | ✅ |
| 6 | Lambda 関数 | ✅ |
| 7 | EventBridge Scheduler（`Asia/Tokyo`） | ✅ |
| 8 | CloudFront OAC | ✅ |
| **8'** | **CloudFront ディストリビューション** | ❌ |

**未検証だった 4 権限（OIDC / IAM ロール / KMS / バケットポリシー）はすべて通りました。**
Phase 2 の設計は、配信層を除いてそのまま使えます。

### ❌ CloudFront の失敗理由（SCP ではない）

```
AccessDenied: Your account must be verified before you can add new CloudFront
resources. To verify your account, please contact AWS Support.
```

**新規アカウントに対する不正利用対策の検証**であり、SCP による恒久的な制約ではありません。
AWS サポートへの問い合わせで解除できますが、**待ち時間が発生するため代替手段を採用します**。

### ⚠️ 副次的な発見: `aws login` の認証情報は Terraform から読めない

公式ドキュメントの「External tools may not be supported」が実際に発生しました。

```
Error: No valid credential sources found
```

`~/.aws/config` の `login_session` は AWS CLI 独自のキーで、Terraform の AWS SDK は
理解しません。**`credential_process` で橋渡しして解決**しました。

```ini
[profile terraform]
region = ap-southeast-2
credential_process = aws configure export-credentials --profile default --format process
```

AWS CLI に標準形式へ変換させる方式です。`aws login` のセッションが有効な間は自動更新され、
以後は `export AWS_PROFILE=terraform` だけで Terraform が動きます。

### 後片付け

16 リソースすべて `terraform destroy` 済み。残存は **KMS キー 1 個が 7 日の削除待機中**
（約 $0.25）のみ。AWS の仕様で短縮できません。

---

## 9. ✅ 配信層の決定: AWS Amplify Hosting（2026-11-25 検証）

CloudFront の代替として **AWS Amplify Hosting** を実機検証し、採用を決めました。

### 検証結果

実際のレポート HTML をデプロイし、以下を確認しました。

| 確認項目 | 結果 |
|---|---|
| アプリ / ブランチの作成 | ✅ CloudFront の 403 は出ない |
| 手動デプロイ（zip アップロード） | ✅ `SUCCEED` |
| **HTTPS 配信** | ✅ `https://main.<appId>.amplifyapp.com` |
| **サブディレクトリ**（`/reports/YYYY-MM-DD.html`） | ✅ 200 |
| `/archive.html` | ✅ 200 |
| **HTTP → HTTPS リダイレクト** | ✅ 301 |

レスポンスヘッダーは `x-cache: Hit from cloudfront` / `server: AmazonS3`。
**内部では CloudFront + S3 が使われており、AWS 管理のディストリビューションのため
アカウント検証を要求されません。** 自前で作れなかった構成を Amplify 経由で使えます。

### なぜ Amplify を選ぶか

| 比較 | Amplify | GitHub Pages | Cloudflare Pages | S3 静的ホスティング |
|---|---|---|---|---|
| HTTPS | ✅ | ✅ | ✅ | ❌ **HTTP のみ** |
| 費用 | 無料枠 | 無料 | 無料 | 無料枠 |
| **AWS 内で完結** | ✅ | ❌ | ❌ | ✅ |
| ベンダーの増加 | なし | なし（既存） | **あり** | なし |
| 独自ドメイン | ✅ | ✅ | ✅ | △ |
| 実機検証 | ✅ **済** | 未 | 未 | — |

**S3 静的ホスティングは HTTPS が使えない**ため除外（X に `http://` のリンクを貼ることになり、
Block Public Access を解除する必要もあってセキュリティ設計と衝突します）。

GitHub Pages / Cloudflare Pages も有力でしたが、**Amplify なら AWS 内で完結し、
請求先・IAM・Terraform の管理対象が増えません**。

### 設計への影響

**変更は配信層だけです。** S3 は `raw/` `data/` の保管用として検証済みのまま使います。

```
raw/ data/ の保管   → S3（変更なし）
HTML の公開配信      → CloudFront → **Amplify Hosting**
```

| 項目 | 変更前 | 変更後 |
|---|---|---|
| Terraform リソース | `aws_cloudfront_distribution` + OAC + バケットポリシー | `aws_amplify_app` + `aws_amplify_branch` |
| Lambda の処理 | S3 に HTML を PUT | S3 に HTML を PUT **+ Amplify へデプロイ** |
| 必要な IAM | `s3:PutObject` | `s3:PutObject` + `amplify:CreateDeployment` / `StartDeployment` |
| dev 環境 | CloudFront を置いて OAC を検証 | **Amplify アプリを dev にも置く**（構成差を作らない方針は維持） |

### 残る検証項目

| # | 内容 |
|---|---|
| 1 | **Terraform の `aws_amplify_app` / `aws_amplify_branch` が作成できるか**（CLI では確認済み） |
| 2 | **Lambda から Amplify へデプロイできるか**（`CreateDeployment` → zip アップロード → `StartDeployment`） |
| 3 | ~~存在しないパスが 301 を返す挙動の調整~~ → ✅ **不要と判明**（2026-11-25）。`301 /nope/` を 1 回経て **404** で終わる。Amplify 既定の pretty-URL 挙動で、`customRules` は要らない |
| 4 | 独自ドメインの設定（当面はデフォルトドメインを使用） |

### 追加で判明したこと（2026-11-25 / 詳細は [13 §8](13-phase2-plan.md)）

| # | 内容 |
|---|---|
| **R-3** | **手動デプロイはサイト全体の置き換え。** `--file-map` に宣言してアップロードしなかったファイルは**消える**（md5 は「据え置き」の印にならない）。毎回、全レポートを含む zip を送る必要がある。実測 HTML 96KB → zip 33KB（約 3 倍圧縮）で、5 年分でも zip 約 13MB |
| **R-7** | `aws_amplify_app` は **AWS プロバイダ 5.x で無言ハング**。`~> 6.0` に固定する（§2-5 を改訂済み）。`CreateApp` のレート上限は厳しく、**`destroy` → `apply` の繰り返しはスロットリングされる** |
| 残 1 | **Terraform での作成は未確認。** 上記スロットリングのため検証が完了していない（CLI 経路は ✅ 確認済み）。時間を置いて再試行する |
