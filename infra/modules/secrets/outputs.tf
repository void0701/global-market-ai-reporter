output "secret_arn" {
  value = aws_secretsmanager_secret.anthropic.arn
}

output "secret_id" {
  description = "put-secret-value に渡す ID"
  value       = aws_secretsmanager_secret.anthropic.name
}
