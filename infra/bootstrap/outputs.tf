output "state_bucket" {
  description = "envs/*/backend.tf に書く state バケット名"
  value       = aws_s3_bucket.state.id
}

output "state_kms_key_arn" {
  description = "state 用 CMK"
  value       = aws_kms_key.state.arn
}

output "secrets_kms_key_arn" {
  description = "Secrets Manager 用 CMK（envs の secrets モジュールに渡す）"
  value       = aws_kms_key.secrets.arn
}

output "gha_role_arns" {
  description = "ワークフロー YAML に埋め込むロール ARN"
  value = {
    plan        = aws_iam_role.gha_plan.arn
    deploy_dev  = aws_iam_role.gha_deploy_dev.arn
    deploy_prod = aws_iam_role.gha_deploy_prod.arn
  }
}

output "next_steps" {
  description = "apply 後にやること"
  value       = <<-EOT
    1. envs/dev/backend.tf と envs/prod/backend.tf の bucket を
       「${aws_s3_bucket.state.id}」に設定する
    2. .github/workflows/deploy-*.yml のロール ARN を上記 gha_role_arns に合わせる
    3. GitHub で Environments「prod」を作り、必須レビュアーを設定する
       （これが prod デプロイの承認ゲートになる / A-42）
  EOT
}
