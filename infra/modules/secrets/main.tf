# secrets — Secrets Manager の「容器」だけを作る
#
# > ### ⚠️ 値は Terraform で管理しない（docs/06 §2-3 / A-33）
# >
# > `aws_secretsmanager_secret_version` を書くと **state ファイルに平文で
# > 記録される**。Secrets Manager を選んで強固にしたつもりが state 経由で
# > 漏れる、という本末転倒になる。
# >
# > 値の投入は人間が 1 回だけ AWS CLI から行う:
# >
# > ```bash
# > aws secretsmanager put-secret-value \
# >   --secret-id <このモジュールの出力 secret_id> \
# >   --secret-string file://./key.json
# > rm key.json   # ← リポジトリ外に作り、投入後すぐ消す
# > ```
#
# **prod だけが呼ぶ。** dev にはシークレットを作らない（A-28 / A-140）。

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
  name = "${var.project}-${var.env}-anthropic-api-key"

  # > ### なぜロール名から 2 形式を組み立てるのか
  # >
  # > `aws:PrincipalArn` がロール ARN を返すのかセッション ARN を返すのかは
  # > 条件によって変わる。推測で片方だけ書くと、**値を投入した本人が
  # > 読めなくなる**（または Deny が効かない）。
  # >
  # > セッション名は毎回変わりうるため、`ArnNotLike` で末尾だけワイルドカードに
  # > する。ワイルドカードの範囲は 1 つのロールのセッション名に限られ、
  # > 「誰でも読める」にはならない。
  admin_arns = [
    "arn:aws:iam::${var.account_id}:role/${var.admin_role_name}",
    "arn:aws:sts::${var.account_id}:assumed-role/${var.admin_role_name}/*",
  ]
}

resource "aws_secretsmanager_secret" "anthropic" {
  name        = local.name
  description = "Claude API キー。値は手動投入する（Terraform では管理しない）"
  kms_key_id  = var.kms_key_arn

  # 誤削除しても 7 日は戻せる。0 にすると即時削除で復旧不能
  recovery_window_in_days = 7
}

# リソースポリシーで二重に守る（docs/06 §3-1）。
# IAM ロール側の許可と合わせて、読めるのは Lambda 実行ロールだけにする。
resource "aws_secretsmanager_secret_policy" "anthropic" {
  secret_arn = aws_secretsmanager_secret.anthropic.arn
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowLambdaRoleOnly"
        Effect    = "Allow"
        Principal = { AWS = var.reader_role_arn }
        Action    = "secretsmanager:GetSecretValue"
        Resource  = "*"
      },
      {
        Sid       = "DenyEveryoneElse"
        Effect    = "Deny"
        Principal = "*"
        Action    = "secretsmanager:GetSecretValue"
        Resource  = "*"
        Condition = {
          # 人間が CLI から投入・確認する経路は塞がない。
          # アカウント内の他のプリンシパルによる読み取りだけを拒否する
          ArnNotLike = {
            "aws:PrincipalArn" = concat([var.reader_role_arn], local.admin_arns)
          }
        }
      },
    ]
  })
}
