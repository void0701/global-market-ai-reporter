variable "region" {
  type    = string
  default = "ap-southeast-2"
}

variable "lambda_zip_path" {
  description = "scripts/build_lambda.sh の出力"
  type        = string
  default     = "../../../build/lambda.zip"
}

variable "secrets_kms_key_arn" {
  description = "bootstrap の出力 secrets_kms_key_arn。terraform.tfvars に書く"
  type        = string
}

variable "admin_principal_arn" {
  description = <<-EOT
    シークレットを手動投入する人間のプリンシパル ARN。
    `aws sts get-caller-identity --query Arn --output text` の値。
    terraform.tfvars に書く（tfvars は .gitignore 済み / A-145）。
  EOT
  type        = string
}

variable "model_id" {
  type    = string
  default = "claude-opus-5"
}

variable "provider_name" {
  type    = string
  default = "anthropic"
}

variable "monthly_budget_usd" {
  type    = number
  default = 20
}
