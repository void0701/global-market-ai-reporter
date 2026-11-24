"""米国・世界ニュース AI 解析＆自動レポート配信パイプライン."""

__version__ = "0.1.0"

# 日次 JSON のスキーマ版。期間レポートが過去データを集計するため、
# 構造を変える場合は必ずインクリメントする（docs/09 §7-2）。
DAILY_SCHEMA_VERSION = 1
RAW_SCHEMA_VERSION = 1
