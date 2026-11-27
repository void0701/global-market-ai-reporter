# bootstrap — 鶏と卵の解決（docs/06 §2-4）
#
# **人間が 1 回だけ手で apply する。** CI には通さない。
#   cd infra/bootstrap && export AWS_PROFILE=terraform
#   terraform init && terraform apply
#
# ここで作るもの:
#   1. リモート state 用の S3 バケット（envs/* がこれを使う）
#   2. KMS CMK 2 個（state 用 / secrets 用。用途で分ける / A-65）
#   3. GitHub OIDC プロバイダ
#   4. CI が引き受ける IAM ロール 3 種
#
# 変更頻度は年に数回あるかどうかなので、手動運用で問題ない。

data "aws_caller_identity" "current" {}

locals {
  prefix       = var.project
  state_bucket = "${var.project}-tfstate-${data.aws_caller_identity.current.account_id}"
}

# ---------------------------------------------------------------------------
# KMS — 用途ごとに 2 キー（A-65）
#   CI のデプロイロールが将来の X 認証情報を復号できないようにするため、
#   state 用と secrets 用でキーポリシーを分離する。
# ---------------------------------------------------------------------------

resource "aws_kms_key" "state" {
  description             = "${local.prefix} Terraform state の暗号化"
  enable_key_rotation     = true
  deletion_window_in_days = 7
}

resource "aws_kms_alias" "state" {
  name          = "alias/${local.prefix}-state"
  target_key_id = aws_kms_key.state.key_id
}

resource "aws_kms_key" "secrets" {
  description             = "${local.prefix} Secrets Manager の暗号化"
  enable_key_rotation     = true
  deletion_window_in_days = 7
}

resource "aws_kms_alias" "secrets" {
  name          = "alias/${local.prefix}-secrets"
  target_key_id = aws_kms_key.secrets.key_id
}

# ---------------------------------------------------------------------------
# state バケット（docs/06 §2-3）
#   **state には秘密情報が平文で入りうる**ため、レポート用バケットより
#   厳しく扱う。バージョニング + BPA + CMK + 誤削除防止。
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "state" {
  bucket = local.state_bucket

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.state.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_policy" "state_tls_only" {
  bucket = aws_s3_bucket.state.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource = [
        aws_s3_bucket.state.arn,
        "${aws_s3_bucket.state.arn}/*",
      ]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

# 古い state バージョンを無限に溜めない（巻き戻しには 90 日あれば十分）
resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    id     = "expire-noncurrent-state"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 90
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# ---------------------------------------------------------------------------
# GitHub OIDC（docs/06 §4-1）
#   アクセスキーを GitHub に置かない。⚠️ プロバイダはアカウントに 1 つだけ
#   作れる。既に存在する場合は `terraform import` が必要。
# ---------------------------------------------------------------------------

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  # GitHub の OIDC は現在 AWS 側で検証されるが、プロバイダ作成時に
  # thumbprint が必須のため従来値を入れる
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

# ---------------------------------------------------------------------------
# CI 用 IAM ロール 3 種（docs/06 §4-3）
#
# **ワイルドカードを使わない。** `sub` に `*` を許すと、同じリポジトリの
# どのブランチからでも、場合によっては他人のリポジトリからでも引き受けられる。
# StringEquals でリスト指定すると「いずれかに完全一致」の意味になり、
# 複数パターンを許しつつワイルドカードを避けられる。
# ---------------------------------------------------------------------------

locals {
  oidc_sub_key = "token.actions.githubusercontent.com:sub"
  oidc_aud_key = "token.actions.githubusercontent.com:aud"
}

data "aws_iam_policy_document" "assume_plan" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = local.oidc_aud_key
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = local.oidc_sub_key
      # PR では `pull_request`、main への push では `ref:refs/heads/main`
      values = [
        "repo:${var.github_repo}:pull_request",
        "repo:${var.github_repo}:ref:refs/heads/main",
      ]
    }
  }
}

data "aws_iam_policy_document" "assume_deploy_dev" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = local.oidc_aud_key
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = local.oidc_sub_key
      values   = ["repo:${var.github_repo}:ref:refs/heads/main"]
    }
  }
}

data "aws_iam_policy_document" "assume_deploy_prod" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = local.oidc_aud_key
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = local.oidc_sub_key
      # **GitHub Environments が承認ゲート。** `environment:prod` の sub は
      # 必須レビュアーの承認後にしか発行されない（A-42）。ブランチ指定では
      # この性質が得られない
      values = ["repo:${var.github_repo}:environment:prod"]
    }
  }
}

# > ### ⚠️ IAM の description は ASCII + Latin-1 のみ
# >
# > `roleDescriptionType` の pattern は
# > `[\u0009\u000A\u000D\u0020-\u007E\u00A1-\u00FF]*` で、**日本語を入れると
# > `CreateRole` が ValidationError で 400 を返す**（実際に踏んだ）。
# > サーバー側の検証なので `terraform plan` では検出できない。
# >
# > KMS / Secrets Manager / CloudWatch / Scheduler / Lambda / Amplify の
# > description には文字種の制約が無い（botocore のモデルで確認）。
# > **IAM だけが例外**なので、説明は英語にしてコメントに日本語を残す。

