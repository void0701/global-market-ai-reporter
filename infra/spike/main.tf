# 権限の検証スパイク（docs/06 §2-0 / docs/05 A-124）
#
# ====================================================================
#  ✅ 完了済み（2026-11-25）。**再実行しないこと。**
#
#  結果は docs/06 §8 にある（16/17 成功）。AWS 上のリソースは
#  destroy 済み。何を検証したかの記録として残しているだけで、
#  本番の構成は infra/bootstrap と infra/modules を参照する。
#
#  再実行すると:
#    - CloudFront はアカウント未検証で 403 になる（§8 のとおり）
#    - プロバイダが `~> 5.0` 固定のままで、Amplify 系を足すと
#      レート制限のリトライで無言ハングする（§9 / R-7）
#  checkov も `.checkov.yaml` の skip-path で除外している。
# ====================================================================
#
# 目的: このAWSアカウント（Sign up for AWS (new) = プロジェクト方式）で、
#       Phase 2 に必要な操作が AWS 管理の SCP に拒否されないかを確認する。
#
# これまでに 2 回、このアカウントの制約で作業が止まっている:
#   - AWS Marketplace が使えず Bedrock のモデル利用契約を結べない
#   - リージョンを変更できない
# 本番の HCL を書く前に、残る未検証事項をここで潰す。
#
# 使い方:
#   cd infra/spike
#   terraform init
#   terraform apply        # ← 何が拒否されるかを見る
#   terraform destroy      # ← 確認後は必ず消す（料金が発生するため）
#
# state はローカル（このディレクトリ）に置く。bootstrap より前の段階なので
# リモート state はまだ存在しない。

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "s-and-p"
      ManagedBy = "terraform"
      Purpose   = "permission-spike"
    }
  }
}

variable "region" {
  description = "プロジェクトのリージョン（新体験では変更不可）"
  type        = string
  default     = "ap-southeast-2"
}

variable "github_repo" {
  description = "OIDC の信頼ポリシーで許可する GitHub リポジトリ owner/repo"
  type        = string
  default     = "void0701/global-market-ai-reporter"
}

locals {
  prefix = "s-and-p-spike"
}

data "aws_caller_identity" "current" {}

# ---------------------------------------------------------------------------
# 検証 1: S3 バケットの作成 + Block Public Access + バケットポリシー
#   本番では state 置き場とレポート配信に使う
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "probe" {
  bucket        = "${local.prefix}-${data.aws_caller_identity.current.account_id}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "probe" {
  bucket                  = aws_s3_bucket.probe.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "probe" {
  bucket = aws_s3_bucket.probe.id
  versioning_configuration {
    status = "Enabled"
  }
}

# s3:PutBucketPolicy が許可されるかの確認。
# Block Public Access を有効にしたうえで、自アカウントのみに限定する無害なポリシー。
resource "aws_s3_bucket_policy" "probe" {
  bucket = aws_s3_bucket.probe.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource = [
        aws_s3_bucket.probe.arn,
        "${aws_s3_bucket.probe.arn}/*",
      ]
      Condition = {
        Bool = { "aws:SecureTransport" = "false" }
      }
    }]
  })
  depends_on = [aws_s3_bucket_public_access_block.probe]
}

# ---------------------------------------------------------------------------
# 検証 2: KMS 顧客管理キーの作成 + キーポリシー
#   本番では state 用 / シークレット用の 2 キーを作る
#   ⚠️ 削除は 7 日の待機期間がある（deletion_window_in_days の最小値）
# ---------------------------------------------------------------------------

resource "aws_kms_key" "probe" {
  description             = "s-and-p permission spike (safe to delete)"
  deletion_window_in_days = 7
  enable_key_rotation     = true
}

resource "aws_kms_alias" "probe" {
  name          = "alias/${local.prefix}"
  target_key_id = aws_kms_key.probe.key_id
}

# ---------------------------------------------------------------------------
# 検証 3: IAM ロールの作成 + インラインポリシー
#   本番では Lambda 実行ロールと GitHub Actions 用ロール 3 種を作る
# ---------------------------------------------------------------------------

resource "aws_iam_role" "probe_lambda" {
  name = "${local.prefix}-lambda"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "probe_lambda" {
  name = "probe"
  role = aws_iam_role.probe_lambda.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:PutObject"]
      Resource = "${aws_s3_bucket.probe.arn}/*"
    }]
  })
}

