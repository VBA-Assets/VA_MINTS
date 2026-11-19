"""ページの縮小画像の列（中央のペインの左。Ver300。要件定義書 4.10）。

- クリックでそのページを大きなプレビューに出す。Ctrl+クリックで選択に足し引きし、
  Shift+クリックで範囲を選ぶ（削除・回転は、選んだページ全部に行う）。
- ドラッグで並べ替える（動かす先に横線を出す）。
- 右クリックでメニューを出す（呼び出し側が用意する）。

縮小画像は、ページごとの「鍵」（どのファイルの何ページ目を何度回したものか）で覚えて
おき、並べ替えた後も描き直さずに使う。描くのは少しずつ（after）で、画面を止めない。
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Hashable
from tkinter import ttk

from PIL import Image, ImageOps, ImageTk

from va_mints.ui import platform_support, theme

THUMB_W = 96   # 縮小画像の最大の幅（拡大率 100% のとき。画素）
THUMB_H = 124  # 縮小画像の最大の高さ
DRAG_START = 6  # これだけ動かしたらドラッグとみなす（画素）


class PageStrip(ttk.Frame):
    def __init__(
        self,
        master,
        on_current: Callable[[int], None],
        on_move: Callable[[list[int], int], None],
        on_context: Callable[[tk.Event], None],
        render: Callable[[Hashable, int, int], Image.Image | None],
    ) -> None:
        super().__init__(master)
        self._on_current = on_current
        self._on_move = on_move
        self._on_context = on_context
        self._render = render
        px = lambda n: theme.px(self, n)  # noqa: E731
        self._tw, self._th = px(THUMB_W), px(THUMB_H)
        self._cell_h = self._th + px(28)
        self._width = self._tw + px(24)
        self.canvas = tk.Canvas(
            self, width=self._width, bg=theme.WINDOW_BG, bd=0, highlightthickness=1,
            highlightbackground=theme.BORDER, highlightcolor=theme.ACCENT, takefocus=True,
        )
        self._scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._scroll.set)
        self._scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.Y, expand=True)

        self._keys: list[Hashable] = []
        self._labels: dict[int, str] = {}
        self._photos: dict[Hashable, ImageTk.PhotoImage] = {}
        self._failed: set[Hashable] = set()
        self._selected: set[int] = set()
        self._current = 0
        self._anchor = 0
        self._editable = False
        self._job: str | None = None
        self._press: tuple[int, int, int] | None = None  # (ページ, x, y)
        self._dragging = False

        c = self.canvas
        c.bind("<Button-1>", self._on_press)
        c.bind("<B1-Motion>", self._on_motion)
        c.bind("<ButtonRelease-1>", self._on_release)
        c.bind("<Button-3>", self._on_right)
        c.bind("<MouseWheel>", self._on_wheel)

    # ------------------------------------------------------------------ 外から
    def set_pages(
        self, keys: list[Hashable], current: int, editable: bool,
        labels: dict[int, str] | None = None,
    ) -> None:
        """並ぶページ（鍵の並び）を入れ替える。current のページを選び、見える所へ動かす。
        labels：枝番の始まるページの位置 → 枝番（例：甲005-2）。ページ番号の横に出し、
        2つ目からの枝番の上に区切りの線を引く（Ver300。4.11）。"""
        self._labels = dict(labels or {})
        self._keys = list(keys)
        self._editable = editable
        self._current = max(0, min(current, len(keys) - 1)) if keys else 0
        self._selected = {self._current} if keys else set()
        self._anchor = self._current
        self._redraw()
        self.see(self._current)
        self._schedule_render()

    def select(self, indices: list[int], current: int | None = None) -> None:
        self._selected = {i for i in indices if 0 <= i < len(self._keys)}
        if current is not None and 0 <= current < len(self._keys):
            self._current = self._anchor = current
        self._redraw_marks()
        self.see(self._current)

    def set_current(self, index: int) -> None:
        """大きなプレビューで別のページに移ったとき（スクロールバー・ホイール）。"""
        if not 0 <= index < len(self._keys):
            return
        self._current = self._anchor = index
        self._selected = {index}
        self._redraw_marks()
        self.see(index)

    @property
    def selected(self) -> list[int]:
        return sorted(self._selected) or ([self._current] if self._keys else [])

    def forget_images(self) -> None:
        """覚えた縮小画像を捨てる（ファイルの中身が変わったとき）。"""
        self._photos.clear()
        self._failed.clear()

    def see(self, index: int) -> None:
        if not self._keys:
            return
        total = len(self._keys) * self._cell_h
        top, bottom = self.canvas.yview()
        y0, y1 = index * self._cell_h / total, (index + 1) * self._cell_h / total
        if y0 < top:
            self.canvas.yview_moveto(y0)
        elif y1 > bottom:
            self.canvas.yview_moveto(max(0.0, y1 - (bottom - top)))

    # ------------------------------------------------------------------ 描く
    def _cell_top(self, index: int) -> int:
        return index * self._cell_h

    def _redraw(self) -> None:
        c = self.canvas
        c.delete("all")
        for i, key in enumerate(self._keys):
            y = self._cell_top(i)
            c.create_rectangle(
                2, y + 2, self._width - 2, y + self._cell_h - 2,
                outline="", fill=theme.WINDOW_BG, tags=("cell", f"cell{i}"),
            )
            photo = self._photos.get(key)
            cx, cy = self._width // 2, y + 6 + self._th // 2
            if photo is not None:
                c.create_image(cx, cy, image=photo, tags=(f"img{i}",))
            else:
                c.create_rectangle(
                    cx - self._tw // 3, cy - self._th // 2 + 4, cx + self._tw // 3, cy + self._th // 2 - 4,
                    outline=theme.BORDER, fill=theme.FIELD_BG, tags=(f"img{i}",),
                )
            label = self._labels.get(i)
            c.create_text(
                cx, y + self._cell_h - 12,
                text=f"{i + 1}　{label}" if label else str(i + 1),
                fill=theme.ACCENT if label else theme.NOTE, tags=(f"num{i}",),
            )
            if label and i > 0:
                c.create_line(2, y + 1, self._width - 2, y + 1, fill=theme.ACCENT, width=2)
        c.configure(scrollregion=(0, 0, self._width, max(1, len(self._keys) * self._cell_h)))
        self._redraw_marks()

    def _redraw_marks(self) -> None:
        c = self.canvas
        for i in range(len(self._keys)):
            selected = i in self._selected
            c.itemconfigure(
                f"cell{i}",
                fill=theme.PRESSED_BG if selected else theme.WINDOW_BG,
                outline=theme.ACCENT if i == self._current else "",
                width=2 if i == self._current else 1,
            )

    def _schedule_render(self) -> None:
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(10, self._render_next)

    def _render_next(self) -> None:
        """まだ描いていない縮小画像を1つ描く（見ているページの近くから）。"""
        self._job = None
        order = sorted(range(len(self._keys)), key=lambda i: abs(i - self._current))
        for i in order:
            key = self._keys[i]
            if key in self._photos or key in self._failed:
                continue
            image = None
            try:
                image = self._render(key, self._tw, self._th)
            except Exception:  # noqa: BLE001 - 縮小画像が描けなくても画面は止めない
                image = None
            if image is None:
                self._failed.add(key)
            else:
                image = ImageOps.expand(image.convert("RGB"), border=1, fill=theme.BORDER)
                self._photos[key] = ImageTk.PhotoImage(image, master=self)
                for j, k in enumerate(self._keys):
                    if k == key:
                        self.canvas.delete(f"img{j}")
                        cx, cy = self._width // 2, self._cell_top(j) + 6 + self._th // 2
                        self.canvas.create_image(cx, cy, image=self._photos[key], tags=(f"img{j}",))
                        self.canvas.tag_raise(f"img{j}", f"cell{j}")
            self._job = self.after(1, self._render_next)
            return

    # ------------------------------------------------------------------ マウス
    def _index_at(self, y: int) -> int | None:
        index = int(self.canvas.canvasy(y) // self._cell_h)
        return index if 0 <= index < len(self._keys) else None

    def _gap_at(self, y: int) -> int:
        gap = round(self.canvas.canvasy(y) / self._cell_h)
        return max(0, min(len(self._keys), gap))

    def _on_press(self, event: tk.Event) -> None:
        self.canvas.focus_set()
        index = self._index_at(event.y)
        if index is None:
            return
        ctrl = bool(event.state & 0x0004)
        shift = bool(event.state & 0x0001)
        if shift:
            lo, hi = sorted((self._anchor, index))
            self._selected = set(range(lo, hi + 1))
            self._current = index
        elif ctrl:
            self._selected ^= {index}
            self._current = self._anchor = index
            if not self._selected:
                self._selected = {index}
        else:
            if index not in self._selected:
                self._selected = {index}
            self._current = self._anchor = index
        self._press = (index, event.x, event.y)
        self._dragging = False
        self._redraw_marks()
        self._on_current(self._current)

    def _on_motion(self, event: tk.Event) -> None:
        if self._press is None or not self._editable:
            return
        _index, x0, y0 = self._press
        if not self._dragging and abs(event.y - y0) + abs(event.x - x0) < DRAG_START:
            return
        self._dragging = True
        gap = self._gap_at(event.y)
        y = gap * self._cell_h
        self.canvas.delete("dropline")
        self.canvas.create_line(4, y, self._width - 4, y, fill=theme.ACCENT, width=3, tags=("dropline",))
        # 端に来たら少しずつ送る
        if event.y < 12:
            self.canvas.yview_scroll(-1, "units")
        elif event.y > self.canvas.winfo_height() - 12:
            self.canvas.yview_scroll(1, "units")

    def _on_release(self, event: tk.Event) -> None:
        self.canvas.delete("dropline")
        press, dragging = self._press, self._dragging
        self._press, self._dragging = None, False
        if press is None:
            return
        if dragging:
            self._on_move(self.selected, self._gap_at(event.y))
        elif not (event.state & 0x0005):
            # ただのクリック：選んでいた複数のページのうちの1つなら、それだけを選ぶ
            self._selected = {press[0]}
            self._redraw_marks()

    def _on_right(self, event: tk.Event) -> None:
        index = self._index_at(event.y)
        if index is None:
            return
        if index not in self._selected:
            self._selected = {index}
            self._current = self._anchor = index
            self._redraw_marks()
            self._on_current(index)
        self._on_context(event)

    def _on_wheel(self, event: tk.Event) -> str:
        self.canvas.yview_scroll(
            platform_support.mousewheel_units(self, event.delta, -int(event.delta / 120) * 2), "units"
        )
        return "break"
