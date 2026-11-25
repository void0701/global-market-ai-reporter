# state は bootstrap が作ったバケットに置く。
# bucket 名は bootstrap の出力 `state_bucket` に合わせて書き換える。
#
# ロックは S3 ネイティブ（Terraform 1.10+）。DynamoDB テーブルは不要。
terraform {
  backend "s3" {
    bucket       = "s-and-p-tfstate-768756481546"
    key          = "dev/terraform.tfstate"
    region       = "ap-southeast-2"
    encrypt      = true
    use_lockfile = true
  }
}
