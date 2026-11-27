# pipeline — Lambda 本体、IAM、スケジュール 4 本、失敗通知
#
# スケジュールは 4 本に分かれる（docs/09 §2-2 / A-137）:
#   日次 火〜土 07:00 / 週次 月 07:00 / 月次 1日 07:30 / 年次 1/1 08:00
# 同じ関数を別ペイロードで呼ぶ。月次・年次を 30 分ずつずらすのは、
# 同日に重なったときの同時実行と index.html の競合を避けるため。

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

locals {
  name = "${var.project}-${var.env}"

  schedules = {
    daily = {
      # 07:00 JST = 前日 17:00 EST / 18:00 EDT。米国の引けを確実に含む。
      # 日・月は米国の取引セッションを 1 つも含まないため除く（A-137）
      expression = "cron(0 7 ? * TUE-SAT *)"
      payload    = { report_type = "daily" }
    }
    weekly = {
      # 月曜は日次を出さず週次を出す。これがこの設計の要点（docs/09 §2-2）
      expression = "cron(0 7 ? * MON *)"
      payload    = { report_type = "weekly" }
    }
    monthly = {
      expression = "cron(30 7 1 * ? *)"
      payload    = { report_type = "monthly" }
    }
    yearly = {
      expression = "cron(0 8 1 1 ? *)"
      payload    = { report_type = "yearly" }
    }
  }
}

# ---------------------------------------------------------------------------
# 失敗通知（A-53 / A-148）
#   **トピックだけを作る。購読登録は人間が 1 回だけ行う**（A-143）。
#   リポジトリが public であり、SNS の email 購読は受信者の確認クリックが
#   必須なので、アドレスを HCL に書く利点がなく露出だけが増える。
# ---------------------------------------------------------------------------

resource "aws_sns_topic" "alerts" {
  name              = "${local.name}-alerts"
  display_name      = "s-and-p ${var.env}"
  kms_master_key_id = "alias/aws/sns"
}

# ---------------------------------------------------------------------------
# ログ
#   Terraform で先に作る。Lambda に自動生成させると保持期間が「無期限」に
#   なり、`logs:CreateLogGroup` も要求される
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${local.name}-report"
  retention_in_days = var.log_retention_days
}

