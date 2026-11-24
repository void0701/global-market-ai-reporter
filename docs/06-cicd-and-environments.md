# 06. CI/CD（GitHub Actions）と環境分離

最終更新: 2026-11-22
対象フェーズ: **Phase 2 以降**（ただし Python の CI ジョブのみ Phase 1 から有効化できる）

## 0. 前提: 未整備の項目

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
| Secrets Manager（X API） | **なし** | あり | 1-1 の理由 |
| CloudWatch Logs | あり（保持 7日） | あり（保持 30日） | dev は短く |
| AWS Budgets アラート | なし | あり | |

**dev にも CloudFront を置く理由:** S3 の Block Public Access + OAC（Origin Access Control）+ バケットポリシーの組み合わせは設定ミスが起きやすい箇所で、ここを dev で検証できないと prod で初めて壊れる。構成の差分は検証価値を下げるため、同じモジュールを両環境に適用する。コストはどちらも無料枠内。

**ただし注意:** CloudFront の作成・更新は 5〜15 分かかる。dev での `terraform apply` が遅くなるため、**日常的な反復は Phase 1 の `--dry-run`（ローカル実行）で行い、`dev` 環境はインフラ変更の検証に限って使う**。この使い分けを守らないと開発速度が落ちる。

---

## 2. Terraform の構成

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
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}
```

`.terraform.lock.hcl` は**コミットする**。CI とローカルでプロバイダのバージョンが揺れるのを防ぐ。

---

## 3. セキュリティ強化（B-4 の実装）

B-4 で「できるだけ強固な方」という方針をいただいたため、Secrets Manager を採用する。ただし **Secrets Manager を選ぶだけでは強固にならない**。以下をセットで実装する。

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

### 4-5. Lambda のパッケージング

```bash
pip install \
  --platform manylinux2014_x86_64 \
  --only-binary=:all: \
  --python-version 3.13 \
  --target build/ \
  -r requirements-lambda.txt
```

**アーキテクチャは x86_64 を採用。** arm64（Graviton）は単価が約 20% 安いが、本システムの Lambda コストは**無料枠内で $0**（[03](03-cost-and-tokens.md)）なので削減額はゼロ。一方 arm64 はクロスアーキテクチャのホイール取得を必要とし、ビルドの複雑さだけが増える。**コスト削減効果がない最適化は採らない。**

**zip は S3 経由でデプロイする**（`aws_lambda_function` の `s3_bucket` / `s3_key` + `source_code_hash`）。直接アップロードの 50MB 制限を構造的に回避でき、yfinance（pandas 依存）を含めることになった場合でも詰まらない。

**[要確認]** より厳格な再現性が必要なら `public.ecr.aws/lambda/python:3.13` コンテナ内でビルドする方式に切り替える。Phase 2 で `--platform` 指定の結果を実機確認し、問題があれば移行する。

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

## 6. モデル差し替えの手順（B-1 の要件）

「今後最新のモデルに差し替えられるようにしておきたい」というご要望に対する設計。

### 6-1. モデルIDは Terraform 変数で管理する

モデルIDをコードに埋め込まず、**Terraform 変数 → Lambda 環境変数 `BEDROCK_MODEL_ID`** として注入する。`settings.toml` の値は環境変数で上書きされる優先順とする。

```hcl
# envs/prod/terraform.tfvars
bedrock_model_id = "anthropic.claude-opus-5"

# IAM の Resource もこの変数から生成する
allowed_model_ids = ["anthropic.claude-opus-5"]
```

これにより **モデル差し替えは tfvars の 2 行変更 + apply だけ**で完了する。Python コードの変更も Lambda zip の再ビルドも不要。

### 6-2. IAM とモデルIDを同じ変数から生成する

**よくある落とし穴:** Lambda の環境変数だけ新モデルに変えても、IAM の `bedrock-mantle:CreateInference` の Resource が旧モデル ARN に固定されていると `AccessDeniedException` になる。

これを防ぐため、`allowed_model_ids` 変数から環境変数と IAM ポリシーの両方を生成する。移行期間中は新旧両方を列挙できる。

```hcl
allowed_model_ids = ["anthropic.claude-opus-5", "anthropic.claude-opus-5-5"]
bedrock_model_id  = "anthropic.claude-opus-5-5"   # 実際に使うもの
```

### 6-3. 差し替え時のチェックリスト

新しいモデルに切り替える際、以下を順に確認する。

1. **Bedrock コンソールでそのモデルのアクセスを有効化**（新モデルは個別申請が必要な場合がある）
2. **API の破壊的変更を確認** — 世代が変わるとパラメータが変わることがある（例: `budget_tokens` は現世代で 400 エラー、`temperature` も現世代では使用不可。[02](02-bedrock-selection.md) 参照）
3. **`allowed_model_ids` に新モデルを追加**して先に apply（IAM を先に広げる）
4. **dev で実行して出力品質を比較** — 同じニュースセットで旧モデルの出力と並べて確認する
5. **トークン消費量の変化を確認** — 新世代はトークナイザが変わることがあり、コストが変動する。[03](03-cost-and-tokens.md) を更新する
6. **prod の `bedrock_model_id` を変更して apply**
7. 旧モデルを `allowed_model_ids` から外す

ステップ 4 のために、**同じ入力で複数モデルを走らせて出力を比較できる CLI オプション**を Phase 1 で用意しておく価値が高い（`--model` でモデルIDを上書きできるようにする）。

---

## 7. Phase ごとの作業範囲

| Phase | CI/CD 関連の作業 |
|---|---|
| **Phase 1** | `git init` + GitHub リポジトリ作成、`ci.yml` の `python` / `security`（gitleaks + pip-audit）ジョブ、pre-commit フック、Dependabot 設定、ブランチ保護設定 |
| **Phase 2** | `infra/bootstrap` の手動 apply（OIDC + state バケット + **KMS 2 キー** + 3 ロール）、`modules/` と `envs/` の記述、`ci.yml` に `terraform` / `checkov` 追加、`deploy-dev.yml`、`deploy-prod.yml` |
| **Phase 3** | prod のみに `secrets` モジュールを追加、X 認証情報を手動投入、CloudTrail データイベント設定 |
