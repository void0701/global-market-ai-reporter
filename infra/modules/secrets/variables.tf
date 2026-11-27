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

variable "recovery_window_in_days" {
  description = <<-EOT
    削除後の復旧猶予（日）。**0 = 即時削除**。

    既定を 0 にしているのは、7 日にすると「名前が 7 日間再利用できない」
    ことがセットアップ中のデッドロックになるため（R-19）。中身は再発行
    できる API キーなので猶予の価値は小さい。
  EOT
  type        = number
  default     = 0

  validation {
    condition     = var.recovery_window_in_days == 0 || var.recovery_window_in_days >= 7
    error_message = "0（即時）または 7〜30 日。AWS の仕様で 1〜6 は指定できません。"
  }
}
