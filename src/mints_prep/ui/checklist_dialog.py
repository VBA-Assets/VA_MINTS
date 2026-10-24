"""チェックボックス方式の確認ダイアログ（枝番ファイル結合〔4.7〕のグループ選択で使用）。

項目（枝番ファイル結合の候補グループ）ごとに個別のチェックボックスを持つ、一括実行／
スキップの二択ではないダイアログ。枝番ファイル結合は複数ファイルにまたがる決定であり、
逐次レビュー画面（write_review_dialog.py）の1ファイル単位の仕組みにはなじまない
ため、候補一覧をまとめて提示しつつグループ単位で選択できるようにする
（CLAUDE.md「絶対に譲れない設計原則」3の「チェックボックスで選択」要件に対応）。
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk

from mints_prep.ui.platform_support import mousewheel_units


@dataclass(frozen=True)
class ChecklistItem:
    key: str
    before_text: str
    after_text: str
    default_checked: bool = True


class ChecklistDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        title: str,
        description: str,
        items: list[ChecklistItem],
    ) -> None:
        super().__init__(parent)
        self.title(title)
        self.geometry("750x550")
        self.transient(parent)
        # 可視化前のgrab_setは環境によりTclError（grab failed: window not
        # viewable）になり得るため、失敗時は可視化後に再試行する
        # （write_review_dialog._grab_when_viewableと同方式）。
        from mints_prep.ui.write_review_dialog import _grab_when_viewable

        _grab_when_viewable(self)

        self.result: dict[str, bool] | None = None
        self._vars: dict[str, tk.BooleanVar] = {}

        ttk.Label(self, text=description, wraplength=700, justify="left", padding=12).pack(
            side=tk.TOP, fill=tk.X
        )
        self.count_label = ttk.Label(
            self, text=f"対象：{len(items)} 件", padding=(12, 0, 12, 8), anchor="w"
        )
        self.count_label.pack(side=tk.TOP, fill=tk.X)

        list_frame = ttk.Frame(self)
        list_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=12)
        canvas = tk.Canvas(list_frame)
        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.LEFT, fill=tk.Y)

        inner = ttk.Frame(canvas)
        inner_window = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        # ウィンドウのリサイズに内側フレームの幅を追随させる（固定幅のままだと
        # ウィンドウを広げても行が左側に張り付いたままになる）。
        canvas.bind(
            "<Configure>", lambda e: canvas.itemconfigure(inner_window, width=e.width)
        )
        # マウスホイールでスクロールできるようにする（wrapping_tableと同等の
        # 操作感）。Toplevel自身へのbindは、bindtags経由でこのダイアログ内の
        # 全ウィジェットに適用される（bind_allと違いアプリ全体のバインドを
        # 専有しないため、他のスクロール領域と衝突しない）。
        def _on_mousewheel(event: tk.Event) -> None:
            canvas.yview_scroll(
                mousewheel_units(self, event.delta, -1 * (event.delta // 120)), "units"
            )

        self.bind("<MouseWheel>", _on_mousewheel)

        for item in items:
            var = tk.BooleanVar(value=item.default_checked)
            self._vars[item.key] = var
            row = ttk.Frame(inner)
            row.pack(side=tk.TOP, fill=tk.X, pady=4, anchor="w")
            # 'clam'テーマのttk.Checkbuttonはチェック時のインジケータが「✕」
            # 形状に描画され、選択済みではなく利用不可のように見えるため、
            # 標準tk.Checkbuttonを使う（write_review_dialogと同方針）。
            tk.Checkbutton(row, variable=var).pack(side=tk.LEFT, anchor="n")
            ttk.Label(
                row,
                text=f"{item.before_text}\n  → {item.after_text}",
                justify="left",
                wraplength=650,
            ).pack(side=tk.LEFT, anchor="w")

        button_frame = ttk.Frame(self, padding=12)
        button_frame.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Button(button_frame, text="中止（結合しない）", command=self._on_cancel).pack(
            side=tk.RIGHT, padx=(6, 0)
        )
        ttk.Button(button_frame, text="この内容で実行", command=self._on_execute).pack(
            side=tk.RIGHT
        )

        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

    def _on_execute(self) -> None:
        self.result = {key: var.get() for key, var in self._vars.items()}
        self.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        self.destroy()


def ask_checklist(
    parent: tk.Misc, title: str, description: str, items: list[ChecklistItem]
) -> dict[str, bool] | None:
    """チェックリストダイアログを表示し、キーごとのON/OFF結果を返す。

    ウィンドウを閉じる・「中止」を選んだ場合はNone（呼び出し側はこれを
    「対象なし」と同義に扱うこと）。
    """
    dialog = ChecklistDialog(parent, title, description, items)
    parent.wait_window(dialog)
    return dialog.result
