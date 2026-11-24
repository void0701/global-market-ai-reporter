"""重複排除.

標準ライブラリのみで実装する（docs/05 A-19）。``rapidfuzz`` は C 拡張のため
Lambda への配布が面倒になる一方、100〜200 件規模なら ``difflib`` で十分速い。

類似度 = max(トークン集合の Jaccard 係数, difflib の文字列類似度)。
Jaccard は語順の入れ替えに強く、difflib は言い換えに強いため両方を見る。

``cluster_id`` / 類似度 / アルゴリズムとパラメータを記事に書き戻して保存する
ことで、後から閾値や手法を変えて同一データで再実験できる（docs/05 A-85）。
"""

from __future__ import annotations

import logging
from difflib import SequenceMatcher

from ..config import Settings
from ..models import Article, DedupeInfo
from ..utils import logging as log
from .normalize import title_tokens

logger = logging.getLogger(__name__)

ALGORITHM = "jaccard+difflib"


def similarity(a: Article, b: Article) -> float:
    ta, tb = title_tokens(a.title_normalized), title_tokens(b.title_normalized)
    jaccard = len(ta & tb) / len(ta | tb) if (ta or tb) else 0.0
    ratio = SequenceMatcher(None, a.title_normalized, b.title_normalized).ratio()
    return max(jaccard, ratio)


def _select_with_feed_cap(ordered: list[Article], limit: int, max_per_feed: int) -> list[Article]:
    """媒体あたりの上限を設けて選抜する.

    上限を置く理由: FT の ``rss/home`` は総合フィードでコラム・社説を多く含み、
    上限なしでは選抜 15 件の過半を 1 媒体が占めて S&P500 と無関係な記事が
    混入した（実測）。クラスタが成立しない言い換え記事を拾えない以上、
    媒体の偏りを直接抑えて話題の多様性を担保する。

    上限は**ソフト**である。媒体数が少ない日に件数まで落とすのは本末転倒なので、
    枠が余ったら超過分で埋める。ただし **rank 順にそのまま詰めると偏りが戻る**
    ため、媒体をまたいだラウンドロビンで埋めて分布を保つ。
    """
    selected: list[Article] = []
    per_feed: dict[str, int] = {}
    overflow: dict[str, list[Article]] = {}

    for art in ordered:
        if len(selected) < limit and per_feed.get(art.feed_name, 0) < max_per_feed:
            selected.append(art)
            per_feed[art.feed_name] = per_feed.get(art.feed_name, 0) + 1
        else:
            overflow.setdefault(art.feed_name, []).append(art)

    # 媒体を順に 1 件ずつ拾って埋める（偏りを戻さない）
    while len(selected) < limit and any(overflow.values()):
        for feed_name in sorted(overflow, key=lambda f: (per_feed.get(f, 0), f)):
            if len(selected) >= limit:
                break
            bucket = overflow[feed_name]
            if not bucket:
                continue
            art = bucket.pop(0)
            selected.append(art)
            per_feed[feed_name] = per_feed.get(feed_name, 0) + 1
    return selected


def _representative(cluster: list[Article]) -> Article:
    """クラスタ代表を選ぶ: 媒体 weight → 情報量（要約長）→ 新しさ."""
    return max(
        cluster,
        key=lambda a: (a.feed_weight, a.summary_len, a.published or ""),
    )


def cluster_articles(
    articles: list[Article], threshold: float
) -> tuple[list[list[Article]], list[Article]]:
    """貪欲法でクラスタリングし、(クラスタ一覧, 代表記事一覧) を返す."""
    clusters: list[list[Article]] = []
    for art in articles:
        placed = False
        for cluster in clusters:
            score = max(similarity(art, member) for member in cluster)
            if score >= threshold:
                art.similarity_to_representative = round(score, 4)
                cluster.append(art)
                placed = True
                break
        if not placed:
            art.similarity_to_representative = 1.0
            clusters.append([art])

    representatives: list[Article] = []
    for idx, cluster in enumerate(clusters):
        rep = _representative(cluster)
        for member in cluster:
            member.cluster_id = idx
            member.is_representative = member is rep
            if member is not rep:
                member.similarity_to_representative = round(similarity(member, rep), 4)
            else:
                member.similarity_to_representative = 1.0
        representatives.append(rep)
    return clusters, representatives


def run(
    articles: list[Article], settings: Settings, *, threshold: float | None = None
) -> tuple[list[Article], DedupeInfo]:
    """重複排除して AI に渡す記事を選ぶ。``articles`` は副作用で更新される."""
    th = settings.similarity_threshold if threshold is None else threshold
    clusters, representatives = cluster_articles(articles, th)

    # クラスタの大きさ（= 複数媒体が報じた度合い）と媒体 weight で優先順位を付ける
    def rank(rep: Article) -> tuple[float, float, str]:
        size = len(clusters[rep.cluster_id]) if rep.cluster_id is not None else 1
        return (rep.feed_weight * (1 + 0.15 * (size - 1)), float(size), rep.published or "")

    ordered = sorted(representatives, key=rank, reverse=True)
    selected = _select_with_feed_cap(
        ordered, settings.max_articles_to_analyze, settings.max_per_feed
    )
    selected_ids = {a.id for a in selected}
    for art in articles:
        art.selected_for_analysis = art.id in selected_ids

    info = DedupeInfo(
        algorithm=ALGORITHM,
        threshold=th,
        input_count=len(articles),
        cluster_count=len(clusters),
        selected_count=len(selected),
    )
    log.info(
        logger,
        "重複排除",
        input=info.input_count,
        clusters=info.cluster_count,
        selected=info.selected_count,
        threshold=th,
        largest_cluster=max((len(c) for c in clusters), default=0),
    )
    # 選抜順（優先度順）で返す
    return selected, info
