# site — データ保管（S3）と公開配信（Amplify Hosting）
#
# **CloudFront を自前で作らない。** このアカウントでは
# `CreateDistribution` がアカウント未検証で 403 になる（docs/06 §8）。
# Amplify Hosting は内部で AWS 管理の CloudFront + S3 を使うため、
# 同じ構成を検証なしで利用できる（A-131 / docs/06 §9）。
#
# S3 は `raw/` `data/` `reports/` の保管用として引き続き使う。
# **公開はしない**（Block Public Access 全 4 項目を有効のまま）。

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

locals {
  name = "${var.project}-${var.env}"
}

# ---------------------------------------------------------------------------
# データ保管バケット
#   公開配信は Amplify が担うため、このバケットは非公開のまま。
#   OAC もバケットポリシーでの公開も不要になった（CloudFront をやめたため）。
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "data" {
  bucket = "${local.name}-reports-${var.account_id}"
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id
  versioning_configuration {
    # 生データは最大保持の方針（docs/11）。誤った上書きからも戻せるようにする
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    apply_server_side_encryption_by_default {
      # 公開ニュースと公開レポートのみなので SSE-S3 で足りる。
      # CMK は state と secrets に限る（キーポリシーを単純に保つ）
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_policy" "tls_only" {
  bucket = aws_s3_bucket.data.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource = [
        aws_s3_bucket.data.arn,
        "${aws_s3_bucket.data.arn}/*",
      ]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  # **現行オブジェクトは消さない。** 後から再解析するために全件保持する
  # 方針（docs/11 / A-84）。古いバージョンだけ整理する。
  rule {
    id     = "expire-noncurrent-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_version_days
    }
  }
}

# ---------------------------------------------------------------------------
# Amplify Hosting
#   Git 連携はしない。Lambda が毎朝サイト全体を zip で手動デプロイする
#   （A-141 / docs/13 §8 R-3）。
# ---------------------------------------------------------------------------

resource "aws_amplify_app" "site" {
  name     = local.name
  platform = "WEB"

  # リポジトリを繋がない = ビルドも走らない。静的 HTML を送るだけ
  enable_branch_auto_build    = false
  enable_branch_auto_deletion = false
  enable_basic_auth           = false

  # **custom_rule は意図的に置かない。**
  # 存在しないパスは `301 /nope/` を 1 回経て 404 で終わることを実機で
  # 確認済み（docs/13 §8 R-10）。SPA 用のフォールバックを入れると
  # 404 が 200 になってしまい、検証済みの挙動を壊す。
}

resource "aws_amplify_branch" "main" {
  app_id      = aws_amplify_app.site.id
  branch_name = var.branch
  stage       = var.env == "prod" ? "PRODUCTION" : "DEVELOPMENT"

  enable_auto_build = false
}
