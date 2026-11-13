"""ボタンの説明書き（マウスを重ねると、少し間をおいて出る。VA Case の tooltip.py にならう）。

見た目は Office と同じく、白地に細い灰色の枠（theme の色）。ボタンから離れる・押すと消える。説明は、文字列か、
いまの説明を返す関数（字が変わるボタン）。
説明はボタンの下に出す。above なら上に出す（画面の下端近くのボタン。下に出すと、タスクバーに隠れることがある）。
"""

from __future__ import annotations

import tkinter as tk

from mints_prep.ui import theme

DELAY_MS = 500
WRAP = 420          # 説明の折り返しの幅（画素。拡大率 100% のとき）。Tk の折り返しは行頭の禁則を守らないので、文ごとに改行する


def _by_sentence(text: str) -> str:
    """文ごとに改行する（「。」の後。すでに改行のある所と、文の終わりは除く）。"""
    return text.replace("。", "。\n").replace("。\n\n", "。\n").rstrip("\n")


class Tooltip:
    def __init__(self, widget, text, above: bool = False) -> None:
        self.widget, self.text, self.above = widget, text, above
        self._job = None
        self._win = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def current_text(self) -> str:
        return self.text() if callable(self.text) else self.text

    def _schedule(self, event=None) -> None:
        self.hide()
        self._job = self.widget.after(DELAY_MS, self.show)

    def show(self) -> None:
        self._job = None
        text = self.current_text()
        if self._win is not None or not text or not self.widget.winfo_viewable():    # 説明が空なら出さない
            return
        x = self.widget.winfo_rootx() + 4
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self._win = win = tk.Toplevel(self.widget)
        win.withdraw()                                  # 位置を決めてから出す（出してからでは位置が効かない）
        win.wm_overrideredirect(True)
        win.configure(background=theme.BORDER)          # 1画素の灰色の枠
        tk.Label(win, text=_by_sentence(text), justify="left", wraplength=theme.px(self.widget, WRAP),
                 background=theme.FIELD_BG, foreground=theme.TEXT, padx=8, pady=5).pack(padx=1, pady=1)
        win.update_idletasks()
        # 画面の右端・下端からはみ出さないようにする
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        x = min(x, sw - w - 4)
        if self.above or y + h > sh:
            y = self.widget.winfo_rooty() - h - 4
        win.wm_geometry(f"{w}x{h}+{x}+{y}")
        win.deiconify()
        win.lift()

    def hide(self, event=None) -> None:
        if self._job is not None:
            self.widget.after_cancel(self._job)
            self._job = None
        if self._win is not None:
            self._win.destroy()
            self._win = None
