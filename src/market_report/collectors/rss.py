"""RSS 収集.

設計上の要点（docs/08 §7）

* フィード 1 本の障害で全体を落とさない（F-7）
* **鮮度チェック** — 最新記事が ``stale_feed_warn_hours`` より古ければ警告。
  WSJ のように「HTTP 200 を返しながら中身が 22 か月前」という故障を検知する（F-1）
* 全フィードが 0 件なら失敗させる（F-2）
* 要約なしフィード（Seeking Alpha）を許容し、``category`` からティッカーを抽出（F-4 / F-5）
* User-Agent を明示的に設定（F-6）
"""

from __future__ import annotations

import logging
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import feedparser

from ..config import FeedConfig, Settings
from ..models import Article, FeedFetchResult
from ..utils import logging as log
from ..utils.text import sha1, strip_html

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36 market-report/0.1"
)

_TICKER_URL_RE = re.compile(r"/symbol/([A-Z.\-]{1,8})$")


class AllFeedsEmptyError(RuntimeError):
    """全フィードから 1 件も取得できなかった（F-2）."""


def _fetch_bytes(url: str, timeout: int) -> tuple[bytes, int | None]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return resp.read(), getattr(resp, "status", None)


def _entry_datetime(entry: object) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        tm = getattr(entry, key, None) or (entry.get(key) if isinstance(entry, dict) else None)
        if tm:
            try:
                return datetime(*tm[:6], tzinfo=UTC)
            except (TypeError, ValueError):
                continue
    return None


def _extract_tickers(entry: object) -> list[str]:
    """Seeking Alpha の ``<category domain=".../symbol/XLE">`` からティッカーを拾う."""
    out: list[str] = []
    tags = entry.get("tags") if isinstance(entry, dict) else getattr(entry, "tags", None)
    for tag in tags or []:
        domain = (tag.get("scheme") or "") if isinstance(tag, dict) else ""
        term = (tag.get("term") or "") if isinstance(tag, dict) else ""
        m = _TICKER_URL_RE.search(domain or "")
        if m:
            out.append(m.group(1).upper())
        elif term and term.isupper() and 1 <= len(term) <= 6 and term.isalpha():
            out.append(term)
    return sorted(set(out))


def _categories(entry: object) -> list[str]:
    tags = entry.get("tags") if isinstance(entry, dict) else getattr(entry, "tags", None)
    out = []
    for tag in tags or []:
        term = (tag.get("term") or "") if isinstance(tag, dict) else ""
        if term:
            out.append(term)
    return out[:12]


def _summary_text(entry: object) -> str:
    for key in ("summary", "description"):
        val = entry.get(key) if isinstance(entry, dict) else getattr(entry, key, None)
        if val:
            return strip_html(str(val))
    content = entry.get("content") if isinstance(entry, dict) else None
    if content:
        first = content[0] if isinstance(content, list) and content else None
        if isinstance(first, dict) and first.get("value"):
            return strip_html(str(first["value"]))
    return ""


def fetch_feed(
    feed: FeedConfig, settings: Settings, window_from: datetime, window_to: datetime
) -> tuple[FeedFetchResult, list[Article]]:
    """1 フィードを取得する。例外は結果に包んで返し、呼び出し側を落とさない."""
    started = time.perf_counter()
    result = FeedFetchResult(name=feed.name, url=feed.url)
    try:
        raw, status = _fetch_bytes(feed.url, settings.fetch_timeout_sec)
        result.http_status = status
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        result.fetch_ms = int((time.perf_counter() - started) * 1000)
        log.warn(logger, "フィード取得失敗", feed=feed.name, error=result.error)
        return result, []

    parsed = feedparser.parse(raw)
    entries = parsed.entries or []
    result.item_count = len(entries)
    result.fetch_ms = int((time.perf_counter() - started) * 1000)

    dates = [d for d in (_entry_datetime(e) for e in entries) if d]
    if dates:
        latest = max(dates)
        age_h = (window_to - latest).total_seconds() / 3600
        result.latest_item_age_hours = round(age_h, 1)
        result.stale = age_h > settings.stale_feed_warn_hours
        if result.stale:
            # WSJ 型の故障: HTTP 200 だが中身が古い
            log.warn(
                logger,
                "フィードが古い（停止している可能性）",
                feed=feed.name,
                latest_age_hours=result.latest_item_age_hours,
                threshold_hours=settings.stale_feed_warn_hours,
            )

    articles: list[Article] = []
    for entry in entries:
        published = _entry_datetime(entry)
        if published is None or not (window_from <= published <= window_to):
            continue
        link = str(entry.get("link") or "")
        guid = str(entry.get("id") or entry.get("guid") or "")
        title = strip_html(str(entry.get("title") or "")).strip()
        if not title:
            continue
        summary = _summary_text(entry)
        articles.append(
            Article(
                id=sha1(guid or link or f"{feed.name}:{title}"),
                feed_name=feed.name,
                feed_weight=feed.weight,
                feed_category=feed.category,
                paywall=feed.paywall,
                title=title,
                summary_raw=summary,
                summary_len=len(summary),
                link=link,
                guid=guid,
                published=published.isoformat(),
                categories=_categories(entry),
                tickers=_extract_tickers(entry),
            )
        )
    result.in_window_count = len(articles)
    log.info(
        logger,
        "フィード取得",
        feed=feed.name,
        status=result.http_status,
        items=result.item_count,
        in_window=result.in_window_count,
        latest_age_h=result.latest_item_age_hours,
        stale=result.stale,
        ms=result.fetch_ms,
    )
    return result, articles


def collect(
    settings: Settings, window_from: datetime, window_to: datetime
) -> tuple[list[FeedFetchResult], list[Article]]:
    """全フィードを並列取得する."""
    results: list[FeedFetchResult] = []
    articles: list[Article] = []

    with ThreadPoolExecutor(max_workers=settings.max_workers) as pool:
        futures = [
            pool.submit(fetch_feed, feed, settings, window_from, window_to)
            for feed in settings.feeds
        ]
        for fut in futures:
            res, arts = fut.result()
            results.append(res)
            articles.extend(arts)

    # 同一記事が複数フィードに出ることがあるため id で一意化（weight の高い方を残す）
    best: dict[str, Article] = {}
    for art in articles:
        cur = best.get(art.id)
        if cur is None or art.feed_weight > cur.feed_weight:
            best[art.id] = art
    unique = sorted(best.values(), key=lambda a: a.published or "", reverse=True)

    ok_feeds = sum(1 for r in results if r.error is None)
    log.info(
        logger,
        "収集完了",
        feeds_ok=ok_feeds,
        feeds_total=len(results),
        feeds_stale=sum(1 for r in results if r.stale),
        articles_raw=len(articles),
        articles_unique=len(unique),
    )
    if not unique:
        raise AllFeedsEmptyError(
            f"全 {len(results)} フィードから対象期間の記事を取得できませんでした "
            f"(窓: {window_from.isoformat()} 〜 {window_to.isoformat()})"
        )
    return results, unique


def window_for(run_at: datetime, window_hours: int) -> tuple[datetime, datetime]:
    """実行時刻から対象期間を決める."""
    to = run_at.astimezone(UTC)
    return to - timedelta(hours=window_hours), to