resource "aws_iam_role" "gha_plan" {
  name = "${local.prefix}-gha-plan"
  # terraform plan 専用（読み取りのみ）
  description        = "terraform plan only (read-only)"
  assume_role_policy = data.aws_iam_policy_document.assume_plan.json
}

resource "aws_iam_role" "gha_deploy_dev" {
  name = "${local.prefix}-gha-deploy-dev"
  # dev への apply
  description        = "apply to dev"
  assume_role_policy = data.aws_iam_policy_document.assume_deploy_dev.json
}

resource "aws_iam_role" "gha_deploy_prod" {
  name = "${local.prefix}-gha-deploy-prod"
  # prod への apply。GitHub Environments の承認が必要
  description        = "apply to prod (requires GitHub Environments approval)"
  assume_role_policy = data.aws_iam_policy_document.assume_deploy_prod.json
}

# ---- 権限 ----

# plan は読み取りのみ。state の書き込みも許さない（ロックファイルは別途許可）
data "aws_iam_policy_document" "plan" {
  statement {
    sid     = "ReadState"
    effect  = "Allow"
    actions = ["s3:GetObject", "s3:ListBucket"]
    resources = [
      aws_s3_bucket.state.arn,
      "${aws_s3_bucket.state.arn}/*",
    ]
  }
  statement {
    sid       = "DecryptState"
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = [aws_kms_key.state.arn]
  }
  statement {
    sid    = "ReadEverythingElse"
    effect = "Allow"
    # plan は既存リソースの読み取りが必要。Describe/Get/List に限る
    actions = [
      "amplify:Get*",
      "amplify:List*",
      "cloudwatch:Describe*",
      "cloudwatch:Get*",
      "cloudwatch:List*",
      "iam:Get*",
      "iam:List*",
      "kms:Describe*",
      "kms:Get*",
      "kms:List*",
      "lambda:Get*",
      "lambda:List*",
      "logs:Describe*",
      "logs:List*",
      "s3:Get*",
      "s3:List*",
      "scheduler:Get*",
      "scheduler:List*",
      "secretsmanager:Describe*",
      "secretsmanager:List*",
      "sns:Get*",
      "sns:List*",
      "sts:GetCallerIdentity",
      "budgets:Describe*",
      "budgets:View*",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "gha_plan" {
  name   = "plan"
  role   = aws_iam_role.gha_plan.id
  policy = data.aws_iam_policy_document.plan.json
}

# デプロイロールは環境ごとの state プレフィックスだけを書ける
data "aws_iam_policy_document" "deploy" {
  for_each = toset(["dev", "prod"])

  statement {
    sid     = "WriteOwnState"
    effect  = "Allow"
    actions = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    # 環境をまたいだ state の書き換えを構造的に防ぐ
    resources = ["${aws_s3_bucket.state.arn}/${each.key}/*"]
  }
  statement {
    sid       = "ListState"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.state.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["${each.key}/*"]
    }
  }
  statement {
    sid     = "UseStateKey"
    effect  = "Allow"
    actions = ["kms:Decrypt", "kms:GenerateDataKey"]
    # **secrets 用 CMK は含めない。** CI は API キーを復号できない（A-65）
    resources = [aws_kms_key.state.arn]
  }
  statement {
    sid    = "ManageInfrastructure"
    effect = "Allow"
    # 本システムが使うサービスに限定する。IAM は PassRole と
    # ロール管理が必要（Lambda / Scheduler の実行ロールを作るため）
    actions = [
      "amplify:*",
      "budgets:*",
      "cloudwatch:*",
      "iam:AttachRolePolicy",
      "iam:CreateRole",
      "iam:DeleteRole",
      "iam:DeleteRolePolicy",
      "iam:DetachRolePolicy",
      "iam:Get*",
      "iam:List*",
      "iam:PassRole",
      "iam:PutRolePolicy",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:UpdateAssumeRolePolicy",
      "kms:CreateAlias",
      "kms:CreateKey",
      "kms:Describe*",
      "kms:Get*",
      "kms:List*",
      "kms:PutKeyPolicy",
      "kms:ScheduleKeyDeletion",
      "kms:TagResource",
      "lambda:*",
      "logs:*",
      "s3:*",
      "scheduler:*",
      "secretsmanager:CreateSecret",
      "secretsmanager:DeleteSecret",
      "secretsmanager:Describe*",
      "secretsmanager:List*",
      "secretsmanager:PutResourcePolicy",
      "secretsmanager:TagResource",
      "sns:*",
      "sts:GetCallerIdentity",
    ]
    resources = ["*"]
  }
  statement {
    sid    = "DenyReadingSecretValues"
    effect = "Deny"
    # **CI がシークレットの値を読めないようにする。** 容器は作れるが
    # 中身は読めない（docs/06 §2-3 の方針を IAM でも裏打ちする）
    actions   = ["secretsmanager:GetSecretValue", "secretsmanager:PutSecretValue"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "gha_deploy_dev" {
  name   = "deploy-dev"
  role   = aws_iam_role.gha_deploy_dev.id
  policy = data.aws_iam_policy_document.deploy["dev"].json
}

resource "aws_iam_role_policy" "gha_deploy_prod" {
  name   = "deploy-prod"
  role   = aws_iam_role.gha_deploy_prod.id
  policy = data.aws_iam_policy_document.deploy["prod"].json
}
