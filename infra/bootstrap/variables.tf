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

# > ### ⚠️ GitHub の sub クレームには不変の数値 ID が入る
# >
# > 実機で確認した sub は名前だけではなく **owner と repo の数値 ID を
# > 伴う形**だった（docs/13 §8 R-14）。
# >
# >     repo:void0701@360565473/global-market-ai-reporter@1469782982:ref:refs/heads/main
# >
# > 名前だけで信頼ポリシーを書くと `Not authorized to perform
# > sts:AssumeRoleWithWebIdentity` になり、エラー文面からは原因が分からない。
# >
# > これは **リポジトリ名の使い回し攻撃**（削除された名前を第三者が取得して
# > 信頼を引き継ぐ）を防ぐための仕組みで、ID を使うほうが安全。
# >
# > 取得方法:
# >
# > ```bash
# > gh api repos/<owner>/<repo> --jq '"owner_id: \(.owner.id)  repo_id: \(.id)"'
# > ```
# >
# > **リポジトリを作り直すと ID が変わり、デプロイが失敗する**（fail close）。
# > その場合はこの値を更新して bootstrap を再 apply する。

variable "github_owner_id" {
  description = "GitHub オーナーの数値 ID（gh api users/<owner> --jq .id）"
  type        = string
  default     = "360565473"
}

variable "github_repo_id" {
  description = "GitHub リポジトリの数値 ID（gh api repos/<owner>/<repo> --jq .id）"
  type        = string
  default     = "1469782982"
}
