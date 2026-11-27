variable "project" {
  type = string
}

variable "env" {
  type = string
}

variable "region" {
  type = string
}

variable "account_id" {
  type = string
}

# ---- site モジュールの出力を受ける ----

variable "bucket" {
  type = string
}

variable "bucket_arn" {
  type = string
}

variable "amplify_app_id" {
  type = string
}

variable "amplify_branch" {
  type = string
}

# ---- secrets モジュールの出力（prod のみ。dev は null） ----

variable "secret_arn" {
  description = "Claude API キーのシークレット ARN。dev では null（A-140）"
  type        = string
  default     = null
}

variable "secrets_kms_key_arn" {
  description = "シークレット復号用 CMK。dev では null"
  type        = string
  default     = null
}

# ---- デプロイ ----

variable "lambda_zip_path" {
  description = "scripts/build_lambda.sh が作る zip のパス"
  type        = string
}

# ---- 振る舞い ----

variable "schedules_enabled" {
  description = "false なら Scheduler を DISABLED で作る（dev / A-27）"
  type        = bool
  default     = false
}

variable "memory_size" {
  description = <<-EOT
    Lambda のメモリ(MB)。**このアカウントの上限は 512**（超えると
    CreateFunction が 400 / docs/13 §8 R-15）。実測で約 10 年分もつ。
  EOT
  type        = number
  default     = 512

  validation {
    condition     = var.memory_size <= 512
    error_message = "このアカウントの Lambda メモリ上限は 512MB です（R-15）。"
  }
}

variable "log_retention_days" {
  type    = number
  default = 7
}

variable "log_level" {
  type    = string
  default = "INFO"
}

variable "model_id" {
  description = "差し替えはこの 1 行の変更 + apply で済む（docs/06 §6-1）"
  type        = string
  default     = "claude-opus-5"
}

variable "provider_name" {
  description = "anthropic（Claude API 直接）か bedrock"
  type        = string
  default     = "anthropic"
  validation {
    condition     = contains(["anthropic", "bedrock"], var.provider_name)
    error_message = "provider_name は anthropic か bedrock。"
  }
}

variable "monthly_budget_usd" {
  description = "月額予算アラートの閾値。null なら Budgets を作らない"
  type        = number
  default     = null
}
