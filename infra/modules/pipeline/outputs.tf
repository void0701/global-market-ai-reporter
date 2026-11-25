output "function_name" {
  value = aws_lambda_function.report.function_name
}

output "function_arn" {
  value = aws_lambda_function.report.arn
}

output "lambda_role_arn" {
  description = "secrets モジュールの reader_role_arn に渡す"
  value       = aws_iam_role.lambda.arn
}

output "alert_topic_arn" {
  description = "ここにメールアドレスを購読登録する（手動 / A-143）"
  value       = aws_sns_topic.alerts.arn
}

output "schedule_names" {
  value = [for s in aws_scheduler_schedule.report : s.name]
}
