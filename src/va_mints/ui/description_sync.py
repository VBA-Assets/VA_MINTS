"""証拠説明書（要件定義書 4.17）の、保存・PDF の作り直し・描き直しの流れと、作成・削除・プレビューのクリック。

ui/app.py の MainWindow にまぜて（多重継承で）使う。状態（self._workspace・self._tree など）は MainWindow が
持つ。右のペインの欄の組み立ては ui/description_form.py。

入力を変えたときの流れは、次の1本だけとする（2026-11-18 のリファクタリング。それまでは欄ごとに道が分かれ、
F5 と自動とで描き直しの手順も違い、頭書を続けて入れると PDF に入らないことがあった）。

    欄の変更 → _queue_description_input（少し待って、まとめて保存する）
             → _request_description_sync（少し待って、まとめて）
             → _sync_description（PDF を作り直し、一覧と、証拠説明書の PDF を見ていればプレビューを描き直す）

F5 は、待たずに _sync_description を呼ぶだけ（右のペインも組み直す）。
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox

from va_mints import APP_NAME, errors
from va_mints.processors.evidence_description import DATA_DIRNAME, DATA_NAME

_SAVE_DELAY_MS = 400  # 字を入れている間は、少し待ってからまとめて保存する
_SYNC_DELAY_MS = 800  # 保存の後、少し待ってから PDF を作り直す（続けて入れている間はまとめる）


class DescriptionSyncMixin:
    def _init_description_sync(self) -> None:
        self._desc_input_jobs: dict[str, str] = {}  # 欄 → 保存を待っている after の id
        self._desc_inputs: dict[str, object] = {}  # 欄 → 待っている保存（引数なしの手順）
        self._desc_sync_job: str | None = None
        # 右のペインの、行の見出し（号証 → ラベル）。プレビューでクリックした行へ送るため。
        self._desc_row_anchors: dict[str, tk.Widget] = {}
        self._preview_shown = None  # （描いたファイル、ページ、画像の大きさ）。ui/app.py が入れる
        self._label_cache: dict[tuple, tuple] = {}  # プレビューのクリック：ページごとの号証の位置

    # ------------------------------------------------------------------ 状態
    @property
    def _description_active(self) -> bool:
        """証拠説明書を作ったフォルダか（作った後は、いつも入力欄を出す。2026-11-18 班長指示）。"""
        ws = self._workspace
        return ws is not None and ws.description.exists and not ws.description_error

    def _showing_description_pdf(self) -> bool:
        ws, current = self._workspace, self._current
        return ws is not None and current is not None and ws.is_description_file(current)

    def _sync_description_button(self) -> None:
        """［作成］は、読み込んだフォルダで、まだ証拠説明書を作っていないときだけ押せる。"""
        ws = self._workspace
        can = ws is not None and not ws.description.exists
        self._desc_button.state(["!disabled"] if can else ["disabled"])

    # ------------------------------------------------------------------ 入力の保存
    def _queue_description_input(self, key: str, action) -> None:
        """欄 key の保存 action を、少し待ってから行い、その後に PDF の作り直しを頼む（続けて入れている
        間は、最後の1回にまとめる）。"""
        job = self._desc_input_jobs.pop(key, None)
        if job is not None:
            self.after_cancel(job)

        def run() -> None:
            self._desc_input_jobs.pop(key, None)
            self._desc_inputs.pop(key, None)
            action()
            self._request_description_sync()

        self._desc_input_jobs[key] = self.after(_SAVE_DELAY_MS, run)
        self._desc_inputs[key] = action

    def _flush_description_inputs(self) -> None:
        """待っている保存を、すぐに行う（作り直しの前・画面を替える前・読み込み直す前・更新の前）。"""
        jobs, inputs = dict(self._desc_input_jobs), dict(self._desc_inputs)
        self._desc_input_jobs.clear()
        self._desc_inputs.clear()
        for key, job in jobs.items():
            self.after_cancel(job)
            action = inputs.get(key)
            if action is not None:
                action()

    def _cancel_description_jobs(self) -> None:
        """待っている保存と作り直しを、行わずに取りやめる（証拠説明書を消すとき）。"""
        for job in self._desc_input_jobs.values():
            self.after_cancel(job)
        self._desc_input_jobs.clear()
        self._desc_inputs.clear()
        if self._desc_sync_job is not None:
            self.after_cancel(self._desc_sync_job)
            self._desc_sync_job = None

    def _report_save_error(self, exc: Exception) -> None:
        self._errors.record(exc, "証拠説明書の保存")
        messagebox.showerror(APP_NAME, f"証拠説明書のデータを保存できませんでした。\n{errors.describe(exc)}",
                             parent=self)

    # ------------------------------------------------------------------ 作り直しと描き直し（1本の道）
    def _request_description_sync(self, delay_ms: int = _SYNC_DELAY_MS) -> None:
        """少し待ってから _sync_description を行う（証拠説明書を作ったフォルダだけ。続けて頼まれたときは、
        最後の1回にまとめる）。名前の変更・元に戻すの後（号証・標目が変わる）にも、ui/app.py が頼む。"""
        if not self._description_active:
            return
        if self._desc_sync_job is not None:
            self.after_cancel(self._desc_sync_job)
        self._desc_sync_job = self.after(delay_ms, self._sync_description)

    def _sync_description(self, rebuild: bool = False, refresh: bool = False) -> None:
        """待っている保存を行い、証拠説明書の PDF を作り直し（中身が変わっていれば）、左の一覧と、
        証拠説明書の PDF を見ていれば中央のプレビューを描き直す。refresh：見ているファイルにかかわらず、
        中央のプレビューを描き直す。rebuild：右のペインも組み直す（見ていた位置のまま）。"""
        if self._desc_sync_job is not None:
            self.after_cancel(self._desc_sync_job)
            self._desc_sync_job = None
        self._flush_description_inputs()
        if self._workspace is None:
            return
        # 作り直す前に確かめる。証拠説明書の番号を変えると、PDF の名前が変わり（「証拠説明書（1）.pdf」）、
        # 作り直した後では、見ていたファイル（古い名前）が証拠説明書の PDF と分からなくなる
        # （2026-11-18、番号を入れた後の頭書がプレビューに入らなかった）。
        showing = self._showing_description_pdf()
        if self._description_active:
            self._write_description_pdf()
        self._refresh_tree_marks()
        if refresh or showing:
            self._refresh_current_view()
        if rebuild:
            self._rebuild_options_keep_scroll()

    def _on_key_f5(self, _event=None):
        """F5：待たずに、保存・作り直し・一覧と右のペインとプレビューの描き直しをする（2026-11-18 班長指示）。"""
        if self._workspace is not None:
            self._sync_description(rebuild=True, refresh=True)
            self._status_label.config(text="最新の状態にしました（F5）。")
        return "break"

    def _write_description_pdf(self, start: bool = False) -> Path | None:
        """証拠説明書の PDF を作り直す（中身が同じなら作らない）。作ったら、その位置。作れなかったときは、
        わけを画面の下の行に出して None。start：［作成］のとき（データのないフォルダでも作る）。"""
        ws = self._workspace
        if ws is None or ws.description_error or (not start and not ws.description.exists):
            return None
        try:
            path = ws.write_description_pdf()
        except PermissionError as exc:
            self._errors.record(exc, "証拠説明書の作成")
            self._status_label.config(
                text="証拠説明書のPDFを作り直せませんでした（他のアプリで開いている可能性があります）。"
            )
            return None
        except ValueError as exc:  # 同じ名前の、利用者のファイルがある
            if not errors.is_for_user(exc):
                self._errors.record(exc, "証拠説明書の作成")
            self._status_label.config(text=errors.describe(exc, detail=False))
            return None
        except Exception as exc:  # noqa: BLE001 - 作り直せなくても、入力は保存してある
            self._errors.record(exc, "証拠説明書の作成")
            self._status_label.config(text=f"証拠説明書のPDFを作り直せませんでした（{errors.reason(exc)}）。")
            return None
        if path is not None:
            self._status_label.config(text=f"証拠説明書のPDFを作り直しました（{path.name}）")
        return path

    def _refresh_current_view(self) -> None:
        """中央のプレビューとページの縮小画像を、いまのファイルの中身で描き直す。証拠説明書の PDF を
        見ていて、番号を変えて名前が変わったときは、新しい名前の PDF に替える。右のペインは組み直さない
        （入力中の欄を壊さないため）。"""
        from va_mints.ui.preview import get_page_count

        ws, record = self._workspace, self._current
        if ws is None or record is None:
            return
        paths = {r.path for r in ws.records}
        if record.path not in paths:
            pdf = ws.description_pdf()
            record = next((r for r in ws.records if r.path == pdf), None)
            if record is None:
                return
            self._current = record
            for iid, r in self._row_records.items():
                if r.path == record.path and self._tree.exists(iid):
                    self._tree.selection_set(iid)  # _current と同じなので、組み直しは起きない
                    self._tree.focus(iid)
                    self._tree.see(iid)
                    break
            # 右のペインの見出し（ファイルの名前）も、新しい名前にする（ペインは組み直さない）。
            title = getattr(self, "_desc_title_label", None)
            if title is not None and title.winfo_exists():
                title.config(text=record.path.name)
        count = ws.page_count(record) if ws.can_edit_pages(record) else get_page_count(record)
        self._page_count = max(1, count)
        self._page_index = min(self._page_index, self._page_count - 1)
        self._strip.forget_images()
        self._refresh_strip()
        self._update_page_nav()
        self._schedule_preview(0)

    def _rebuild_options_keep_scroll(self) -> None:
        """右のペインを組み直す（見ていた位置のまま。全部の行を並べたペインで、下の方の行を直したとき）。"""
        top = self._options_canvas.yview()[0]
        self._rebuild_options()
        self.update_idletasks()
        self._options_canvas.yview_moveto(top)

    # ------------------------------------------------------------------ 作成・削除
    def _create_description(self) -> None:
        """［作成］：証拠説明書の PDF とデータを作り、左の一覧に出す（PDF はいちばん上）。作った後は、
        右のペインに、いつも入力欄を出す。"""
        ws = self._workspace
        if ws is None:
            messagebox.showinfo(APP_NAME, "先に［ファイルを読み込み］でファイルを読み込んでください。", parent=self)
            return
        if ws.description_error:
            messagebox.showwarning(APP_NAME,
                f"証拠説明書のデータを読めませんでした。\n{ws.description_error}\n\n"
                "データを上書きしないよう、作りません。対象フォルダの中の"
                f"「{DATA_DIRNAME}」フォルダの {DATA_NAME} を確かめてください。",
                parent=self,
            )
            return
        self._flush_description_inputs()
        self._write_description_pdf(start=True)
        if not ws.description.exists:
            # 作れなかった（同じ名前の、利用者のファイルがあるなど）。わけは画面の下の行に出ている。
            messagebox.showerror(APP_NAME,
                f"証拠説明書を作れませんでした。\n{self._status_label.cget('text')}", parent=self)
        self._sync_description_button()
        self._refresh_tree_marks()
        self._rebuild_options()
        self._sync_page_buttons()

    def _delete_description(self) -> None:
        """一覧で証拠説明書の PDF を選んで［削除］：確認のうえ、PDF とデータ（「証拠説明書データ」フォルダ）を、
        その場でごみ箱に移す（下書きにはしない。2026-11-18 班長決定。画面の文では「入力」ではなく
        「データ」と言う。同日 班長指示）。"""
        from va_mints.ui import platform_support

        ws = self._workspace
        if ws is None:
            return
        if not messagebox.askokcancel(APP_NAME,
            f"証拠説明書のPDF（{ws.description_pdf().name}）とデータ（「{DATA_DIRNAME}」フォルダ）を、"
            "ごみ箱に移します。\n\n"
            "・データ（頭書と、各行の原本・写しの別・作成年月日・作成者・立証趣旨・備考）も消えます"
            "（ごみ箱から戻せます）。\n"
            "・証拠のファイルとファイル名は変わりません。\n"
            "・もう一度［作成］を押すまで、証拠説明書は作りません。\n\n"
            "消しますか？",
            icon=messagebox.WARNING, default=messagebox.CANCEL, parent=self,
        ):
            return
        # 待っている保存・作り直しは、消した後にフォルダを作り直さないよう、取りやめる。
        self._cancel_description_jobs()
        try:
            ws.delete_description(platform_support.send_to_recycle_bin)
        except OSError as exc:
            self._errors.record(exc, "証拠説明書の削除")
            messagebox.showerror(APP_NAME,
                f"証拠説明書を消せませんでした。\n{errors.describe(exc, detail=False)}\n\n"
                "証拠説明書のPDFを他のアプリで開いている場合は、閉じてからやり直してください。",
                parent=self,
            )
            return
        self._sync_description_button()  # また［作成］を押せる
        self._current = None
        self._refresh_tree_marks()
        rows = list(self._tree.get_children())
        if rows:
            self._tree.selection_set(rows[0])
            self._tree.focus(rows[0])
            self._on_select_file()
        else:
            self._show_options_placeholder()
            self._show_preview_text("一覧にファイルがありません。")
        self._status_label.config(text="証拠説明書のPDFとデータを、ごみ箱に移しました。")

    # ------------------------------------------------------------------ プレビューの行をクリック
    def _on_description_preview_click(self, event) -> None:
        """証拠説明書の PDF のプレビューで行をクリックしたら、右のペインを、その行の入力欄まで送る。
        1ページ目の表より上（頭書）なら、右のペインの先頭（頭書の欄）へ送る（2026-11-18 班長指示）。
        証拠説明書の PDF を選んでいないとき・表の外は、何もしない。"""
        shown = self._preview_shown
        if shown is None or not self._showing_description_pdf():
            return
        path, page_index, (image_w, image_h) = shown
        widget = self._preview_label
        # 画像は枠の中央に置かれ、まわりに1画素の線がある。
        x = event.x - (widget.winfo_width() - image_w) / 2 - 1
        y = event.y - (widget.winfo_height() - image_h) / 2 - 1
        label = row_label_at(self._label_cache, path, page_index, x, y, image_w - 2)
        if label is None:
            if page_index == 0 and 0 <= x <= image_w - 2 and 0 <= y <= image_h - 2:
                self._options_canvas.yview_moveto(0.0)
            return
        anchor = self._desc_row_anchors.get(label)
        if anchor is None or not anchor.winfo_exists():
            return
        self.update_idletasks()
        total = max(1, self._options_frame.winfo_height())
        self._options_canvas.yview_moveto(max(0.0, (anchor.winfo_y() - 4) / total))


def label_positions(path: Path, page_index: int) -> tuple[float, list[tuple[float, str]], float | None]:
    """PDF のページの、（ページの幅、[(号証の字の上端, 号証)…]（上から順）、表の見出しの行の下端）。
    単位はポイント。見出しの行が見つからなければ、下端は None。"""
    import re

    import pdfplumber

    pattern = re.compile(r"^[^\x00-\x7f][A-Za-z]?\d{3}(?:-\d+)?$")
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[page_index]
        words = page.extract_words()
        found = sorted(
            (float(w["top"]), w["text"]) for w in words
            if pattern.match(w["text"]) and float(w["x0"]) < page.width * 0.25  # 号証の列（左端）
        )
        # 見出しの行（「号証」「作成年月日」…）の下端：2行目の「年月日」の字の下端（欄の下の余白を足す）。
        heading = [float(w["bottom"]) for w in words if w["text"] == "年月日"]
        return float(page.width), found, (min(heading) + 3 if heading else None)


def row_label_at(cache: dict, path: Path, page_index: int, x: float, y: float, shown_width: float) -> str | None:
    """プレビューの画像の上の位置（x, y。画素）にある行の号証。行の途中（号証の字より下）も、その行とする。
    2ページ目からで、見出しの行より下・そのページの最初の号証より上は、前のページから続いている行
    （1ページに収まらずに分けた行の続き）とする。表の行より上（頭書・見出し）は None。"""

    def positions(index: int):
        key = (path, path.stat().st_mtime, index)
        if key not in cache:
            cache[key] = label_positions(path, index)
        return cache[key]

    try:
        page_width, found, heading_bottom = positions(page_index)
    except Exception:  # noqa: BLE001 - 送れないだけ
        return None
    if shown_width <= 0 or x < 0 or x > shown_width:
        return None
    top = y * page_width / shown_width
    # 号証の字の上端の少し上（欄の上の余白）から、その行とみなす。
    above = [label for row_top, label in found if row_top - 4 <= top]
    if above:
        return above[-1]
    if page_index > 0 and heading_bottom is not None and top > heading_bottom:
        try:
            for index in range(page_index - 1, -1, -1):
                earlier = positions(index)[1]
                if earlier:
                    return earlier[-1][1]  # 前のページの最後の行の続き
        except Exception:  # noqa: BLE001 - 送れないだけ
            return None
    return None
