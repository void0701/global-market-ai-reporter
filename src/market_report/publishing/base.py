"""公開先の抽象.

``pipeline`` はこのプロトコルにのみ依存するため、Phase 1（ローカル）から
Phase 2（S3）への差し替えで中核ロジックを変更する必要がない（docs/01 §3-1）。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Publisher(Protocol):
    """成果物の書き出し先."""

    def put_text(self, key: str, text: str, content_type: str = "text/html") -> str:
        """``key``（例 ``reports/2026-11-24.html``）にテキストを書く。戻り値は所在."""
        ...

    def get_text(self, key: str) -> str | None:
        """``key`` を読む。無ければ ``None``."""
        ...

    def list_keys(self, prefix: str) -> list[str]:
        """``prefix`` 配下のキーを列挙する."""
        ...

    def exists(self, key: str) -> bool: ...

    @property
    def location(self) -> str:
        """人間向けの所在表示（ログ用）."""
        ...
