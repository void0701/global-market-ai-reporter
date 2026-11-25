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

variable "admin_principal_arn" {
  description = "値を手動投入する人間のプリンシパル ARN（aws sts get-caller-identity の Arn）"
  type        = string
}
