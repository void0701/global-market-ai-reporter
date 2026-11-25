output "site_url" {
  value = module.site.site_url
}

output "bucket" {
  value = module.site.bucket
}

output "function_name" {
  value = module.pipeline.function_name
}

output "alert_topic_arn" {
  value = module.pipeline.alert_topic_arn
}

output "smoke_test" {
  description = "配線確認のコマンド（LLM を呼ばないので無料）"
  value       = <<-EOT
    aws lambda invoke \
      --function-name ${module.pipeline.function_name} \
      --payload '{"report_type":"daily","dry_run":true}' \
      --cli-binary-format raw-in-base64-out \
      /dev/stdout
  EOT
}
