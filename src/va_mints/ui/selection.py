"""1ファイル（又は1結合グループ）の、右ペインの処理の項目の選択（ui/workspace.py）。

Workspace と、役目ごとに分けたモジュール（workspace_*.py）の両方から使うので、別に置く。
"""

from __future__ import annotations

from dataclasses import dataclass

from va_mints.ui.review_logic import ReviewItem, ReviewOption


@dataclass
class Selection:
    """1ファイル又は1結合グループの選択。"""

    item: ReviewItem
    options: list[ReviewOption]
    choices: dict[str, bool]
    # スタンプ位置調整（4.7）。None は config.yaml の既定のまま。
    stamp_margin_mm: tuple[float, float] | None = None
    # 以前の更新で、証拠番号スタンプを付けたファイルか（更新記録による）。True のとき、
    # スタンプのチェックは「付いている」ことを表す：入れたままなら何もせず（もう一度は
    # 付けない）、外して更新すると消す。
    stamped: bool = False
    # 利用者が左のペインで変えた名前（Ver300。要件定義書 4.3）。None は変えていない。
    renamed: str | None = None
    # ②画像を軽くするときの解像度（Ver300。要件定義書 4.13）。None は設定ファイルの既定。
    reduce_dpi: int | None = None
    # 以前の更新で済ませた圧縮（更新記録による）。スタンプの stamped と同じく、チェックは
    # 「済んでいる」ことを表す：入れたまま（②は同じ解像度のまま）なら何もせず、外したり
    # 解像度を変えたりして更新すると、元のファイルから作り直す。reduced_dpi は済ませた解像度。
    compressed: bool = False
    reduced_dpi: int | None = None
