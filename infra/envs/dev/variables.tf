variable "region" {
  type    = string
  default = "ap-southeast-2"
}

variable "lambda_zip_path" {
  description = "scripts/build_lambda.sh の出力"
  type        = string
  default     = "../../../build/lambda.zip"
}

variable "model_id" {
  type    = string
  default = "claude-opus-5"
}

variable "provider_name" {
  type    = string
  default = "anthropic"
}
