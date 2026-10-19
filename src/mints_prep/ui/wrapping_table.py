"""セル内テキストの折り返し表示に対応した一覧表。

ttk.Treeviewはセル内テキストの折り返し表示に対応していない（列幅を超える
文章は単純に切り詰められ、横スクロールしても画面外に隠れた列が見える
だけで、1セル内の文章そのものは折り返されない）。そのため、Frame/Labelを
組み合わせた自作の一覧表で代替する。各Labelにwraplengthを設定して折り返し、
行の高さは内容量に応じてTkinterのgridジオメトリマネージャが自動調整する。

行数が多い場合（数百件規模）は列数×行数の分だけウィジェットを生成するため、
Treeviewより描画コストが高くなる。本ツールの想定利用規模（1事件あたり数十〜
中程度の百件台）では実用上問題ない想定だが、極端に大量のファイルを含む
フォルダでは描画が遅くなる可能性がある点に留意する。
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk


@dataclass(frozen=True)
class ColumnSpec:
    key: str
    label: str
    width: int  # ピクセル幅（wraplengthの基準にもなる）
    stretch: bool = False  # ウィンドウ幅に余裕がある場合に追加の幅を分け合うか


class WrappingTable(ttk.Frame):
    # チェックボックス列の幅（ピクセル）。一次資料に定めはなく、ヘッダーの
    # 全選択チェックボックス（「対象」ラベル付き）が収まる実装上の値。
    _CHECKBOX_COLUMN_WIDTH = 60

    def __init__(
        self,
        parent: tk.Widget,
        columns: list[ColumnSpec],
        tag_colors: dict[str, str] | None = None,
        header_bg: str = "#e8e8e8",
        with_checkbox: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(parent, **kwargs)
        self._columns = columns
        self._tag_colors = tag_colors or {}
        self._with_checkbox = with_checkbox
        self._row_frames: list[tk.Frame] = []
        self._row_values: list[tuple[str, ...]] = []
        self._check_vars: list[tk.BooleanVar] = []
        self._check_enabled: list[bool] = []
        self._select_all_var = tk.BooleanVar(value=True)

        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)

        # ヘッダー行（スクロールしない固定行）。本体側は縦スクロールバーの分だけ
        # 実際の表示幅が狭くなるため、ヘッダー側にも同じ幅の空白セルを右端に
        # 置いて列位置を揃える（揃えないと列がずれて見える）。
        header_container = tk.Frame(self)
        header_container.grid(row=0, column=0, sticky="ew")
        header_container.columnconfigure(0, weight=1)

        self._header = tk.Frame(header_container, bg=header_bg)
        self._header.grid(row=0, column=0, sticky="ew")
        self._configure_row_columns(self._header)
        column_offset = 1 if self._with_checkbox else 0
        if self._with_checkbox:
            # 対象選択チェックボックス列（4.1節）：各ファイルを今回の書き出し
            # 対象に含めるかどうかを選べるようにする。外部アプリで修正した
            # ファイルだけを再処理したい場合に、フォルダ全体を毎回やり直さず
            # 済むようにするための選択列。ヘッダー自体を全選択チェックボックス
            # とし、ONで全行ON・OFFで全行OFFに一括切替できるようにする
            # （操作不可の行〔書き出し対象になり得ないC・D分類〕は影響を
            # 受けず、常にOFFのまま）。
            tk.Checkbutton(
                self._header,
                text="対象",
                variable=self._select_all_var,
                command=self._on_select_all_toggle,
                font=("", 9, "bold"),
                bg=header_bg,
                activebackground=header_bg,
                padx=4,
                pady=2,
            ).grid(row=0, column=0)
        for i, col in enumerate(columns):
            tk.Label(
                self._header,
                text=col.label,
                anchor="w",
                justify="left",
                font=("", 9, "bold"),
                bg=header_bg,
                wraplength=col.width - 12,
                padx=6,
                pady=4,
            ).grid(row=0, column=i + column_offset, sticky="ew")

        # 本体（縦スクロール可能）
        body = ttk.Frame(self)
        body.grid(row=1, column=0, sticky="nsew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        self._canvas = tk.Canvas(body, highlightthickness=0)
        vsb = ttk.Scrollbar(body, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=vsb.set)
        self._canvas.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        self._inner = tk.Frame(self._canvas)
        self._inner.columnconfigure(0, weight=1)
        self._inner_window = self._canvas.create_window((0, 0), window=self._inner, anchor="nw")

        self._inner.bind("<Configure>", self._on_inner_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        self._canvas.bind("<Enter>", self._enable_mousewheel)
        self._canvas.bind("<Leave>", self._disable_mousewheel)

        self.update_idletasks()
        header_spacer = tk.Frame(header_container, width=vsb.winfo_reqwidth(), bg=header_bg)
        header_spacer.grid(row=0, column=1, sticky="ns")
        header_spacer.grid_propagate(False)

    # ------------------------------------------------------------------ 内部処理
    def _configure_row_columns(self, frame: tk.Frame) -> None:
        column_offset = 1 if self._with_checkbox else 0
        if self._with_checkbox:
            frame.columnconfigure(0, minsize=self._CHECKBOX_COLUMN_WIDTH, weight=0)
        for i, col in enumerate(self._columns):
            frame.columnconfigure(
                i + column_offset, minsize=col.width, weight=1 if col.stretch else 0
            )

    def _on_inner_configure(self, _event: tk.Event) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self._canvas.itemconfig(self._inner_window, width=event.width)

    def _enable_mousewheel(self, _event: tk.Event) -> None:
        self._canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _disable_mousewheel(self, _event: tk.Event) -> None:
        self._canvas.unbind_all("<MouseWheel>")

    def _on_mousewheel(self, event: tk.Event) -> None:
        self._canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _on_select_all_toggle(self) -> None:
        """ヘッダーの全選択チェックボックス：ONで全行ON・OFFで全行OFF。

        操作不可（checkable=False）の行は書き出し対象になり得ないため
        影響を受けず、常にOFFのまま。
        """
        value = self._select_all_var.get()
        for var, enabled in zip(self._check_vars, self._check_enabled):
            if enabled:
                var.set(value)

    # ------------------------------------------------------------------ 公開API
    def clear(self) -> None:
        for row in self._row_frames:
            row.destroy()
        self._row_frames.clear()
        self._row_values.clear()
        self._check_vars.clear()
        self._check_enabled.clear()
        # 再読み込み後の既定は「全ファイル対象」のため、ヘッダーの全選択
        # チェックボックスもONへ戻す。
        self._select_all_var.set(True)

    def add_row(
        self,
        values: tuple[str, ...],
        tag: str | None = None,
        checked: bool = True,
        checkable: bool = True,
    ) -> None:
        """チェックボックス列を有効化している場合、checkedが行の初期チェック
        状態になる（既定はON）。checkable=Falseの行はチェックボックスを
        操作不可（グレーアウト）で表示する（そもそも選択の対象になり得ない
        行〔書き出し対象外の分類等〕であることを視覚的に示すため。呼び出し側
        は通常checked=Falseと組み合わせる）。チェックボックス列を無効化して
        いる場合はいずれも無視される。"""
        bg = self._tag_colors.get(tag, "white") if tag else "white"
        row = tk.Frame(self._inner, bg=bg)
        row.grid(row=len(self._row_frames), column=0, sticky="ew")
        self._configure_row_columns(row)
        column_offset = 0
        if self._with_checkbox:
            var = tk.BooleanVar(value=checked)
            self._check_vars.append(var)
            self._check_enabled.append(checkable)
            tk.Checkbutton(
                row,
                variable=var,
                bg=bg,
                activebackground=bg,
                state=tk.NORMAL if checkable else tk.DISABLED,
            ).grid(row=0, column=0, sticky="n", pady=4)
            column_offset = 1
        for i, (col, value) in enumerate(zip(self._columns, values)):
            tk.Label(
                row,
                text=value,
                anchor="nw",
                justify="left",
                bg=bg,
                wraplength=col.width - 12,
                padx=6,
                pady=4,
            ).grid(row=0, column=i + column_offset, sticky="new")
        self._row_frames.append(row)
        self._row_values.append(values)

    def get_row(self, index: int) -> dict[str, str]:
        """行番号（0始まり）から、列キーと表示値の対応を取得する（主にテスト用）。"""
        return dict(zip((c.key for c in self._columns), self._row_values[index]))

    def get_checked_states(self) -> list[bool]:
        """各行のチェックボックス状態を、add_rowを呼んだ順（＝行の並び順）で
        返す。with_checkbox=Falseの場合は空リストを返す。"""
        return [var.get() for var in self._check_vars]

    @property
    def row_count(self) -> int:
        return len(self._row_frames)