# ---------------------------------------------------------------------------
# Lambda 実行ロール（docs/06 §3-0 / A-147）
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${local.name}-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "lambda" {
  statement {
    sid     = "Logs"
    effect  = "Allow"
    actions = ["logs:CreateLogStream", "logs:PutLogEvents"]
    # ロググループは Terraform で作るので CreateLogGroup は不要
    resources = ["${aws_cloudwatch_log_group.lambda.arn}:*"]
  }

  statement {
    sid    = "ReadWriteReports"
    effect = "Allow"
    # **GetObject / ListBucket が必須。** `run_daily` は末尾で
    # `rebuild_index` を呼び、過去の全日次レコードを読む（R-2）。
    # 書き込みだけ許可すると初日は通って 2 日目から AccessDenied になる
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${var.bucket_arn}/*"]
  }

  statement {
    sid       = "ListReportsBucket"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [var.bucket_arn]
  }

  statement {
    sid    = "DeployToAmplify"
    effect = "Allow"
    actions = [
      "amplify:CreateDeployment",
      "amplify:StartDeployment",
      "amplify:GetJob",
      "amplify:GetApp",
      "amplify:GetBranch",
    ]
    resources = [
      "arn:aws:amplify:${var.region}:${var.account_id}:apps/${var.amplify_app_id}",
      "arn:aws:amplify:${var.region}:${var.account_id}:apps/${var.amplify_app_id}/*",
    ]
  }

  statement {
    sid    = "NotifyFailures"
    effect = "Allow"
    # DLQ への送信にも同じ権限を使う
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]
  }

  statement {
    sid    = "Tracing"
    effect = "Allow"
    actions = [
      "xray:PutTraceSegments",
      "xray:PutTelemetryRecords",
    ]
    # X-Ray の書き込み API はリソース単位の指定に対応していない
    resources = ["*"]
  }

  # シークレットは prod のみ。dev では文ごと作らない（A-140）
  dynamic "statement" {
    for_each = var.secret_arn == null ? [] : [var.secret_arn]
    content {
      sid       = "ReadApiKey"
      effect    = "Allow"
      actions   = ["secretsmanager:GetSecretValue"]
      resources = [statement.value]
    }
  }

  dynamic "statement" {
    for_each = var.secrets_kms_key_arn == null ? [] : [var.secrets_kms_key_arn]
    content {
      sid       = "DecryptApiKey"
      effect    = "Allow"
      actions   = ["kms:Decrypt"]
      resources = [statement.value]
    }
  }
}

resource "aws_iam_role_policy" "lambda" {
  name   = "pipeline"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda.json
}

# ---------------------------------------------------------------------------
# Lambda 関数
#   zip は直接アップロード（A-139。実測 6.2MB / 上限 50MB）
# ---------------------------------------------------------------------------

resource "aws_lambda_function" "report" {
  function_name = "${local.name}-report"
  role          = aws_iam_role.lambda.arn
  handler       = "market_report.lambda_handler.handler"
  runtime       = "python3.12" # ローカルと揃える（A-138）
  architectures = ["x86_64"]   # arm64 は無料枠内で削減額ゼロ（docs/06 §4-5）

  filename         = var.lambda_zip_path
  source_code_hash = filebase64sha256(var.lambda_zip_path)

  # > ### ⚠️ このアカウントは Lambda のメモリ上限が 512MB
  # >
  # > 1024 を指定すると CreateFunction が
  # > `ValidationException: 'MemorySize' value failed to satisfy constraint:
  # > Member must have value less than or equal to 512` で 400 を返す
  # > （docs/13 §8 R-15 / A-173）。service-quotas には現れない制約。
  # >
  # > **実測では 512MB で約 10 年分もつ**（下表）。build_site() が過去の
  # > 全レコードを読むため日数に比例して増える。
  # >
  # > | 日数 | ページ | zip | 最大 RSS |
  # > |---|---|---|---|
  # > | 3 | 5 | 0.0 MB | 34.7 MB |
  # > | 300（約1.2年） | 302 | 3.0 MB | 93.8 MB |
  # > | 600（約2.3年） | 602 | 6.0 MB | 150.0 MB |
  # > | 1300（約5年） | 1302 | 13.1 MB | 282.0 MB |
  # >
  # > CloudWatch の Max Memory Used が 450MB に近づいたら、クォータの
  # > 引き上げ申請かアーカイブの打ち切りを検討する。
  memory_size = var.memory_size

  # LLM 実測 153 秒 + 取得 20 秒。300 秒では余裕が足りない（A-128）。
  # アカウントの Function timeout クォータは 900 秒なので 600 は通る
  timeout = 600

  # 暴走時のコスト上限。スケジュールは 30 分ずらしているので 2 で足りる
  reserved_concurrent_executions = 2

  tracing_config {
    # 1 回 150 秒かかる処理の内訳（RSS 取得 / 市況 / LLM / S3 / Amplify）が
    # 見える。月 30 回の実行では X-Ray の無料枠内で $0
    mode = "Active"
  }

  dead_letter_config {
    # Scheduler は非同期呼び出しなので、リトライを使い切った呼び出しは
    # ハンドラの try/except に到達しない。SNS を DLQ にして取りこぼさない
    # （A-148 の検知をもう 1 枚増やす）
    target_arn = aws_sns_topic.alerts.arn
  }

  environment {
    variables = merge(
      {
        # **API キーは入れない。** コンソールで誰でも見えてしまうため、
        # コールドスタート時に Secrets Manager から取る（docs/06 §3-0）
        REPORT_BUCKET   = var.bucket
        AMPLIFY_APP_ID  = var.amplify_app_id
        AMPLIFY_BRANCH  = var.amplify_branch
        ALERT_TOPIC_ARN = aws_sns_topic.alerts.arn
        LOG_LEVEL       = var.log_level

        # モデル差し替えは tfvars の 1 行変更だけで済む（docs/06 §6-1）
        BEDROCK_MODEL_ID       = var.model_id
        MARKET_REPORT_PROVIDER = var.provider_name

        # Lambda は /tmp 以外が読み取り専用（config.py が解決する）
        MARKET_REPORT_OUTPUT_DIR = "output"
      },
      var.secret_arn == null ? {} : { ANTHROPIC_SECRET_ID = var.secret_arn },
    )
  }

  depends_on = [aws_cloudwatch_log_group.lambda]
}

# ---------------------------------------------------------------------------
# 失敗検知の 2 層目（A-148）
#   ハンドラに到達しない異常（タイムアウト・OOM・import 失敗）を拾う。
#   ハンドラ内の try/except だけではこれらが無通知になる
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  alarm_name          = "${local.name}-report-errors"
  alarm_description   = "レポート生成 Lambda がエラー終了した（タイムアウト含む）"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.report.function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  # データが無い間は正常扱い（1 日 1 回しか動かないため）
  treat_missing_data = "notBreaching"
  alarm_actions      = [aws_sns_topic.alerts.arn]
}

# ---------------------------------------------------------------------------
# EventBridge Scheduler ×4
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
    condition {
      # 混乱した代理問題を防ぐ。自アカウント以外からの引き受けを拒否する
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${local.name}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

resource "aws_iam_role_policy" "scheduler" {
  name = "invoke"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = aws_lambda_function.report.arn
    }]
  })
}

resource "aws_scheduler_schedule" "report" {
  for_each = local.schedules

  name = "${local.name}-${each.key}"
  # dev では無効状態で作る。構成差を作らずに自動実行だけ止める（A-27）
  state                        = var.schedules_enabled ? "ENABLED" : "DISABLED"
  schedule_expression          = each.value.expression
  schedule_expression_timezone = "Asia/Tokyo"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.report.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode(each.value.payload)

    retry_policy {
      # 失敗したらその日は諦める方針（A-57 / 3-3）。短いリトライのみ
      maximum_retry_attempts       = 2
      maximum_event_age_in_seconds = 3600
    }
  }
}

# ---------------------------------------------------------------------------
# コスト上限（prod のみ / A-144）
#   通知先は SNS トピック。アドレスを HCL に書かずに済む
# ---------------------------------------------------------------------------

resource "aws_budgets_budget" "monthly" {
  count = var.monthly_budget_usd == null ? 0 : 1

  name         = "${local.name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 80
    threshold_type            = "PERCENTAGE"
    notification_type         = "ACTUAL"
    subscriber_sns_topic_arns = [aws_sns_topic.alerts.arn]
  }

  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 100
    threshold_type            = "PERCENTAGE"
    notification_type         = "FORECASTED"
    subscriber_sns_topic_arns = [aws_sns_topic.alerts.arn]
  }
}

# Budgets が SNS に投げられるようにトピックポリシーを付ける
resource "aws_sns_topic_policy" "alerts" {
  arn = aws_sns_topic.alerts.arn
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowBudgets"
        Effect    = "Allow"
        Principal = { Service = "budgets.amazonaws.com" }
        Action    = "SNS:Publish"
        Resource  = aws_sns_topic.alerts.arn
        Condition = { StringEquals = { "aws:SourceAccount" = var.account_id } }
      },
      {
        Sid       = "AllowCloudWatchAlarms"
        Effect    = "Allow"
        Principal = { Service = "cloudwatch.amazonaws.com" }
        Action    = "SNS:Publish"
        Resource  = aws_sns_topic.alerts.arn
        Condition = { StringEquals = { "aws:SourceAccount" = var.account_id } }
      },
    ]
  })
}
