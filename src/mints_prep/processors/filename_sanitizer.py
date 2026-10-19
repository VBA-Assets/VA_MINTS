"""ファイル名整形（要件定義書 4.5「ファイル名整形」）。

100文字超過の場合は自動短縮を提案し、禁止文字は置換（アンダースコア等）を
提案する。根拠：操作マニュアル2.3版6頁・別紙2エラー#53（100文字）、
別紙2エラー#22〜24（入力禁止文字）。

このモジュールは「提案の生成」のみを行う純粋関数であり、ファイルへの反映
（実際のリネーム・書き出し）は行わない。CLAUDE.md「絶対に譲れない設計原則」3
（ファイル変更処理は実行前に必ずユーザー確認を要する）に従い、提案内容を
確認UIで提示したうえでユーザーが実行を選択した場合にのみ、別途呼び出し側が
ファイルへの反映を行う。
"""

from __future__ import annotations

from pathlib import Path

from mints_prep.config import FilenameConfig
from mints_prep.models import FilenameFinding

DEFAULT_REPLACEMENT_CHAR = "_"


def needs_sanitization(finding: FilenameFinding) -> bool:
    """ファイル名整形（短縮・禁止文字置換）が必要かどうかを判定する。"""
    return finding.exceeds_max_length or bool(finding.forbidden_chars_found)


def sanitize_filename(
    name: str, config: FilenameConfig, replacement: str = DEFAULT_REPLACEMENT_CHAR
) -> str:
    """mintsの制約（100文字以内・禁止文字なし）に適合するファイル名を提案する。

    拡張子は保持したまま、禁止文字は置換し、なお100文字を超える場合は
    幹（拡張子を除いた部分）を切り詰める。
    """
    stem = Path(name).stem
    suffix = Path(name).suffix

    sanitized_stem = "".join(replacement if ch in config.forbidden_chars else ch for ch in stem)
    candidate = f"{sanitized_stem}{suffix}"

    overflow = len(candidate) - config.max_length
    if overflow > 0:
        sanitized_stem = sanitized_stem[:-overflow] if overflow < len(sanitized_stem) else ""
        candidate = f"{sanitized_stem}{suffix}"

    return candidate
