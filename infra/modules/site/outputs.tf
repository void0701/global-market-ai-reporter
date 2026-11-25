output "bucket" {
  value = aws_s3_bucket.data.id
}

output "bucket_arn" {
  value = aws_s3_bucket.data.arn
}

output "amplify_app_id" {
  value = aws_amplify_app.site.id
}

output "amplify_branch" {
  value = aws_amplify_branch.main.branch_name
}

output "site_url" {
  description = "公開 URL（当面は既定ドメイン / A-52）"
  value       = "https://${aws_amplify_branch.main.branch_name}.${aws_amplify_app.site.default_domain}"
}
