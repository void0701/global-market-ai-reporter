# prod — 本番（docs/06 §1-1）
#
# dev との差は 3 点（Scheduler 有効 / シークレットあり / 保持 30 日 + Budgets）。
#
# ## apply 後にやる手動作業（2 つとも人間が 1 回だけ）
#
# 1. **API キーの投入**（A-33。Terraform で管理すると state に平文で残る）
#
#    ```bash
#    aws secretsmanager put-secret-value \
#      --secret-id "$(terraform output -raw secret_id)" \
#      --secret-string file://./key.json
#    rm key.json   # ← リポジトリ外に作り、投入後すぐ消す
#    ```
#
# 2. **通知先の購読登録**（A-143。リポジトリが public なのでアドレスを置かない）
#
#    ```bash
#    aws sns subscribe \
#      --topic-arn "$(terraform output -raw alert_topic_arn)" \
#      --protocol email \
#      --notification-endpoint <あなたのアドレス>
#    ```
#    この後、届いた確認メールのリンクを開く。

data "aws_caller_identity" "current" {}

locals {
  project = "s-and-p"
  env     = "prod"
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

  secret_arn          = module.secrets.secret_arn
  secrets_kms_key_arn = var.secrets_kms_key_arn

  schedules_enabled  = true
  log_retention_days = 30
  log_level          = "INFO"
  model_id           = var.model_id
  provider_name      = var.provider_name
  # AWS 分の想定は $2.80/月。その約 7 倍で止める（docs/13 §2）
  monthly_budget_usd = var.monthly_budget_usd
}

module "secrets" {
  source = "../../modules/secrets"

  project     = local.project
  env         = local.env
  account_id  = data.aws_caller_identity.current.account_id
  kms_key_arn = var.secrets_kms_key_arn

  # 値を読めるのは Lambda だけ。投入する人間だけが例外（docs/06 §3-1）
  reader_role_arn = module.pipeline.lambda_role_arn
  admin_role_name = var.admin_role_name
}
