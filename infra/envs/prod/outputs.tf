output "site_url" {
  description = "公開 URL（当面は Amplify の既定ドメイン / A-52）"
  value       = module.site.site_url
}

output "bucket" {
  value = module.site.bucket
}

output "function_name" {
  value = module.pipeline.function_name
}

output "secret_id" {
  description = "put-secret-value に渡す ID（値は手動投入 / A-33）"
  value       = module.secrets.secret_id
}

output "alert_topic_arn" {
  description = "ここにメールアドレスを購読登録する（手動 / A-143）"
  value       = module.pipeline.alert_topic_arn
}

output "manual_steps" {
  description = "apply 後に人間が 1 回だけ行う作業"
  value       = <<-EOT
    1) API キーの投入
       aws secretsmanager put-secret-value \
         --secret-id ${module.secrets.secret_id} \
         --secret-string file://./key.json && rm key.json

    2) 通知先の購読登録（この後、確認メールのリンクを開く）
       aws sns subscribe \
         --topic-arn ${module.pipeline.alert_topic_arn} \
         --protocol email \
         --notification-endpoint <あなたのアドレス>

    3) 公開 URL の確認
       ${module.site.site_url}
  EOT
}
