# bootstrap は state を S3 に置けない（その S3 をここで作るため）。
# state はこのディレクトリのローカルに残り、`.gitignore` で除外される。
terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source = "hashicorp/aws"
      # 5.x は aws_amplify_app の作成でレート制限のリトライを無表示で続け、
      # 14 分ハングする（docs/13 §8 R-7 / A-146）
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "s-and-p"
      ManagedBy = "terraform"
      Component = "bootstrap"
    }
  }
}
