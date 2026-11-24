"""ローカル実行用 CLI.

設定の優先順は **CLI > 環境変数 > TOML**（docs/04 §6）。``--model`` は
モデル差し替え前の品質比較に使う（docs/05 A-10）。
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path

from . import doctor as doctor_mod
from . import pipeline
from .analysis.analyzer import Analyzer, compose_x_post, daily_prefix, estimate_reanalyze_cost
from .analysis.client import CredentialsError
from .collectors import market as market_mod
from .collectors import rss
from .config import Settings, load_settings
from .models import DailyRecord, PeriodType
from .processing import dedupe, normalize
from .publishing import store
from .publishing.local import LocalPublisher
from .rendering.html import Renderer
from .utils.logging import setup_logging

logger = logging.getLogger(__name__)


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _common_options() -> argparse.ArgumentParser:
    """サブコマンドの前後どちらでも指定できるようにするための親パーサ."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--model", help="Bedrock モデルIDを上書き（品質比較用）")
    common.add_argument("--region", help="AWS リージョンを上書き")
    common.add_argument("--output-dir", help="出力先を上書き")
    common.add_argument("--log-level", default=None)
    common.add_argument("--json-logs", action="store_true", help="ログを JSON 形式で出す")
    return common


def build_parser() -> argparse.ArgumentParser:
    common = _common_options()
    parser = argparse.ArgumentParser(
        prog="market-report",
        description="米国・世界ニュース AI 解析＆自動レポート配信パイプライン（Phase 1）",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="レポートを生成する（既定）", parents=[common])
    run.add_argument(
        "--type",
        dest="report_type",
        default="daily",
        choices=["daily", "weekly", "monthly", "yearly"],
    )
    run.add_argument("--date", type=_parse_date, help="対象日 YYYY-MM-DD（既定: 今日）")
    run.add_argument("--period", help="期間ラベル（例 2026-W47 / 2026-11 / 2026）")
    run.add_argument(
        "--dry-run", action="store_true", help="Bedrock を呼ばず擬似データで全段を検証（無料）"
    )

    collect = sub.add_parser(
        "collect", help="収集と重複排除だけ実行して結果を表示（無料）", parents=[common]
    )
    collect.add_argument("--threshold", type=float, help="類似度閾値を上書き")
    collect.add_argument("--show", type=int, default=15, help="表示件数")

    recluster = sub.add_parser(
        "recluster",
        help="保存済み記事を別パラメータで再クラスタリング（AI 呼び出しなし・無料）",
        parents=[common],
    )
    recluster.add_argument("--date", type=_parse_date, required=True)
    recluster.add_argument("--threshold", type=float, required=True)

    reanalyze = sub.add_parser(
        "reanalyze", help="保存済み記事を再解析する（課金あり）", parents=[common]
    )
    reanalyze.add_argument("--date", type=_parse_date, help="単日")
    reanalyze.add_argument("--from", dest="date_from", type=_parse_date)
    reanalyze.add_argument("--to", dest="date_to", type=_parse_date)
    reanalyze.add_argument("--out-suffix", default="v2", help="出力ファイルの接尾辞")
    reanalyze.add_argument(
        "--dry-run-cost", action="store_true", help="コスト見積もりだけ表示して終了"
    )
    reanalyze.add_argument("--yes", action="store_true", help="確認プロンプトを省略")

    index = sub.add_parser("reindex", help="index / archive を再生成する（無料）", parents=[common])
    index.add_argument("--noop", action="store_true", help=argparse.SUPPRESS)

    market = sub.add_parser("market", help="市況データだけ取得して表示（無料）", parents=[common])
    market.add_argument("--date", type=_parse_date)
    market.add_argument("--all", action="store_true", help="保存対象の全40系列を表示")

    doc = sub.add_parser(
        "doctor",
        help="セットアップを検証する（Bedrock に極小リクエストを送る）",
        parents=[common],
    )
    doc.add_argument("--quick", action="store_true", help="Bedrock と外部取得を省略")

    return parser


# --------------------------------------------------------------- commands


def cmd_run(args: argparse.Namespace, settings: Settings) -> int:
    pub = LocalPublisher(Path(settings.output_dir))
    if args.report_type == "daily":
        record = pipeline.run_daily(
            settings, target_date=args.date, dry_run=args.dry_run, publisher=pub
        )
        print(f"\n日次レポート {record.date} を生成しました")
        print(f"  {Path(settings.output_dir) / store.report_key(date.fromisoformat(record.date))}")
        print(f"  X投稿文: {record.x_post_full}")
        return 0

    ptype: PeriodType = args.report_type
    try:
        record = pipeline.run_period(
            settings,
            ptype,
            label=args.period,
            target_date=args.date,
            dry_run=args.dry_run,
            publisher=pub,
        )
    except pipeline.InsufficientDataError as exc:
        # データ不足は異常としない（docs/09 §7-3 / A-80）
        print(f"スキップ: {exc}")
        return 0
    print(f"\n{ptype} レポート {record.period_label} を生成しました（{record.stats.days}日分）")
    print(
        "  " + str(Path(settings.output_dir) / store.period_report_key(ptype, record.period_label))
    )
    print(f"  X投稿文: {record.x_post_full}")
    return 0


