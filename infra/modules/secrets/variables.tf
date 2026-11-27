variable "project" {
  type = string
}

variable "env" {
  type = string
}

variable "kms_key_arn" {
  description = "secrets 用 CMK（bootstrap の出力）"
  type        = string
}

variable "reader_role_arn" {
  description = "値を読める唯一のロール（Lambda 実行ロール）"
  type        = string
}

variable "account_id" {
  type = string
}

variable "admin_role_name" {
  description = <<-EOT
    値を手動投入する人間が引き受けているロールの**名前**（ARN ではない）。

      aws sts get-caller-identity --query Arn --output text
      # arn:aws:sts::<acct>:assumed-role/<この部分>/<セッション名>

    ARN ではなく名前を受けるのは、`aws:PrincipalArn` がロール ARN 形式と
    セッション ARN 形式のどちらを返すかが条件によって変わるため。
    モジュール側で両形式を組み立てる。
  EOT
  type        = string
}
