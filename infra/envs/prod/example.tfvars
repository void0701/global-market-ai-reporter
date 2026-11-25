# prod の設定例。実値を書いた terraform.tfvars は .gitignore 済み（A-145）。
#
#   cp example.tfvars terraform.tfvars  して値を埋める

# bootstrap の出力 `secrets_kms_key_arn`
secrets_kms_key_arn = "arn:aws:kms:ap-southeast-2:000000000000:key/00000000-0000-0000-0000-000000000000"

# `aws sts get-caller-identity --query Arn --output text` の値
admin_principal_arn = "arn:aws:sts::000000000000:assumed-role/XXXX/YYYY"

# モデル差し替えはこの 1 行（docs/06 §6-1）
model_id      = "claude-opus-5"
provider_name = "anthropic"

monthly_budget_usd = 20