def cmd_collect(args: argparse.Namespace, settings: Settings) -> int:
    window_from, window_to = rss.window_for(datetime.now().astimezone(), settings.window_hours)
    feed_results, articles = rss.collect(settings, window_from, window_to)
    articles = normalize.prepare(articles)
    selected, info = dedupe.run(articles, settings, threshold=args.threshold)

    print("\n=== フィード ===")
    for res in sorted(feed_results, key=lambda r: -r.in_window_count):
        flag = "STALE" if res.stale else ("ERROR" if res.error else "ok")
        age = f"{res.latest_item_age_hours}h" if res.latest_item_age_hours is not None else "—"
        print(
            f"  [{flag:>5}] {res.name:<32} items={res.item_count:>3} "
            f"窓内={res.in_window_count:>3} 最新={age:>7} {res.error or ''}"
        )
    print(
        f"\n=== 重複排除（閾値 {info.threshold}）: 入力 {info.input_count} / "
        f"クラスタ {info.cluster_count} / 選抜 {info.selected_count} ==="
    )
    for idx, art in enumerate(selected[: args.show], 1):
        print(f"  {idx:>2}. [{art.feed_name[:20]:<20}] {art.title[:76]}")
    return 0


def cmd_recluster(args: argparse.Namespace, settings: Settings) -> int:
    """AI を呼ばずに重複排除を再実験する（docs/05 A-93 — 無料で何度でも）."""
    pub = LocalPublisher(Path(settings.output_dir))
    text = pub.get_text(store.raw_news_key(args.date))
    if text is None:
        print(f"保存済み記事がありません: {store.raw_news_key(args.date)}")
        return 1
    from .models import RawNewsRecord

    raw = RawNewsRecord.model_validate_json(text)
    articles = normalize.prepare(raw.articles)
    selected, info = dedupe.run(articles, settings, threshold=args.threshold)

    print(f"\n=== 再クラスタリング {args.date}（AI 呼び出しなし）===")
    print(f"  元: 閾値 {raw.dedupe.threshold} → クラスタ {raw.dedupe.cluster_count}")
    print(f"  新: 閾値 {info.threshold} → クラスタ {info.cluster_count}")
    from collections import defaultdict

    buckets: defaultdict[int, list] = defaultdict(list)
    for art in articles:
        buckets[art.cluster_id].append(art)
    multi = {k: v for k, v in buckets.items() if len(v) > 1}
    print(f"  2件以上のクラスタ: {len(multi)} 個")
    for key, members in list(multi.items())[:10]:
        print(f"  [cluster {key}] {len(members)}件")
        for art in members:
            mark = "*" if art.is_representative else " "
            print(
                f"      {mark} sim={art.similarity_to_representative:.2f} "
                f"[{art.feed_name[:16]:<16}] {art.title[:56]}"
            )
    print(f"\n  選抜 {len(selected)} 件:")
    for idx, art in enumerate(selected, 1):
        print(f"    {idx:>2}. [{art.feed_name[:18]:<18}] {art.title[:70]}")
    return 0


