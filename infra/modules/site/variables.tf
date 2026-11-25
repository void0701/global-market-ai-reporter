variable "project" {
  type = string
}

variable "env" {
  type = string
  validation {
    condition     = contains(["dev", "prod"], var.env)
    error_message = "env は dev か prod。"
  }
}

variable "account_id" {
  description = "バケット名を全世界で一意にするために使う"
  type        = string
}

variable "branch" {
  description = "Amplify のブランチ名。既定ドメインは https://<branch>.<appId>.amplifyapp.com"
  type        = string
  default     = "main"
}

variable "noncurrent_version_days" {
  description = "旧バージョンを保持する日数（現行オブジェクトは消さない）"
  type        = number
  default     = 90
}