# ---------------------------------------------------------------------------
# 検証 4: GitHub OIDC プロバイダ + 引き受けロール
#   本番では CI からアクセスキーなしでデプロイするために使う（docs/06 §4-1）
#   ⚠️ OIDC プロバイダはアカウントに 1 つ。既に存在する場合は import が必要
# ---------------------------------------------------------------------------

resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

resource "aws_iam_role" "probe_gha" {
  name = "${local.prefix}-gha"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.github.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        # aud / sub を完全一致で絞る（docs/06 §4-2 の重大な設定ミス対策）
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = "repo:${var.github_repo}:ref:refs/heads/main"
        }
      }
    }]
  })
}

# ---------------------------------------------------------------------------
# 検証 5: Secrets Manager の「容器」の作成
#   値は Terraform で管理しない（state に平文で残るため / docs/06 §2-3）
# ---------------------------------------------------------------------------

resource "aws_secretsmanager_secret" "probe" {
  name                    = "${local.prefix}-probe"
  kms_key_id              = aws_kms_key.probe.id
  recovery_window_in_days = 0 # 検証用のため即時削除可にする
}

# ---------------------------------------------------------------------------
# 検証 6: Lambda 関数の作成（最小）
# ---------------------------------------------------------------------------

data "archive_file" "probe_lambda" {
  type        = "zip"
  output_path = "${path.module}/.probe_lambda.zip"
  source {
    content  = "def handler(event, context):\n    return {'ok': True}\n"
    filename = "handler.py"
  }
}

resource "aws_lambda_function" "probe" {
  function_name    = "${local.prefix}-fn"
  role             = aws_iam_role.probe_lambda.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  filename         = data.archive_file.probe_lambda.output_path
  source_code_hash = data.archive_file.probe_lambda.output_base64sha256
  timeout          = 10
}

# ---------------------------------------------------------------------------
# 検証 7: EventBridge Scheduler（Asia/Tokyo タイムゾーン指定）
# ---------------------------------------------------------------------------

resource "aws_iam_role" "probe_scheduler" {
  name = "${local.prefix}-scheduler"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "probe_scheduler" {
  name = "invoke"
  role = aws_iam_role.probe_scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = aws_lambda_function.probe.arn
    }]
  })
}

resource "aws_scheduler_schedule" "probe" {
  name                         = "${local.prefix}-schedule"
  schedule_expression          = "cron(0 7 ? * TUE-SAT *)"
  schedule_expression_timezone = "Asia/Tokyo"
  state                        = "DISABLED" # 検証用なので起動させない

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.probe.arn
    role_arn = aws_iam_role.probe_scheduler.arn
    input    = jsonencode({ report_type = "daily", dry_run = true })
  }
}

# ---------------------------------------------------------------------------
# 検証 8: CloudFront + OAC（作成に 5〜15 分かかる）
#   コストは無料枠内。S3 を OAC 経由でのみ公開できるかを確認する
# ---------------------------------------------------------------------------

resource "aws_cloudfront_origin_access_control" "probe" {
  name                              = "${local.prefix}-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "probe" {
  enabled             = true
  default_root_object = "index.html"
  comment             = "s-and-p permission spike"
  price_class         = "PriceClass_200"

  origin {
    domain_name              = aws_s3_bucket.probe.bucket_regional_domain_name
    origin_id                = "s3"
    origin_access_control_id = aws_cloudfront_origin_access_control.probe.id
  }

  default_cache_behavior {
    target_origin_id       = "s3"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    # CachingOptimized（AWS 管理ポリシー）
    cache_policy_id = "658327ea-f89d-4fab-a63d-7e88639e58f6"
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
    minimum_protocol_version       = "TLSv1"
  }
}

# ---------------------------------------------------------------------------
# 出力
# ---------------------------------------------------------------------------

output "result" {
  description = "ここまで表示されれば、Phase 2 に必要な操作はすべて許可されている"
  value = {
    account_id    = data.aws_caller_identity.current.account_id
    region        = var.region
    s3_bucket     = aws_s3_bucket.probe.id
    kms_key       = aws_kms_alias.probe.name
    iam_roles     = [aws_iam_role.probe_lambda.name, aws_iam_role.probe_gha.name]
    oidc_provider = aws_iam_openid_connect_provider.github.arn
    secret        = aws_secretsmanager_secret.probe.name
    lambda        = aws_lambda_function.probe.function_name
    scheduler     = aws_scheduler_schedule.probe.name
    cloudfront    = aws_cloudfront_distribution.probe.domain_name
    next_step     = "terraform destroy で後片付けしてください"
  }
}