def cmd_reanalyze(args: argparse.Namespace, settings: Settings) -> int:
    """保存済み記事を別スキーマ / 別プロンプトで再解析する（docs/11 §7）."""
    pub = LocalPublisher(Path(settings.output_dir))
    if args.date:
        targets = [args.date]
    elif args.date_from and args.date_to:
        targets = [d for d in store.list_daily_dates(pub) if args.date_from <= d <= args.date_to]
        if not targets:
            from datetime import timedelta

            cur, targets = args.date_from, []
            while cur <= args.date_to:
                if pub.exists(store.raw_news_key(cur)):
                    targets.append(cur)
                cur += timedelta(days=1)
    else:
        print("--date か --from/--to を指定してください")
        return 1

    available = [d for d in targets if pub.exists(store.raw_news_key(d))]
    missing = [d for d in targets if d not in available]
    cost = estimate_reanalyze_cost(settings.model_id, len(available))

    print("\n=== 再解析の見積もり ===")
    print(f"  モデル     : {settings.model_id}")
    print(
        f"  対象日数   : {len(available)} 日"
        + (f"（データ無し {len(missing)} 日）" if missing else "")
    )
    print(f"  推定コスト : ${cost:.2f}  （参考単価ベース。Bedrock 実単価は要確認）")
    print(f"  出力接尾辞 : -{args.out_suffix}（元のファイルは上書きしません）")
    if args.dry_run_cost:
        return 0
    if not available:
        print("  対象データがありません")
        return 1
    if not args.yes:
        answer = input("\n実行しますか？ [y/N]: ").strip().lower()
        if answer not in ("y", "yes"):
            print("中止しました")
            return 0

    from .models import RawNewsRecord

    analyzer = Analyzer(settings, dry_run=False)
    renderer = Renderer(settings)
    total = 0.0
    for target in available:
        raw = RawNewsRecord.model_validate_json(pub.get_text(store.raw_news_key(target)) or "{}")
        selected = [a for a in raw.articles if a.selected_for_analysis]
        if not selected:
            articles = normalize.prepare(raw.articles)
            selected, _ = dedupe.run(articles, settings)
        snapshot_text = pub.get_text(store.raw_market_key(target))
        if snapshot_text is None:
            print(f"  {target}: 市況データが無いためスキップ")
            continue
        from .models import MarketSnapshot

        snapshot = MarketSnapshot.model_validate_json(snapshot_text)
        analysis, result, system, user = analyzer.analyze_daily(target, snapshot, selected)
        store.dump(
            pub,
            store.raw_llm_key(target, "daily", args.out_suffix),
            pipeline._llm_record(
                target.isoformat(), "daily", settings, system, user, result, False
            ),
        )
        record = DailyRecord(
            schema_version=pipeline.DAILY_SCHEMA_VERSION,
            date=target.isoformat(),
            generated_at=pipeline._now_iso(),
            model=f"{settings.model_id} (reanalyzed)",
            dry_run=False,
            market=snapshot,
            analysis=analysis,
            x_post_full=compose_x_post(
                daily_prefix(snapshot, target), analysis.x_post_text, settings.x_weight_limit
            ),
        )
        store.dump(pub, store.daily_key(target, args.out_suffix), record)
        pub.put_text(
            f"reports/{target.isoformat()}-{args.out_suffix}.html",
            renderer.daily(record, rel="../"),
        )
        spent = result.cost_usd if result else 0.0
        total += spent
        print(f"  {target}: 完了 (${spent:.4f})")
    print(f"\n合計コスト: ${total:.4f}")
    return 0


def cmd_reindex(args: argparse.Namespace, settings: Settings) -> int:
    pub = LocalPublisher(Path(settings.output_dir))
    pipeline.rebuild_index(settings, pub)
    print(f"index / archive を再生成しました: {settings.output_dir}")
    return 0


def cmd_doctor(args: argparse.Namespace, settings: Settings) -> int:
    return doctor_mod.run(settings, quick=args.quick)


def cmd_market(args: argparse.Namespace, settings: Settings) -> int:
    target = args.date or datetime.now().astimezone().date()
    snapshot = market_mod.fetch_snapshot(settings, market_mod.market_asof_for(target))
    symbols = settings.all_symbols if args.all else settings.ai_symbols
    print(f"\n=== 市況 基準日 {snapshot.reference_date}（asof {snapshot.asof_date}）===")
    for symbol in symbols:
        quote = snapshot.get(symbol)
        if not quote:
            continue
        chg = f"{quote.change_pct:+7.2f}%" if quote.change_pct is not None else "      —"
        print(f"  {symbol:<11} {quote.label:<20} {quote.close:>12,.2f} {chg}  [{quote.group}]")
    gv = snapshot.global_view
    if gv.jpy_theoretical_change_pct is not None:
        print("\n=== オルカン（円建て全世界株式）===")
        print(f"  円建て変化率（理論値） {gv.jpy_theoretical_change_pct:+.2f}%")
        print(
            f"    株価要因 {gv.contribution_equity_pct:+.2f}% "
            f"/ 為替要因 {gv.contribution_fx_pct:+.2f}%"
        )
        if gv.etf_jpy_change_pct is not None:
            print(f"  2559.T 実測 {gv.etf_jpy_change_pct:+.2f}%（{gv.etf_jpy_date}・1営業日前）")
    if snapshot.fetch_errors:
        print(f"\n取得エラー {len(snapshot.fetch_errors)} 件:")
        for err in snapshot.fetch_errors:
            print(f"  {err}")
    return 0


# --------------------------------------------------------------- entrypoint

_COMMANDS = {
    "run": cmd_run,
    "collect": cmd_collect,
    "recluster": cmd_recluster,
    "reanalyze": cmd_reanalyze,
    "reindex": cmd_reindex,
    "market": cmd_market,
    "doctor": cmd_doctor,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(json_format=args.json_logs or None, level=args.log_level or "INFO")

    settings = load_settings(model_id=args.model, region=args.region, output_dir=args.output_dir)

    command = args.command or "run"
    if command == "run" and args.command is None:
        # 引数なしで実行された場合は日次
        args.report_type = "daily"
        args.date = None
        args.period = None
        args.dry_run = False

    handler = _COMMANDS[command]
    try:
        return handler(args, settings)
    except CredentialsError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 3
    except rss.AllFeedsEmptyError as exc:
        logger.error("収集に失敗: %s", exc)
        return 2
    except KeyboardInterrupt:
        print("\n中断しました")
        return 130


if __name__ == "__main__":
    sys.exit(main())
