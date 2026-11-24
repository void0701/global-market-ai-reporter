"""ローカルファイルシステムへの公開（Phase 1）."""

from __future__ import annotations

import logging
from pathlib import Path

from ..utils import logging as log

logger = logging.getLogger(__name__)


class LocalPublisher:
    """``output_dir`` 配下にそのままのキー構造で書き出す."""

    def __init__(self, output_dir: Path):
        self.root = Path(output_dir)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        root = self.root.resolve()
        if not str(path).startswith(str(root)):
            raise ValueError(f"出力先がルート外です: {key}")
        return path

    def put_text(self, key: str, text: str, content_type: str = "text/html") -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        log.info(logger, "書き出し", key=key, bytes=len(text.encode("utf-8")))
        return str(path)

    def get_text(self, key: str) -> str | None:
        path = self._path(key)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def list_keys(self, prefix: str) -> list[str]:
        base = self._path(prefix)
        if not base.exists():
            return []
        root = self.root.resolve()
        return sorted(str(p.resolve().relative_to(root)) for p in base.rglob("*") if p.is_file())

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    @property
    def location(self) -> str:
        return str(self.root)
