variable "project" {
  description = "全リソース名の接頭辞"
  type        = string
  default     = "s-and-p"
}

variable "region" {
  description = "プロジェクトのリージョン（新サインアップ体験では変更不可 / A-120）"
  type        = string
  default     = "ap-southeast-2"
}

variable "github_repo" {
  description = "デプロイを許可する GitHub リポジトリ owner/repo"
  type        = string
  default     = "void0701/global-market-ai-reporter"
}
