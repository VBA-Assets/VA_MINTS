"""ファイルサイズ取得。

docs/03_requirements.md 4.2「ファイルサイズ」：単体サイズを取得し、
後続のバッチ分割（200MB合計）の対象とする。
"""

from __future__ import annotations

from pathlib import Path


def get_size_bytes(path: Path) -> int:
    return path.stat().st_size
