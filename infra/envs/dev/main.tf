# dev — 配線の検証用（docs/06 §1-1）
#
# prod との差は 3 点だけ。構成差を作ると「dev で通ったのに prod で壊れる」
# が起きるため、差分は意図的に最小にしている。
#
#   1. Scheduler は DISABLED で作る（自動実行しない / A-27）
#   2. **シークレットを作らない**（A-28 / A-140）。LLM は dry_run で呼ばない
#   3. ログ保持 7 日 / Budgets なし

data "aws_caller_identity" "current" {}

locals {
  project = "s-and-p"
  env     = "dev"
}

module "site" {
  source = "../../modules/site"

  project    = local.project
  env        = local.env
  account_id = data.aws_caller_identity.current.account_id
}

module "pipeline" {
  source = "../../modules/pipeline"

  project    = local.project
  env        = local.env
  region     = var.region
  account_id = data.aws_caller_identity.current.account_id

  bucket         = module.site.bucket
  bucket_arn     = module.site.bucket_arn
  amplify_app_id = module.site.amplify_app_id
  amplify_branch = module.site.amplify_branch

  lambda_zip_path = var.lambda_zip_path

  # dev にはシークレットを置かない。IAM の該当文ごと作られない
  secret_arn          = null
  secrets_kms_key_arn = null

  schedules_enabled  = false
  log_retention_days = 7
  log_level          = "DEBUG"
  model_id           = var.model_id
  provider_name      = var.provider_name
  monthly_budget_usd = null
}
