"""リボンのボタンのアイコン（VA Case の icons.py にならう）。

アイコンは Microsoft の Fluent UI System Icons（MIT License。icons\\LICENSE）で、VA Case・VA Document と
同じ2色の形。scripts\\make_ribbon_icons.py で SVG から大きめの PNG にしたもの（icons\\<名前>.png・
<名前>_disabled.png・<名前>_large.png）を、起動したときに Pillow で画面の拡大率に合った大きさに縮める
（拡大してもぼやけない。PyMuPDF〔AGPL〕を配るものに入れないため。設計原則1〔完全ローカル動作〕のとおり、
画像はすべて同梱ファイルで、外部通信はしない）。
"""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path


def _icon_dir() -> Path:
    """アイコンの置き場。PyInstaller の onefile 実行時は展開先（sys._MEIPASS）の
    mints_prep/ui/icons（mints_prep.spec の datas で同梱）、開発時はこのファイルの隣の icons。"""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        return base / "mints_prep" / "ui" / "icons"
    return Path(__file__).resolve().parent / "icons"


ICON_DIR = _icon_dir()
ICON_SIZE = 16          # 小さいアイコンの大きさ（拡大率 100% のとき。画素）
LARGE_ICON = 32         # 大きいボタンのアイコンの大きさ

# ボタンの字（改行と「…」を除く）→ アイコンの名前（icons\<名前>.png）
BUTTON_ICONS = {
    "ファイルを読み込み": "folder_open",
    "ファイルを更新": "save",
    "元のファイルに戻す": "arrow_undo",
    # 「ページ」の組（Ver300。2026-11-17 班長指示）。回転・やり直すは、元に戻すと同じく自作。
    "ページを削除": "delete",
    "ページを挿入": "document_add",
    "ファイルを分割": "document_page_break",
    "回転": "arrow_rotate",
    "枝番の区切り": "text_number_list_ltr",
    "元に戻す": "arrow_undo",
    "やり直す": "arrow_redo",
    # 「証拠説明書」の組（4.17。2026-11-18）。表のアイコンは VA Document・VA Request と同じもの。
    "作成": "table",
}


def icon_name(text: str) -> str | None:
    return BUTTON_ICONS.get(str(text).replace("\n", "").replace("…", ""))


class IconSet:
    """アイコンの画像を作って覚えておく（Tk の画像は、参照がなくなると消えるため）。"""

    def __init__(self, root: tk.Misc) -> None:
        self.root = root
        self.scale = max(1.0, root.winfo_fpixels("1i") / 96)        # 画面の拡大率
        self._cache: dict[tuple, object] = {}

    def image(self, name: str, disabled: bool = False, large: bool = False):
        """アイコンの画像（なければ None）。large なら大きいボタンの大きさで、色付きのアイコン（_large）が
        あればそれを使う（押せないときは、灰色の線画を大きくする）。"""
        size = round((LARGE_ICON if large else ICON_SIZE) * self.scale)
        key = (name, disabled, large, size)
        if key not in self._cache:
            candidates = [f"{name}_large.png"] if large and not disabled else []
            candidates.append(f"{name}_disabled.png" if disabled else f"{name}.png")
            path = next((ICON_DIR / c for c in candidates if (ICON_DIR / c).exists()), None)
            if path is None:
                return None
            from PIL import Image, ImageTk
            with Image.open(path) as img:
                small = img.convert("RGBA").resize((size, size), Image.LANCZOS)
            self._cache[key] = ImageTk.PhotoImage(small, master=self.root)
        return self._cache[key]
