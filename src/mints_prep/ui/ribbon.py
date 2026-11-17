"""Word のリボンと同じ形のボタンの並び（VA Case の ribbon.py にならう）。

・ボタンを組（読み込み・書き出しなど）に分け、組の名前をボタンの下に小さく書く。組と組の間は縦の線で区切る。
・よく使うボタンは大きいアイコンの下に字（large）、ほかは小さいアイコンの右に字を縦に3つまで積む（small）。
・ボタンは、ふだんは枠も地もなく、マウスを重ねたときだけ淡い青の地と枠を出す（Word のリボンと同じ。スタイルは
  theme.py の Ribbon*）。主要な操作（読み込み・書き出し）だけは、いつも枠を出す（theme.py の RibbonAccent）。
・組の名前は、どの組も下端にそろえる。
・ボタンは Tab で止まらない（ttk のボタンは押すと入力先を取るので、takefocus を切る。一覧の操作が、
  ボタンを押した後も効くように）。
・幅に組が入りきらないときは、入りきらない組を次の段に送る（Word のように組を畳んで隠すことはしない）。
・マウスを重ねると、少し間をおいて説明書きを出す（tip。tooltip.py）。
"""

from __future__ import annotations

from tkinter import ttk

from mints_prep.ui.icons import icon_name
from mints_prep.ui.tooltip import Tooltip

ROWS = 3                # 小さいボタンを縦に積む数


class RibbonGroup(ttk.Frame):
    """リボンの1つの組。ボタンは左から列ごとに置く。"""

    def __init__(self, master, title, icons):
        super().__init__(master, style="Ribbon.TFrame")
        self.title = title
        self.icons = icons
        self.body = ttk.Frame(self, style="Ribbon.TFrame")
        self.body.grid(row=0, column=0, sticky="nsew", padx=2, pady=(2, 0))
        ttk.Label(self, text=title, style="RibbonGroup.TLabel", anchor="center").grid(row=1, column=0, sticky="sew")
        ttk.Separator(self, orient="vertical").grid(row=0, column=1, rowspan=2, sticky="ns", pady=(4, 4))
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)      # 組の高さがそろったとき、余りはボタンの側に回し、名前を下端にそろえる
        self._column = 0
        self._stack = 0         # 今の列に積んだ小さいボタンの数

    def _images(self, text, icon, large):
        name = icon or icon_name(text)
        if self.icons is None or not name:
            return {}
        normal, disabled = self.icons.image(name, large=large), self.icons.image(name, disabled=True, large=large)
        if normal is None:
            return {}
        return {"image": (normal, "disabled", disabled or normal), "compound": "top" if large else "left"}

    def large(self, text, command, icon=None, style="RibbonLarge.TButton", tip=None, **kw):
        """大きいボタン（アイコンの下に字）。1列を使う。tip：説明書き（文字列か、説明を返す関数）。"""
        b = ttk.Button(self.body, text=text, command=command, style=style, takefocus=False,
                       **self._images(text, icon, True), **kw)
        if tip:
            b.tooltip = Tooltip(b, tip)
        self._next_column()
        b.grid(row=0, column=self._column, rowspan=ROWS, sticky="ns", padx=1, pady=0)
        self._column += 1
        return b

    def small(self, text, command, icon=None, tip=None, **kw):
        """小さいボタン（アイコンの右に字）。今の列に縦に積み、ROWS 個で次の列へ。tip：説明書き。"""
        b = ttk.Button(self.body, text=text, command=command, style="Ribbon.TButton", takefocus=False,
                       **self._images(text, icon, False), **kw)
        if tip:
            b.tooltip = Tooltip(b, tip)
        if self._stack >= ROWS:
            self._next_column()
        b.grid(row=self._stack, column=self._column, sticky="w", padx=1, pady=0)
        self._stack += 1
        return b

    def dropdown(self, text, items, icon=None, tip=None):
        """小さいボタンの形の、押すとメニューが開くボタン（Word の［回転］と同じ）。items：
        （メニューの字、実行）の並び。置き方は small と同じ。"""
        import tkinter as tk

        b = ttk.Menubutton(self.body, text=text, style="Ribbon.TMenubutton", takefocus=False,
                           direction="below", **self._images(text, icon, False))
        menu = tk.Menu(b, tearoff=False)
        for label, command in items:
            menu.add_command(label=label, command=command)
        b.configure(menu=menu)
        b.menu = menu
        if tip:
            b.tooltip = Tooltip(b, tip)
        if self._stack >= ROWS:
            self._next_column()
        b.grid(row=self._stack, column=self._column, sticky="w", padx=1, pady=0)
        self._stack += 1
        return b

    def new_column(self):
        """次の小さいボタンを、新しい列の上から置く（3つ積む前に列を改めるとき）。"""
        self._next_column()

    def block(self, widget):
        """部品（フォルダの欄など）を、新しい列の上から置く。"""
        self._next_column()
        widget.grid(in_=self.body, row=0, column=self._column, rowspan=ROWS, sticky="nw", padx=(1, 6), pady=(1, 0))
        self._column += 1
        return widget

    def _next_column(self):
        if self._stack:
            self._column += 1
            self._stack = 0


class Ribbon(ttk.Frame):
    """組を左から並べる。幅に入りきらない組は次の段に送る。"""

    def __init__(self, master, icons):
        super().__init__(master, style="Ribbon.TFrame")
        self.icons = icons
        self.groups = []
        self._lines = []          # 段ごとの枠
        self._layout = None
        self.bind("<Configure>", lambda e: self.reflow(e.width))

    def group(self, title):
        g = RibbonGroup(self, title, self.icons)
        self.groups.append(g)
        self.reflow(self.winfo_width())
        return g

    def reflow(self, width):
        """幅 width に入るように、組を段に分けて置く（同じ並びなら置き直さない。Configure が続かないように）。"""
        if width <= 1:
            width = 10 ** 6
        rows, used = [[]], 0
        for g in self.groups:
            w = g.winfo_reqwidth()
            if rows[-1] and used + w > width:
                rows.append([])
                used = 0
            rows[-1].append(g)
            used += w
        layout = [[id(g) for g in row] for row in rows]
        if layout == self._layout:
            return
        self._layout = layout
        # 段ごとに枠を分け、組を左から詰めて置く（1つの表に段と列で置くと、2段目の組が1段目の同じ列の幅に広げられる）
        while len(self._lines) < len(rows):
            self._lines.append(ttk.Frame(self, style="Ribbon.TFrame"))
        for r, line in enumerate(self._lines):
            if r < len(rows):
                line.grid(row=r, column=0, sticky="w")
            else:
                line.grid_remove()
        for r, row in enumerate(rows):
            for c, g in enumerate(row):
                g.grid(in_=self._lines[r], row=0, column=c, sticky="nsw")
                g.lift(self._lines[r])      # 組は段の枠より先に作るので、枠の上に出す
