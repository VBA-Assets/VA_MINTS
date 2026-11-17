"""VA mints メインウィンドウ（tkinter）。

Ver200 から、画面を3つのペインに分け、対象フォルダの中のファイルを
その場で更新する方式にした（2026-11-16 班長指示。要件定義書 第3章・第4章冒頭）。

- 左のペイン：読み込んだファイルの名前だけ（分類A〜Dで行を色分け。見出し・列はない）。
- 中央のペイン：左で選んだファイルのプレビュー。右のペインでチェックを付けた処理
  （証拠番号スタンプ・用紙を A4 にそろえる など）を行った状態を表示し、チェックを付け外しすると
  描き直す。ページは、右端のスクロールバー（又はホイール）で送る。
- 右のペイン：処理の項目。すべての項目（PROCESS_ITEMS）を、ファイルを選ぶ前から
  いつも同じ並びで表示し、そのファイルに適用するものにチェックが付く（必要と判断
  したものには、読み込んだときに自動で付ける。ui/review_logic.py の既定値）。
  そのファイルに当てはまらない項目は、チェックなし・操作不可で表示する。本ツール
  では対処できない（検出のみの）事項は、項目の上に赤字で出す。
（2026-11-16 班長指示：一覧の「分類」「処理」の列と見出し、プレビューの上のファイル名、
「前のページ」「次のページ」のボタン、右ペインの「B：要修正」等のラベル、分類ごとの
件数の行は、不要として外した。）

［ファイルを更新］は、すべてのファイルを、いまのチェックのとおりにまとめて更新する。
ファイルの内容・体裁を変更する処理のため、CLAUDE.md「絶対に譲れない設計原則」3に
従い、押したときに、処理の種類ごとの件数と、まだプレビューを表示していない
ファイルの数を示して、確認を得る。更新する前のファイルは、同じフォルダの
「元のファイル」フォルダに残し（processors/updater.py）、［元のファイルに戻す］で戻せる。
更新が済むと読み込み直すので、済んだ項目のチェックは外れる。

選択の状態と更新の計画は ui/workspace.py（Tk に依存しない）に置く。
"""

from __future__ import annotations

import dataclasses
import os
import queue
import sys
import tempfile
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import ImageOps, ImageTk

from mints_prep import __version__, diagnostics
from mints_prep.config import Config, EvidenceStampConfig, load_config
from mints_prep.models import Classification, FileRecord, ScanResult
from mints_prep.processors.compress import compressed_size
from mints_prep.processors.report_writer import format_evidence_sequence_warnings
from mints_prep.processors.updater import (
    BACKUP_DIRNAME,
    Manifest,
    discard_unused_materials,
    has_restorable_update,
    isolate_shared_pages,
    remove_file,
    restore_all,
    save_choices,
)
from mints_prep.scanner.walker import scan_folder
from mints_prep.ui import platform_support, theme
from mints_prep.ui.background import BackgroundJobs
from mints_prep.ui.formatting import describe_expected_processing
from mints_prep.ui.icons import IconSet
from mints_prep.ui.preview import (
    fit_preview,
    get_page_count,
    render_preview,
    render_preview_with_stamp,
)
from mints_prep.ui.review_logic import build_pane_warnings, orientation_notice
from mints_prep.ui.page_strip import PageStrip
from mints_prep.ui.ribbon import Ribbon
from mints_prep.ui.theme import apply_theme
from mints_prep.ui.tooltip import Tooltip
from mints_prep.user_settings import (
    STAMP_FONT_SIZE_MAX,
    STAMP_FONT_SIZE_MIN,
    apply_user_settings,
    load_user_settings,
    parse_geometry,
    save_user_settings,
)
from mints_prep.ui.workspace import (
    MERGE_KEY,
    PaneView,
    Selection,
    Workspace,
    apply_update,
    describe_failure,
    describe_update_plan,
    format_skipped,
    unstamped_image_line,
)

_CLASSIFICATION_TAGS = {
    Classification.A_COMPLIANT: "cls_a",
    Classification.B_NEEDS_FIX: "cls_b",
    Classification.C_NEEDS_CONVERSION: "cls_c",
    Classification.D_UNSUPPORTED: "cls_d",
}

_TAG_COLORS = {
    "cls_a": "#e6f4ea",
    "cls_b": "#fff4e5",
    "cls_c": "#e8f0fe",
    "cls_d": "#f1f1f1",
}

# ウィンドウとメッセージの題名。VAプロジェクト共通の決まり（2026-11-16 班長決定）：
# メインウィンドウの題名はツールの名前だけ、メッセージの題名もツールの名前とする
# （完了・確認・エラーの別は、題名ではなくアイコンと本文で伝える）。
APP_TITLE = "VA mints"

# 右ペインにいつも表示する処理の項目（キー、当てはまらないときに出す名前）。
# キーは ui/review_logic.py の ReviewOption.key・ui/workspace.py の MERGE_KEY と同じ。
# Tkのインスタンス化を伴わずに単体テストできるよう、モジュールレベルの定数とする。
PROCESS_ITEMS = (
    ("sanitize", "ファイル名整形"),
    ("strip_metadata", "メタデータ除去"),
    ("strip_hidden", "付箋・コメント・添付ファイルの除去"),
    ("fit_a4", "用紙をA4にそろえる"),
    ("stamp", "証拠番号スタンプ"),
    (MERGE_KEY, "枝番ファイル結合"),
    ("reduce", "画像を軽くする"),
    ("compress", "画質を損なわない圧縮"),
)
# 「ファイルを小さくする」の枠にまとめる項目（PROCESS_ITEMS の末尾に続けて並べる）。
COMPRESS_KEYS = ("reduce", "compress")

# ［オプション］で選べる証拠番号スタンプの見た目（画面の文、config.STAMP_STYLES の値）。
STAMP_STYLE_CHOICES = (("黒字", "black"), ("赤字", "red"), ("赤字に赤枠", "red_boxed"))

# スタンプ位置調整ボタン1回分の移動量（mm）。一次資料に定めはなく、UI操作上の
# 実装値（CLAUDE.md「数値パラメータの設定ファイル化」原則が対象とする
# mints側の制約値〔200MB・100文字等〕とは性質が異なるため、設定ファイル化は
# せずここに定数として置く）。
_POSITION_STEP_MM = 2.0

# 本ツールでは対処できない（検出のみの）項目の文字色。操作不可に加えて赤字に
# することで、対処が必要な検出事項であることを一目で分かるようにする。
_UNFIXABLE_TEXT_COLOR = "#c00000"


def _virtual_screen(widget: tk.Misc) -> tuple[int, int, int, int]:
    """すべてのモニターを合わせた画面の（左, 上, 幅, 高さ）。ウィンドウを戻す位置が画面の
    中かを確かめるために使う。Windows は API で、それ以外は Tk の画面の大きさで調べる。"""
    if os.name == "nt":
        try:
            import ctypes

            metrics = ctypes.windll.user32.GetSystemMetrics
            # SM_XVIRTUALSCREEN=76, SM_YVIRTUALSCREEN=77, SM_CXVIRTUALSCREEN=78, SM_CYVIRTUALSCREEN=79
            left, top, width, height = metrics(76), metrics(77), metrics(78), metrics(79)
            if width > 0 and height > 0:
                return left, top, width, height
        except (AttributeError, OSError):
            pass
    return 0, 0, widget.winfo_screenwidth(), widget.winfo_screenheight()


def _window_icon_path() -> Path:
    """ウィンドウアイコン（assets/VA_mints.ico）のパスを解決する。

    PyInstallerのonefile実行時は展開先（sys._MEIPASS）へ同梱されたものを、
    ソースから直接実行する開発時はリポジトリ直下のassets/を参照する
    （config.pyの_default_config_pathと同じ切替方針）。
    """
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    else:
        base = Path(__file__).resolve().parents[3]
    return base / "assets" / "VA_mints.ico"


def _grab_when_viewable(dialog: tk.Toplevel) -> None:
    """モーダル化（grab_set）を、ウィンドウが可視になってから行う。

    可視化前のgrab_setは環境によりTclError（grab failed: window not viewable）
    になり得る。wait_visibility()で待つ方式は、テスト等でウィンドウが表示され
    ない環境では永久に戻らないため使わず、失敗時にafterで再試行する。
    """
    try:
        dialog.grab_set()
    except tk.TclError:
        dialog.after(50, lambda: _grab_when_viewable(dialog) if dialog.winfo_exists() else None)


def preview_placeholder_text(record: FileRecord) -> str:
    """プレビューを出せないときに、代わりに出す文。"""
    if record.pdf_finding is not None and record.pdf_finding.has_password:
        return "プレビュー非対応\n（パスワード付きファイル）"
    if record.detected_format in ("MP3", "MP4"):
        return "プレビュー非対応\n（音声・動画ファイル）"
    if record.detected_format not in ("PDF", "JPEG", "PNG"):
        return "プレビュー非対応\n（mints「記録」の対応形式ではないファイル）"
    return "プレビューを生成できませんでした"


def display_name(record: FileRecord, root: Path) -> str:
    """左ペインに出す名前。サブフォルダ・「提出用_NN」の中のファイルは、フォルダから書く。"""
    try:
        return str(record.path.relative_to(root))
    except ValueError:
        return record.path.name


def _format_size(size_bytes: int) -> str:
    """左の一覧に出す大きさ（1MB 以上は「12.3MB」、それ未満は「456KB」）。"""
    mb = size_bytes / (1024 * 1024)
    if mb >= 1:
        return f"{mb:.1f}MB"
    return f"{max(1, round(size_bytes / 1024))}KB"


class MainWindow(tk.Tk):
    def __init__(self, config: Config | None = None) -> None:
        super().__init__()
        # config.yaml の設定（配布物の既定）と、［ファイル］→［オプション］で利用者が変えた
        # 設定（%APPDATA%\VA mints\settings.json）を重ねたものを使う（user_settings.py）。
        self._base_config = config or load_config()
        self._user_settings = load_user_settings()
        self.config_obj = apply_user_settings(self._base_config, self._user_settings)
        # 画面全体のテーマ（フラットデザイン・Microsoft系ダイアログの質感）。
        # ウィジェット生成前に一度だけ適用する（詳細はui/theme.py参照）。
        apply_theme(self)
        # ウィンドウの題名は、ツールの名前だけにする（VAプロジェクト共通の決まり。
        # 2026-11-16 班長決定。説明と版は［ヘルプ］の「VA mints について」に書く）。
        self.title(APP_TITLE)
        # タイトルバー・タスクバーのアイコン（実行ファイル自体のアイコンは
        # mints_prep.specのEXE(icon=...)で別途埋め込まれる）。アイコンが
        # 見つからない・読めない場合は既定のままにする（起動を妨げない）。
        icon_path = _window_icon_path()
        if icon_path.exists():
            try:
                self.iconbitmap(str(icon_path))
            except tk.TclError:
                pass
        self.minsize(960, 560)

        self._input_dir_var = tk.StringVar(value="")
        self._scan_result: ScanResult | None = None
        self._workspace: Workspace | None = None
        self._queue: "queue.Queue[object]" = queue.Queue()
        # 起きた誤りの記録（型と場所だけ。診断情報〔ヘルプ→診断情報をコピー〕に載せる）。
        self._errors = diagnostics.ErrorLog()

        # いま左ペインで選んでいるファイルと、そのプレビューの状態。
        self._current: FileRecord | None = None
        self._row_records: dict[str, FileRecord] = {}
        # 裏で行う計算（圧縮の大きさの見込み）。
        self._background = BackgroundJobs(self)
        self._estimate_label: ttk.Label | None = None
        self._page_index = 0
        self._page_count = 1
        self._preview_photo: ImageTk.PhotoImage | None = None  # GC対策で参照を保持
        self._preview_job: str | None = None
        self._preview_size = (0, 0)
        self._wrap_widgets: list[tk.Widget] = []
        self._position_label: ttk.Label | None = None
        # 更新・復元の後の読み込み直しで、選び直すファイル（左ペインの名前）。
        self._reselect_name: str | None = None

        self._build_menu()
        self._build_widgets()
        self._sync_page_buttons()
        # ページの編集（Ver300）のショートカット。文字を入れる欄では、その欄に任せる。
        self.bind("<Control-z>", self._on_key_undo)
        self.bind("<Control-Z>", self._on_key_undo)
        self.bind("<Control-y>", self._on_key_redo)
        self.bind("<Control-Y>", self._on_key_redo)
        self.bind("<Delete>", self._on_key_delete)
        self.bind("<F2>", self._begin_rename)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        # 大きさは、メニューを作った後に決める（先に決めると、Tk がメニューの高さの分だけ
        # 縮めてしまい、閉じて開くたびに小さくなる）。
        self._restore_window()

    def report_callback_exception(self, exc_type, exc, tb) -> None:
        """画面の処理（ボタン・キーなど）で起きた、予期しない誤りを覚える（Tk の既定どおり、出力にも出す）。"""
        self._errors.record(exc, "画面の処理")
        super().report_callback_exception(exc_type, exc, tb)

    # フォルダパスはテキストボックス（パスの貼り付け可）と連動させるため、常に
    # StringVarから読み取る（ボタンでの選択と貼り付けの両方を単一の入力源にする）。
    @property
    def _input_dir(self) -> Path | None:
        # エクスプローラーの［パスのコピー］は、パスを「"」で囲むので、外す。
        text = self._input_dir_var.get().strip().strip('"').strip()
        return Path(text) if text else None

    def _fit_folder_box(self) -> None:
        """対象フォルダの欄の高さを、［ファイルを読み込み］ボタンの高さにそろえる。"""
        holder, button = self._folder_holder, self._scan_button
        height = button.winfo_height()
        if height <= 1:
            height = button.winfo_reqheight()
        height = max(height, 20)
        text = holder.winfo_children()[0]
        width = text.winfo_reqwidth()
        if int(holder.cget("height")) != height or int(holder.cget("width")) != width:
            holder.configure(width=width, height=height)
            holder.pack_propagate(False)

    def _build_folder_box(self, parent: tk.Misc) -> tk.Text:
        """対象フォルダの欄（2行分の高さ。2026-11-17 班長指示）。

        ttk.Entry は1行しか持てないので、classic の tk.Text を使い、_input_dir_var と
        つなぐ（CLAUDE.md のUI方針：ttk に対応品がないものは classic のままでよい）。
        改行は入れない（Enter は何もしない。貼り付けた改行は外す）。長いパスは、行の端で
        折り返して2行目に続ける。
        """
        text = tk.Text(
            parent, height=2, width=42, wrap="char", undo=False,
            font="TkDefaultFont", relief="flat", borderwidth=0,
            background=theme.FIELD_BG, foreground=theme.TEXT, insertbackground=theme.TEXT,
            highlightthickness=1, highlightbackground=theme.BORDER, highlightcolor=theme.ACCENT,
            padx=3, pady=2,
        )
        syncing = False

        def _from_box(_event=None) -> None:
            nonlocal syncing
            if syncing:
                return
            value = text.get("1.0", "end-1c")
            clean = value.replace("\r", "").replace("\n", "")
            syncing = True
            try:
                if clean != value:
                    insert = text.index("insert")
                    text.delete("1.0", "end")
                    text.insert("1.0", clean)
                    text.mark_set("insert", insert)
                if self._input_dir_var.get() != clean:
                    self._input_dir_var.set(clean)
            finally:
                syncing = False
            text.edit_modified(False)

        def _from_var(*_args) -> None:
            nonlocal syncing
            if syncing:
                return
            value = self._input_dir_var.get()
            if text.get("1.0", "end-1c") != value:
                syncing = True
                try:
                    text.delete("1.0", "end")
                    text.insert("1.0", value)
                finally:
                    syncing = False
                text.edit_modified(False)

        text.bind("<<Modified>>", lambda e: _from_box() if text.edit_modified() else None)
        text.bind("<Return>", lambda e: "break")
        text.bind("<KP_Enter>", lambda e: "break")
        # Tab で次の部品へ（Text の既定では、タブ文字が入る）。
        text.bind("<Tab>", lambda e: (e.widget.tk_focusNext().focus_set(), "break")[1])
        self._input_dir_var.trace_add("write", _from_var)
        return text

    # ------------------------------------------------------------------ メニュー
    def _build_menu(self) -> None:
        """メニューバー（［ヘルプ］→［診断情報をコピー］。2026-11-10 班長決定：Python のツールに、
        次の版上げのときに入れる）。"""
        bar = tk.Menu(self)
        # ［ファイル］：リボンの3つのボタンと同じ操作（2026-11-16 班長指示）。押せる・
        # 押せないは、メニューを開くたびに、リボンのボタンの状態に合わせる（読み込む
        # までは更新できない、更新の記録がなければ戻せない、を二重に持たない）。
        f = tk.Menu(bar, tearoff=False)
        entries = (
            ("ファイルを読み込み", self._start_scan, lambda: self._scan_button),
            ("ファイルを更新", self._start_update, lambda: self._update_button),
            ("元のファイルに戻す", self._start_restore, lambda: self._restore_button),
        )
        for label, command, _button in entries:
            f.add_command(label=label, command=command)
        # ［オプション］（2026-11-16 班長指示）：証拠番号スタンプの文字の大きさを変える。
        # 3つの操作の後ろに区切り線を挟んで置く（_sync_file_menu の番号 0〜2 は変わらない）。
        f.add_separator()
        f.add_command(label="オプション...", command=self._open_options)

        def _sync_file_menu() -> None:
            for index, (_label, _command, button) in enumerate(entries):
                disabled = "disabled" in button().state()
                f.entryconfigure(index, state=tk.DISABLED if disabled else tk.NORMAL)

        f.configure(postcommand=_sync_file_menu)
        bar.add_cascade(label="ファイル", menu=f)
        self._file_menu, self._sync_file_menu = f, _sync_file_menu
        # ［編集］（Ver300）：ページの編集と、元に戻す・やり直す。リボンの「ページ」の組・
        # 縮小画像の右クリックのメニューと同じ操作。
        e = tk.Menu(bar, tearoff=False)
        self._add_page_commands(e)
        e.configure(postcommand=lambda: self._sync_page_menu(e))
        bar.add_cascade(label="編集", menu=e)
        h = tk.Menu(bar, tearoff=False)
        h.add_command(label="診断情報をコピー", command=self._copy_diagnostics)
        h.add_separator()
        h.add_command(
            label="VA mints について",
            command=lambda: messagebox.showinfo(APP_TITLE,
                f"VA mints  {__version__}\n"
                "mints（民事裁判書類電子提出システム）「記録」提出前チェック・準備ツール\n\n"
                "VBAアセット（vba-assets.net）\nMIT License",
                parent=self,
            ),
        )
        bar.add_cascade(label="ヘルプ", menu=h)
        self.config(menu=bar)

    # ------------------------------------------------------------------ ページの編集（Ver300）
    def _page_commands(self):
        """ページの編集の操作：（名前、メニューの字、実行、ショートカットの表示、要るもの）。
        要るもの："page"＝ページを編集できるファイルを選んでいる、"undo"・"redo"。"""
        return (
            ("undo", "元に戻す", self._undo_edit, "Ctrl+Z", "undo"),
            ("redo", "やり直す", self._redo_edit, "Ctrl+Y", "redo"),
            None,
            ("delete", "ページを削除", self._delete_pages, "Delete", "page"),
            ("left", "左に90°回転", lambda: self._rotate_pages(270), "", "page"),
            ("right", "右に90°回転", lambda: self._rotate_pages(90), "", "page"),
            ("half", "180°回転", lambda: self._rotate_pages(180), "", "page"),
            ("insert", "ページを挿入...", self._insert_pages, "", "page"),
            None,
            ("division", "このページから次の枝番にする", self._toggle_division, "", "division"),
            ("split", "このページから別のファイルにする", self._split_file, "", "division"),
            None,
            ("rename", "名前を変更", self._begin_rename, "F2", "rename"),
            ("join", "ファイルを結合", self._join_files, "", "join"),
        )

    def _selected_records(self) -> list[FileRecord]:
        """左の一覧で選んでいるファイル（一覧の並び順）。"""
        return [self._row_records[i] for i in self._tree.selection() if i in self._row_records]

    def _insert_pages(self) -> None:
        """ほかのファイル（PDF・画像）の全ページを、選んでいるページの後ろに入れる（4.10・4.12）。"""
        if not self._page_edit_ready():
            return
        record, ws = self._current, self._workspace
        chosen = filedialog.askopenfilename(
            parent=self,
            title="挿入するファイル",
            initialdir=str(ws.root),
            filetypes=[("PDF・画像", "*.pdf *.jpg *.jpeg *.png"), ("PDF", "*.pdf"), ("画像", "*.jpg *.jpeg *.png")],
        )
        if not chosen:
            return
        at = max(self._strip.selected or [self._page_index]) + 1
        try:
            added = ws.insert_pages(record, at, Path(chosen))
        except ValueError as exc:
            messagebox.showinfo(APP_TITLE, str(exc), parent=self)
            return
        self._strip.forget_images()
        self._after_page_edit(added[0], keep=added)

    def _join_files(self) -> None:
        """左の一覧で選んだファイルを、一覧の並び順に1つのファイルにする（4.10）。"""
        ws = self._workspace
        if ws is None:
            return
        records = self._selected_records()
        error = ws.join_error(records)
        if error:
            messagebox.showinfo(APP_TITLE, error, parent=self)
            return
        target = ws.join(records)
        self._show_record(target.path)

    def _show_record(self, path: Path) -> None:
        """左の一覧でこのファイルを選び、画面を描き直す（結合・元に戻すの後）。"""
        self._refresh_tree_marks()
        for iid, record in self._row_records.items():
            if record.path == path:
                if self._tree.selection() != (iid,):
                    self._tree.selection_set(iid)
                self._tree.focus(iid)
                self._tree.see(iid)
                break
        self.update_idletasks()
        if self._current is not None and self._current.path == path:
            self._rebuild_options()
            self._after_page_edit(min(self._page_index, max(0, self._workspace.page_count(self._current) - 1)))
        self._sync_page_buttons()

    def _division_label(self) -> str:
        """枝番の区切りの操作の字：いまのページが区切りなら「外す」。"""
        ws, record = self._workspace, self._current
        if ws is not None and record is not None and ws.can_edit_pages(record):
            if self._page_index > 0 and self._page_index in ws.branch_starts(record):
                return "枝番の区切りを外す"
        return "このページから次の枝番にする"

    def _toggle_division(self) -> None:
        """いまのページから次の枝番にする（区切りなら外す。要件定義書 4.11）。"""
        ws, record = self._workspace, self._current
        if ws is None or record is None:
            return
        try:
            ws.toggle_division(record, self._page_index)
        except ValueError as exc:
            messagebox.showinfo(APP_TITLE, str(exc), parent=self)
            return
        self._after_rename()

    def _split_file(self) -> None:
        """いまのページから後ろを、新しいファイルに分ける（要件定義書 4.10）。"""
        ws, record = self._workspace, self._current
        if ws is None or record is None:
            return
        try:
            new = ws.split(record, self._page_index)
        except ValueError as exc:
            messagebox.showinfo(APP_TITLE, str(exc), parent=self)
            return
        self._strip.forget_images()
        self._show_record(new.path)

    def _add_page_commands(self, menu: tk.Menu) -> None:
        for entry in self._page_commands():
            if entry is None:
                menu.add_separator()
                continue
            _key, label, command, accel, _need = entry
            menu.add_command(label=label, command=command, accelerator=accel)

    def _page_command_enabled(self, need: str) -> bool:
        ws = self._workspace
        if ws is None:
            return False
        if need == "undo":
            return ws.can_undo
        if need == "redo":
            return ws.can_redo
        if need == "rename":
            return self._current is not None and ws.can_rename(self._current)
        if need == "join":
            return len(self._tree.selection()) >= 2
        if need == "division":
            # 証拠番号がないなどで付けられないときも押せるようにし、押したら理由を出す。
            return (
                self._current is not None and ws.can_edit_pages(self._current)
                and self._page_index > 0
            )
        return self._current is not None and ws.can_edit_pages(self._current)

    def _sync_page_menu(self, menu: tk.Menu) -> None:
        index = 0
        for entry in self._page_commands():
            if entry is not None:
                menu.entryconfigure(
                    index, state=tk.NORMAL if self._page_command_enabled(entry[4]) else tk.DISABLED
                )
                if entry[0] == "division":
                    menu.entryconfigure(index, label=self._division_label())
            index += 1

    def _sync_page_buttons(self) -> None:
        for entry in self._page_commands():
            if entry is None:
                continue
            key, _label, _command, _accel, need = entry
            button = self._page_buttons.get(key)
            if button is not None:
                button.state(["!disabled"] if self._page_command_enabled(need) else ["disabled"])

    def _typing(self) -> bool:
        """文字を入れる欄に入力先があるか（ショートカットを、その欄に任せる）。"""
        focus = self.focus_get()
        # Text：対象フォルダの欄（2行分の高さ。2026-11-17）。
        return focus is not None and focus.winfo_class() in ("TEntry", "Entry", "TSpinbox", "Spinbox", "Text")

    def _on_key_undo(self, _event=None):
        if self._typing():
            return None
        self._undo_edit()
        return "break"

    def _on_key_redo(self, _event=None):
        if self._typing():
            return None
        self._redo_edit()
        return "break"

    def _on_key_delete(self, _event=None):
        # 縮小画像の列に入力先があるときは、ページを削除する。左の一覧に入力先があるときは、
        # ファイルを一覧から削除する（下書き。更新すると「元のファイル」フォルダへ移す）。
        focus = self.focus_get()
        if focus is self._strip.canvas:
            self._delete_pages()
            return "break"
        if focus is self._tree:
            self._remove_files()
            return "break"
        return None

    # ------------------------------------------------------------------ ファイルの削除・追加（Ver300）
    def _remove_files(self) -> None:
        """左の一覧で選んだファイルを、一覧から削除する（下書き。元に戻すで戻せる）。"""
        ws = self._workspace
        records = self._selected_records()
        if ws is None or not records:
            return
        rows = list(self._tree.get_children())
        chosen = {i for i in self._tree.selection() if i in rows}
        last = max((rows.index(i) for i in chosen), default=-1)
        after = [i for i in rows[last + 1:] if i not in chosen]
        before = [i for i in rows[:last] if i not in chosen]
        try:
            ws.remove(records)
        except ValueError as exc:
            messagebox.showinfo(APP_TITLE, str(exc), parent=self)
            return
        self._refresh_tree_marks()
        # 削除したファイルの次（なければ前）のファイルを選ぶ。
        follow = (after or before[-1:] or [None])[0]
        if follow is not None and self._tree.exists(follow):
            self._tree.selection_set(follow)
            self._tree.focus(follow)
            self._tree.see(follow)
        else:
            self._current = None
            self._show_options_placeholder()
            self._show_preview_text("一覧にファイルがありません。")
        self._sync_page_buttons()

    def _add_paths(self, paths: list[Path]) -> None:
        """ドロップ・貼り付けされたファイル（フォルダは直下のファイル）を、一覧に追加する（下書き）。"""
        ws = self._workspace
        if ws is None:
            messagebox.showinfo(
                APP_TITLE, "先に［ファイルを読み込み］でフォルダを読み込んでから、追加してください。", parent=self
            )
            return
        files = ws.expand_dropped(paths)
        if not files:
            messagebox.showinfo(APP_TITLE, "追加できるファイルがありません。", parent=self)
            return
        added, errors = ws.add_files(files)
        self._refresh_tree_marks()
        if added:
            self._show_record(added[0].path)
        if errors:
            shown = "\n".join(f"・{e}" for e in errors[:10])
            if len(errors) > 10:
                shown += f"\n…ほか {len(errors) - 10} 件"
            messagebox.showinfo(APP_TITLE, f"追加できなかったファイルがあります。\n\n{shown}", parent=self)
        self._sync_page_buttons()

    def _paste_files(self, _event=None):
        if self._typing():
            return None
        files = platform_support.clipboard_files()
        if not files:
            messagebox.showinfo(
                APP_TITLE,
                "貼り付けるファイルがありません。エクスプローラーでファイルを選んでコピー（Ctrl+C）してから、"
                "貼り付けてください。",
                parent=self,
            )
            return "break"
        self._add_paths(files)
        return "break"

    def _page_edit_ready(self) -> bool:
        return self._page_command_enabled("page")

    def _delete_pages(self) -> None:
        if not self._page_edit_ready():
            return
        record, ws = self._current, self._workspace
        selected = self._strip.selected
        try:
            ws.delete_pages(record, selected)
        except ValueError as exc:
            messagebox.showinfo(APP_TITLE, str(exc), parent=self)
            return
        self._after_page_edit(min(selected[0], ws.page_count(record) - 1))

    def _rotate_pages(self, degrees: int) -> None:
        if not self._page_edit_ready():
            return
        selected = self._strip.selected
        self._workspace.rotate_pages(self._current, selected, degrees)
        self._rebuild_options()  # 向きの注意（回したページは外れる）を描き直す
        self._after_page_edit(self._page_index, keep=selected)

    def _select_pages(self, positions: list[int]) -> None:
        """縮小画像で、これらのページを選んだ状態にする（向きの注意の［そのページを選ぶ］）。"""
        if not positions or self._current is None or self._workspace is None:
            return
        self._page_index = positions[0]
        self._strip.select(positions, positions[0])
        self._update_page_nav()
        self._schedule_preview(0)
        self._sync_page_buttons()

    def _on_strip_move(self, indices: list[int], target: int) -> None:
        if not self._page_edit_ready():
            return
        moved = self._workspace.move_pages(self._current, indices, target)
        if moved:
            self._after_page_edit(moved[0], keep=moved)

    def _undo_edit(self) -> None:
        if self._workspace is not None:
            self._after_history(self._workspace.undo())

    def _redo_edit(self) -> None:
        if self._workspace is not None:
            self._after_history(self._workspace.redo())

    def _after_history(self, path: Path | None) -> None:
        if path is None:
            return
        # 分割・削除・追加を戻す・やり直すと、一覧の行が増減する
        self._refresh_tree_marks()
        visible = set(self._tree.get_children())
        if self._current is not None and self._current.path not in {r.path for r in self._workspace.records}:
            # いま表示しているファイルが、一覧からなくなった（削除をやり直した・追加を戻した）。
            self._current = None
            rows = list(self._tree.get_children())
            if rows:
                self._tree.selection_set(rows[0])
                self._tree.see(rows[0])
            else:
                self._show_options_placeholder()
                self._show_preview_text("一覧にファイルがありません。")
        if self._current is None or self._current.path != path:
            for iid, record in self._row_records.items():
                if record.path == path and iid in visible:
                    self._tree.selection_set(iid)
                    self._tree.see(iid)
                    break
            self._sync_page_buttons()
            return
        count = self._workspace.page_count(self._current)
        self._rebuild_options()
        self._after_page_edit(min(self._page_index, max(0, count - 1)))

    def _after_page_edit(self, current: int, keep: list[int] | None = None) -> None:
        record, ws = self._current, self._workspace
        count = ws.page_count(record) if ws.can_edit_pages(record) else get_page_count(record)
        self._page_count = max(1, count)
        self._page_index = max(0, min(current, self._page_count - 1))
        self._refresh_strip()
        if keep:
            self._strip.select(keep, self._page_index)
        self._update_page_nav()
        self._schedule_preview(0)
        self._refresh_tree_marks()
        self._sync_page_buttons()

    def _thumb_key(self, index: int):
        record, ws = self._current, self._workspace
        if ws is not None and ws.can_edit_pages(record):
            info = ws.preview_page(record, index)
            if info is not None:
                path, page, rotate, _label = info
                return (str(path), page, rotate)
        return (str(record.path), index, 0)

    def _render_thumb(self, key, width: int, height: int):
        path, page, rotate = key
        if self._current is None:
            return None
        shown = dataclasses.replace(self._current, path=Path(path))
        return render_preview(
            shown, page_index=page, max_width=width, max_height=height,
            rotate_degrees=rotate, resolution=40,
        )

    def _refresh_strip(self) -> None:
        record, ws = self._current, self._workspace
        if record is None or ws is None:
            self._strip.set_pages([], 0, False)
            return
        editable = ws.can_edit_pages(record)
        count = ws.page_count(record) if editable else get_page_count(record)
        labels = ws.branch_labels(record) if editable else {}
        self._strip.set_pages(
            [self._thumb_key(i) for i in range(count)], self._page_index, editable, labels
        )

    def _on_strip_current(self, index: int) -> None:
        if index == self._page_index:
            return
        self._page_index = index
        self._update_page_nav()
        self._schedule_preview(0)
        self._sync_page_buttons()

    def _on_strip_context(self, event: tk.Event) -> None:
        self._sync_page_menu(self._page_menu)
        try:
            self._page_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._page_menu.grab_release()

    def _refresh_tree_marks(self) -> None:
        """まだ更新していない編集（ページ・名前）のあるファイルの名前に「＊」を付ける。
        名前を変えたファイルは、変えた後の名前を出す。"""
        ws = self._workspace
        if ws is None or self._scan_result is None:
            return
        # 分割で新しく作るファイル（Ver300）の行を足す。元に戻したものの行は隠す。
        row_of = {record.path: iid for iid, record in self._row_records.items()}
        for record in ws.records:
            iid = row_of.get(record.path)
            if iid is None:
                iid = self._tree.insert(
                    "", "end", text="", tags=(_CLASSIFICATION_TAGS[record.classification],)
                )
                row_of[record.path] = iid
            self._row_records[iid] = record  # 分け直したときは、新しい記録に替える
        current = {record.path for record in ws.records}
        for path, iid in row_of.items():
            if path not in current:
                self._tree.detach(iid)
        if self._current is not None and self._current.path in row_of:
            self._current = self._row_records[row_of[self._current.path]]

        visible: list[str] = []
        for record in ws.records:
            iid = row_of[record.path]
            # ほかのファイルに結合したファイルは、一覧から隠す（元に戻すと、元の位置に出る）。
            if ws.is_absorbed(record):
                self._tree.detach(iid)
                continue
            visible.append(iid)
            name = display_name(record, self._scan_result.root)
            if ws.is_renamed(record):
                name = str(Path(name).with_name(ws.name_of(record)))
            joined = ws.absorbed_into(record)
            if joined:
                name += f"（{len(joined)}件を結合）"
            if ws.is_created(record):
                name += "（分割で作るファイル）"
            if ws.is_added(record):
                name += "（追加するファイル）"
            mark = "＊" if ws.is_changed(record) else ""
            self._tree.item(iid, text=mark + name)
        for index, iid in enumerate(visible):
            self._tree.move(iid, "", index)
        self._refresh_sizes()

    def _refresh_sizes(self) -> None:
        """左の一覧の大きさの列と、一覧の下の合計を出し直す。

        各行は、いまのファイルの大きさ（結合するファイルは、結合するファイルとの合計。分割で
        作るファイルは、まだないので空）。合計は、読み込んだファイルの大きさの合計と、圧縮の
        見込みが出ているときは、圧縮の後の見込み（見込みの出たファイルだけを置き換えたもの）。
        """
        ws = self._workspace
        if ws is None:
            self._total_label.configure(text="")
            self._limit_label.configure(text="")
            return
        self._limit_label.configure(text=self._limit_label._text)
        for iid, record in self._row_records.items():
            if not self._tree.exists(iid):
                continue
            if ws.is_created(record):
                size = ""
            else:
                total = record.size_bytes + sum(r.size_bytes for r in ws.absorbed_into(record))
                size = _format_size(total)
            self._tree.set(iid, "size", size)
        real = [r for r in ws.records if not ws.is_created(r)]
        total = sum(r.size_bytes for r in real)
        after, compressing = total, False
        pending: list[tuple] = []
        for r in real:
            key = ws.estimate_key(r)
            if key is None:
                continue
            compressing = True
            size = ws.estimate(r)
            if size is None:
                pending.append(key)
            elif size >= 0:
                after += size - r.size_bytes
        count = sum(1 for r in ws.records if not ws.is_absorbed(r))
        text = f"合計 {_format_size(total)}（{count}件）"
        if pending:
            text += "\n圧縮の後：計算しています..."
            # 見込みは、1件ずつ裏で計算する（重い計算を同時にいくつも走らせない）。
            if not self._background.busy():
                self._start_estimate(ws, pending[0])
        elif compressing:
            text += f"\n圧縮の後：約{_format_size(after)}"
        self._total_label.configure(text=text)

    # ------------------------------------------------------------------ 名前の変更（Ver300）
    def _begin_rename(self, _event=None):
        """左の一覧で、選んでいるファイルの名前を変える欄を出す（ダブルクリック・F2）。"""
        ws, record = self._workspace, self._current
        if ws is None or record is None or self._typing():
            return None
        iid = next((i for i, r in self._row_records.items() if r is record), None)
        if iid is None:
            return None
        if len(self._tree.selection()) > 1:
            self._tree.selection_set(iid)  # 名前を変えるのは1つだけ
        if not ws.can_rename(record):
            messagebox.showinfo(APP_TITLE, ws.rename_error(record, record.path.name), parent=self)
            return "break"
        self._tree.see(iid)
        self.update_idletasks()
        bbox = self._tree.bbox(iid, "#0")
        if not bbox:
            return "break"
        x, y, width, height = bbox
        var = tk.StringVar(value=ws.name_of(record))
        entry = ttk.Entry(self._tree, textvariable=var)
        entry.place(x=x, y=y, width=max(width, theme.px(self, 160)), height=height)
        hint = tk.Label(
            self._tree, text="", fg=_UNFIXABLE_TEXT_COLOR, bg=theme.FIELD_BG, anchor="w",
            justify="left", bd=1, relief=tk.SOLID, highlightthickness=0,
            wraplength=max(width, theme.px(self, 160)),
        )
        stem_end = len(var.get()) - len(record.path.suffix)
        entry.focus_set()
        entry.selection_range(0, max(0, stem_end))
        entry.icursor(max(0, stem_end))
        state = {"done": False}

        def _show_hint(*_args) -> None:
            text = ws.rename_error(record, var.get()) or ws.rename_warning(var.get()) or ""
            if text:
                hint.configure(text=text)
                hint.update_idletasks()
                # 欄の下に収まらなければ（一覧の下の方の行）、欄の上に出す。
                below = y + height
                if below + hint.winfo_reqheight() > self._tree.winfo_height():
                    below = max(0, y - hint.winfo_reqheight())
                hint.place(x=x, y=below, width=max(width, theme.px(self, 160)))
                hint.lift()
            else:
                hint.place_forget()

        def _close() -> None:
            state["done"] = True
            entry.destroy()
            hint.destroy()
            self._tree.focus_set()

        def _commit(_event=None):
            if state["done"]:
                return "break"
            error = ws.rename_error(record, var.get())
            if error:
                _show_hint()
                return "break"  # 直せるように、欄を出したままにする
            ws.rename(record, var.get())
            _close()
            self._after_rename()
            return "break"

        def _cancel(_event=None):
            if not state["done"]:
                _close()
            return "break"

        def _focus_out(_event=None):
            # ほかの所をクリックした：変えられる名前なら決め、変えられなければやめる。
            if state["done"]:
                return
            if ws.rename_error(record, var.get()):
                _close()
            else:
                _commit()

        var.trace_add("write", _show_hint)
        entry.bind("<Return>", _commit)
        entry.bind("<KP_Enter>", _commit)
        entry.bind("<Escape>", _cancel)
        entry.bind("<FocusOut>", _focus_out)
        _show_hint()
        return "break"

    def _after_rename(self) -> None:
        self._refresh_tree_marks()
        self._rebuild_options()
        self._refresh_strip()
        self._schedule_preview(0)
        self._sync_page_buttons()

    def _on_tree_context(self, event: tk.Event) -> None:
        iid = self._tree.identify_row(event.y)
        menu = tk.Menu(self, tearoff=False)
        loaded = self._workspace is not None
        if iid:
            if iid not in self._tree.selection():
                self._tree.selection_set(iid)
            self._tree.focus(iid)
            self.update_idletasks()
            enabled = self._page_command_enabled("rename")
            menu.add_command(
                label="名前を変更", accelerator="F2", command=self._begin_rename,
                state=tk.NORMAL if enabled else tk.DISABLED,
            )
            menu.add_command(
                label="ファイルを結合", command=self._join_files,
                state=tk.NORMAL if self._page_command_enabled("join") else tk.DISABLED,
            )
            menu.add_command(
                label="一覧から削除", accelerator="Delete", command=self._remove_files,
                state=tk.NORMAL if loaded else tk.DISABLED,
            )
            menu.add_separator()
        # 空いた所でも、貼り付けはできる（Ver300）。
        menu.add_command(
            label="貼り付け（ファイルを追加）", accelerator="Ctrl+V", command=self._paste_files,
            state=tk.NORMAL if loaded else tk.DISABLED,
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _confirm_discard_edits(self, action: str) -> bool:
        """更新していないページの編集があれば、捨ててよいかを尋ねる。"""
        ws = self._workspace
        if ws is None or not ws.has_edits:
            return True
        return messagebox.askokcancel(
            APP_TITLE,
            "まだ更新していない編集（ページ・名前。一覧の名前に「＊」の付いたファイル）があります。\n"
            f"{action}と、その編集は失われます。続けますか？",
            icon=messagebox.WARNING, default=messagebox.CANCEL, parent=self,
        )

    def _on_close(self) -> None:
        if self._confirm_discard_edits("閉じる"):
            self._save_window()
            self.destroy()

    # ------------------------------------------------------------------ ウィンドウの大きさ
    def _restore_window(self) -> None:
        """前回閉じたときの大きさ・位置と、最大化していたかを戻す（user_settings.py。
        2026-11-17 班長指摘「ウインドウを最大化して終了したことが、保存されない」）。

        覚えた位置が、いまの画面の外になる（モニターを外した等）ときは、位置を使わず
        大きさだけを戻す。最大化を解いたときに戻る大きさは、最大化していないときの大きさ。
        """
        # メニューの高さが決まってから大きさを決める（先に決めると、Tk がメニューの高さの分
        # だけ縮め、閉じて開くたびに小さくなる。2026-11-17 に確かめた）。
        self.update_idletasks()
        geometry = "1300x760"
        saved = parse_geometry(self._user_settings.window_geometry)
        if saved is not None:
            w, h, x, y = saved
            w, h = max(w, 960), max(h, 560)
            geometry = f"{w}x{h}"
            left, top, width, height = _virtual_screen(self)
            # タイトルバーの左の辺りが画面の中にあれば、その位置に出す（つかんで動かせるように）。
            if left <= x + 100 < left + width and top <= y + 10 < top + height:
                geometry += f"+{x}+{y}"
        self.geometry(geometry)
        self._normal_geometry = geometry if "+" in geometry else None
        if self._user_settings.window_maximized:
            try:
                self.state("zoomed")
            except tk.TclError:
                pass  # 最大化のない環境（一部の Linux）では、大きさだけを戻す
        self.bind("<Configure>", self._on_window_configure, add="+")

    def _on_window_configure(self, event: tk.Event) -> None:
        """最大化していないときの大きさ・位置を覚えておく（最大化したまま閉じても、
        最大化を解いたときの大きさを保存できるように）。"""
        if event.widget is not self:
            return
        try:
            if self.state() == "normal":
                self._normal_geometry = self.geometry()
        except tk.TclError:
            pass

    def _save_window(self) -> None:
        """いまの大きさ・位置と、最大化しているかを保存する（保存できなくても閉じる）。"""
        try:
            maximized = self.state() == "zoomed"
            geometry = self._normal_geometry if maximized else self.geometry()
        except tk.TclError:
            return
        settings = dataclasses.replace(
            self._user_settings,
            window_geometry=geometry if parse_geometry(geometry) is not None else None,
            window_maximized=True if maximized else None,
        )
        try:
            save_user_settings(settings)
            self._user_settings = settings
        except OSError as exc:
            self._errors.record(exc, "ウィンドウの大きさの保存")

    # ------------------------------------------------------------------ オプション
    def _open_options(self) -> None:
        """［ファイル］→［オプション］：証拠番号スタンプの文字の大きさと見た目（黒字・赤字・
        赤字に赤枠。Ver300。2026-11-17 班長指示）を変える。

        既定は config.yaml の値（16ポイント・赤字）。変えた値は利用者ごとに保存し、次に
        起動したときも使う。すでにスタンプを付けたファイルには影響しない（付け直すには、
        スタンプのチェックを外して更新し、入れ直してもう一度更新する）。
        """
        default_size = self._base_config.evidence_stamp.font_size_pt
        current = self.config_obj.evidence_stamp.font_size_pt
        default_style = self._base_config.evidence_stamp.style
        default_style_text = next(
            (t for t, v in STAMP_STYLE_CHOICES if v == default_style), default_style
        )

        dialog = tk.Toplevel(self)
        dialog.title("オプション")  # 子の画面は、その画面でする事の名前（題名の決まり）
        dialog.transient(self)
        dialog.resizable(False, False)
        body = ttk.Frame(dialog, padding=(16, 14, 16, 8))
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        ttk.Label(body, text="証拠番号スタンプの文字の大きさ").grid(row=0, column=0, sticky="w")
        size_var = tk.StringVar(value=f"{current:g}")
        spin = ttk.Spinbox(
            body, from_=STAMP_FONT_SIZE_MIN, to=STAMP_FONT_SIZE_MAX, increment=1,
            textvariable=size_var, width=6, justify="right",
        )
        spin.grid(row=0, column=1, sticky="w", padx=(12, 4))
        ttk.Label(body, text="ポイント").grid(row=0, column=2, sticky="w")
        ttk.Label(
            body,
            text=f"既定：{default_size:g} ポイント（{STAMP_FONT_SIZE_MIN}〜{STAMP_FONT_SIZE_MAX}）",
            foreground=theme.NOTE, justify="left",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))

        ttk.Label(body, text="証拠番号スタンプの見た目").grid(row=2, column=0, sticky="w", pady=(14, 0))
        style_var = tk.StringVar(value=self.config_obj.evidence_stamp.style)
        styles = ttk.Frame(body)
        styles.grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))
        for text, value in STAMP_STYLE_CHOICES:
            ttk.Radiobutton(styles, text=text, variable=style_var, value=value).pack(
                side=tk.LEFT, padx=(0, 14)
            )
        ttk.Label(
            body,
            text=(
                f"既定：{default_style_text}\n"
                "すでにスタンプを付けたファイルの大きさ・見た目は変わりません。"
            ),
            foreground=theme.NOTE, justify="left",
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))

        buttons = ttk.Frame(dialog, padding=(16, 4, 16, 14))
        buttons.pack(side=tk.BOTTOM, fill=tk.X)

        def _reset() -> None:
            size_var.set(f"{default_size:g}")
            style_var.set(default_style)

        def _ok() -> None:
            text = size_var.get().strip()
            try:
                size = float(text)
            except ValueError:
                size = None
            if size is None or not (STAMP_FONT_SIZE_MIN <= size <= STAMP_FONT_SIZE_MAX):
                messagebox.showerror(
                    APP_TITLE,
                    f"文字の大きさは、{STAMP_FONT_SIZE_MIN}〜{STAMP_FONT_SIZE_MAX} の数で入れてください。",
                    parent=dialog,
                )
                return
            style = style_var.get()
            # ウィンドウの大きさなど、この画面で扱わない設定は、そのまま残す。
            settings = dataclasses.replace(
                self._user_settings,
                stamp_font_size_pt=None if size == default_size else size,
                stamp_style=None if style == default_style else style,
            )
            try:
                save_user_settings(settings)
            except OSError as exc:
                self._errors.record(exc, "オプションの保存")
                messagebox.showerror(
                    APP_TITLE,
                    "設定を保存できませんでした。この画面を閉じるまでの間だけ、変えた設定を使います。",
                    parent=dialog,
                )
            self._user_settings = settings
            self.config_obj = apply_user_settings(self._base_config, settings)
            if self._workspace is not None:
                self._workspace.config = self.config_obj
            dialog.destroy()
            if self._current is not None:
                self._schedule_preview(0)

        b = ttk.Button(buttons, text="キャンセル", command=dialog.destroy)
        b.pack(side=tk.RIGHT, padx=(6, 0))
        b = ttk.Button(buttons, text="OK", command=_ok, style="Accent.TButton")
        b.pack(side=tk.RIGHT)
        b = ttk.Button(buttons, text="既定に戻す", command=_reset)
        b.pack(side=tk.LEFT)
        b.tooltip = Tooltip(
            b, f"文字の大きさと見た目を、既定（{default_size:g} ポイント・{default_style_text}）に戻します。",
            above=True,
        )

        dialog.bind("<Return>", lambda _e: _ok())
        dialog.bind("<Escape>", lambda _e: dialog.destroy())
        # 小さな画面を、メインウィンドウの中央に出し、アイコンもそろえる（Tk の既定の
        # 羽根のアイコン・画面左上ではなく）。
        icon_path = _window_icon_path()
        if icon_path.exists():
            try:
                dialog.iconbitmap(str(icon_path))
            except tk.TclError:
                pass
        dialog.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - dialog.winfo_reqwidth()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - dialog.winfo_reqheight()) // 3
        dialog.geometry(f"+{max(0, x)}+{max(0, y)}")
        spin.focus_set()
        spin.selection_range(0, tk.END)
        _grab_when_viewable(dialog)
        self.wait_window(dialog)

    def _copy_diagnostics(self) -> None:
        text = self._diagnostic_text()
        self.clipboard_clear()
        self.clipboard_append(text)
        messagebox.showinfo(APP_TITLE,
            "診断情報をコピーしました。\n\n"
            "不具合を相談するときは、これを貼り付けて渡してください。版・環境・設定の数値・件数・"
            "誤りの起きた場所だけが入っていて、証拠・書面のファイル名や、ファイル・フォルダの場所は"
            "入っていません。\n"
            "渡す前に、メモ帳などに貼り付けて、中身を確かめることができます。",
            parent=self,
        )

    def _diagnostic_text(self) -> str:
        """診断情報の文。中身（ファイル名・場所・例外のメッセージの本文）を一切入れない（diagnostics.py の冒頭）。"""
        d = diagnostics
        c = self.config_obj
        lines = [f"VA mints 診断情報（{datetime.now():%Y-%m-%d %H:%M}）",
                 "※ 証拠・書面のファイル名、ファイルやフォルダの場所、利用者と PC の名前は入っていません。", ""]
        lines += ["■ 環境"] + d.environment_lines(self, "VA mints", __version__) + [""]
        lines += ["■ 設定（config.yaml の数値。場所・正規表現の中身は入れない）",
                  f"アップロード上限：{c.upload.max_total_mb:g}MB・安全マージン {c.upload.safety_margin_ratio:g}",
                  f"ファイル名：上限 {c.filename.max_length} 字・警告 {c.filename.warning_length} 字・"
                  f"禁止文字 {len(c.filename.forbidden_chars)} 種",
                  f"証拠番号：符号 {len(c.evidence_number.symbols)} 種・数字 {c.evidence_number.digits} 桁",
                  f"スタンプ：{c.evidence_stamp.font_size_pt:g}pt"
                  f"（{'オプションで変更' if self._user_settings.stamp_font_size_pt is not None else '既定'}）・"
                  f"上 {c.evidence_stamp.margin_top_mm:g}mm・"
                  f"右 {c.evidence_stamp.margin_right_mm:g}mm・見た目 {c.evidence_stamp.style}"
                  f"（{'オプションで変更' if self._user_settings.stamp_style is not None else '既定'}）",
                  f"個人情報パターン：{len(c.pii_detection.patterns)} 件", ""]
        lines.append("■ 状態")
        path = self._input_dir
        if path is None:
            lines.append("対象フォルダの欄：設定なし")
        else:
            try:
                exists = "フォルダがある" if path.is_dir() else "見つからない"
            except OSError:
                exists = "確かめられない"
            lines.append(f"対象フォルダの欄：設定あり（{exists}）")
        ws = self._workspace
        if self._scan_result is None or ws is None:
            lines.append("読み込み：まだ")
        else:
            counts = {c2: 0 for c2 in Classification}
            for record in self._scan_result.records:
                counts[record.classification] += 1
            plan = ws.plan_update()
            lines.append(
                f"読み込み済み：全 {len(self._scan_result.records)} 件"
                f"（A {counts[Classification.A_COMPLIANT]}・B {counts[Classification.B_NEEDS_FIX]}・"
                f"C {counts[Classification.C_NEEDS_CONVERSION]}・D {counts[Classification.D_UNSUPPORTED]}）"
            )
            lines.append(
                f"チェック中の処理：{sum(plan.counts.values())} 件・結合の候補 {len(ws.groups)} 組・"
                f"提出回 {len(plan.batches)} 回・プレビューを表示したファイル {len(ws.viewed)} 件"
            )
            lines.append(f"更新の記録（「{BACKUP_DIRNAME}」フォルダ）：{len(ws.manifest.entries)} 件")
        records = self._errors.records
        lines += ["", f"■ 起きた誤り（新しいものから {min(len(records), d.SHOW_ERRORS)} 件。"
                      f"覚えているのは全 {len(records)} 件）"]
        if not records:
            lines.append("なし")
        for r in reversed(records[-d.SHOW_ERRORS:]):
            lines += d.format_record(r)
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------ UI構築
    def _build_widgets(self) -> None:
        # 上部の操作は、VA Case シリーズと同じリボン（ui/ribbon.py）に置く。
        # 組は「読み込み」「更新」の2つ。主要な操作（読み込み・更新）は大きいボタン
        # （いつも青い枠。RibbonAccent）、フォルダの欄などの部品は組の中の block に置く。
        self._icons = IconSet(self)
        rb = Ribbon(self, self._icons)
        rb.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(6, 2))

        # ---- 「読み込み」の組
        g = rb.group("読み込み")
        box = ttk.Frame(g.body, style="Ribbon.TFrame")
        ttk.Label(box, text="対象フォルダ", style="Ribbon.TLabel").grid(
            row=0, column=0, sticky="w", padx=(2, 6), pady=1
        )
        # フォルダパス欄は、画面全幅まで伸ばさず固定幅にとどめる（従来方針）。長いパスが
        # 見切れないよう複数行にし、高さは［ファイルを読み込み］ボタンにそろえる
        # （2026-11-17 班長指示。ボタンができた後に、その高さに合わせる：_fit_folder_box）。
        self._folder_holder = tk.Frame(box, background=theme.FIELD_BG)
        e = self._build_folder_box(self._folder_holder)
        e.pack(fill=tk.BOTH, expand=True)
        self._folder_holder.grid(row=0, column=1, sticky="w", pady=0)
        e.tooltip = Tooltip(e, "読み込むファイルの入ったフォルダです。パスを貼り付けるか、右の［...］で選びます。")
        # フォルダ選択ボタンは、Windows標準の「参照」ダイアログの慣行に倣い
        # 「...」の省略表記にとどめる。
        b = ttk.Button(box, text="...", width=3, command=self._choose_input_dir,
                       style="Ribbon.TButton", takefocus=False)
        b.grid(row=0, column=2, sticky="w", padx=(2, 0), pady=1)
        b.tooltip = Tooltip(b, "対象フォルダを選びます。")
        # 「サブフォルダ内のファイルも含める」は外した（Ver300。2026-11-17 班長指示。
        # 読み込むのは直下のファイルと「提出用_NN」の中だけ。scanner/walker.py の _iter_files）。
        g.block(box)
        box.grid_configure(pady=0)  # 欄の上下を［ファイルを読み込み］ボタンにそろえる
        # ボタン名は「スキャン」を避ける（実物のスキャナー〔ScanSnap等〕による
        # 読み取り操作と紛らわしいため）。
        self._scan_button = g.large(
            "ファイルを読み込み", self._start_scan, style="RibbonAccent.TButton",
            tip="対象フォルダの中のファイルを読み込み、mintsの制約（ファイル形式・サイズ・"
                "ファイル名等）に適合するかを検査して、一覧に表示します。ファイルは変更しません。",
        )
        self._scan_button.bind("<Configure>", lambda _e: self._fit_folder_box(), add="+")
        self.after_idle(self._fit_folder_box)

        # ---- 「更新」の組
        g = rb.group("更新")
        self._update_button = g.large(
            "ファイルを更新", self._start_update, style="RibbonAccent.TButton",
            tip=self._update_tip,
        )
        self._restore_button = g.large(
            "元のファイルに戻す", self._start_restore,
            tip=self._restore_tip,
        )
        # ---- 「ページ」の組（Ver300）：ページの編集と、元に戻す・やり直す。
        g = rb.group("ページ")
        self._page_buttons = {}
        tips = {
            "undo": "直前のページの編集を取り消します（Ctrl+Z）。更新する前なら、何度でも取り消せます。",
            "redo": "取り消したページの編集を、もう一度行います（Ctrl+Y）。",
            "delete": "選んだページ（左の縮小画像。Ctrl・Shift を押しながらクリックすると、複数選べます）を削除します（Delete）。",
            "left": "選んだページを、左（反時計回り）に90°回します。",
            "right": "選んだページを、右（時計回り）に90°回します。",
            "half": "選んだページを、180°回します（逆さまのページを直すとき）。",
            "insert": "ほかのファイル（PDF・JPEG・PNG）を選び、その全ページを、選んでいるページの後ろに"
                      "入れます。画像は、A4 の1ページにして入れます（画質は変わりません）。"
                      "入れた元のファイルは、そのまま残ります。",
            "division": "表示しているページから、次の枝番にします（もう一度押すと、区切りを外します）。"
                        "1つのファイルのまま、甲005-1、甲005-2…と枝番を付けるときに使います。"
                        "区切りごとの最初のページに、その枝番のスタンプを押します。",
            "split": "表示しているページから後ろを、新しいファイルに分けます。新しいファイルは、"
                     "左の一覧のすぐ下に出ます（名前は、ダブルクリック又は F2 キーで変えられます）。"
                     "更新すると、新しいファイルができます。",
        }
        # 並び：削除・回転（左90°・右90°・180°をメニューで選ぶ）・挿入 ｜ 枝番の区切り・分割 ｜
        # 元に戻す・やり直す（2026-11-17 班長指示）。どのボタンにもアイコンを付ける（icons.BUTTON_ICONS）。
        commands = {e[0]: e for e in self._page_commands() if e is not None}
        labels = {"insert": "ページを挿入", "split": "ファイルを分割", "division": "枝番の区切り"}
        for column in (("delete", "rotate", "insert"), ("division", "split"), ("undo", "redo")):
            g.new_column()
            for key in column:
                if key == "rotate":
                    items = [(commands[k][1], commands[k][2]) for k in ("left", "right", "half")]
                    button = g.dropdown(
                        "回転", items,
                        tip="選んだページを回します。押すと、回す向き（左に90°・右に90°・180°）を選べます。",
                    )
                    for k in ("left", "right", "half"):
                        self._page_buttons[k] = button  # 押せる・押せないは、3つとも同じ
                    continue
                _key, label, command, _accel, _need = commands[key]
                self._page_buttons[key] = g.small(labels.get(key, label), command, tip=tips[key])
        self._page_menu = tk.Menu(self, tearoff=False)
        self._add_page_commands(self._page_menu)

        # ファイルを読み込むまでは、更新は押せない（グレーアウト。2026-11-13 班長指示）。
        # ［元のファイルに戻す］は、対象フォルダに更新の記録があるときだけ押せる。
        self._update_button.state(["disabled"])
        self._restore_button.state(["disabled"])

        # ステータス行：画面の最下部に、見出しなしで配置する。フラットデザイン
        # 方針のため立体的な枠線は使わず、上側に細い区切り線を1本引くだけとする。
        # side=BOTTOMは先にpackした要素ほど画面の下端に近づく。
        self._status_label = ttk.Label(self, text="", anchor="w", padding=(8, 4))
        self._status_label.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Separator(self, orient="horizontal").pack(side=tk.BOTTOM, fill=tk.X)
        # 号証の連番・欠番チェック（4.7）は複数ファイルにまたがる検出結果で、右ペイン
        # （1ファイルずつ）には収まらないため、読み込んだ時点からここに出し続ける。
        # 疑いのないときは、行ごと出さない（_set_sequence_text）。
        self._sequence_label = ttk.Label(
            self, text="", padding=(8, 2, 8, 4), anchor="w", justify="left",
            foreground=_UNFIXABLE_TEXT_COLOR,
        )

        # 3つのペイン。ttk.Panedwindow は最小幅を指定できないため、classic の
        # tk.PanedWindow を使う（班長の UI 設計方針のとおり）。
        px = lambda n: theme.px(self, n)  # noqa: E731
        panes = tk.PanedWindow(
            self, orient=tk.HORIZONTAL, sashwidth=px(5), bd=0, bg=theme.WINDOW_BG, sashrelief=tk.FLAT
        )
        panes.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=(6, 4))
        self._panes = panes
        left = ttk.Frame(panes)
        center = ttk.Frame(panes)
        right = ttk.Frame(panes)
        panes.add(left, minsize=px(180), width=px(330), stretch="never")
        panes.add(center, minsize=px(260), stretch="always")
        panes.add(right, minsize=px(260), width=px(420), stretch="never")
        self._build_file_list(left)
        self._build_preview(center)
        self._build_options_pane(right)

    def _build_file_list(self, parent: ttk.Frame) -> None:
        """左のペイン：読み込んだファイルの名前。"""
        # 名前だけを並べる（見出し・列なし。2026-11-16 班長指示）。
        # 複数を選べるようにする（ファイルの結合。Ver300）。プレビューと右のペインには、
        # 最後にクリックしたファイル（focus）を出す。
        # ファイルの大きさを、名前の右に出す（見出しは出さない）。一覧の下に合計を出す
        # （2026-11-17 班長指示。「mints に1回で送れるのは合計200MBまで」と比べられるように）。
        tree = ttk.Treeview(
            parent, show="tree", columns=("size",), selectmode="extended", style="Files.Treeview"
        )
        tree.column("#0", width=theme.px(self, 240), minwidth=theme.px(self, 100), stretch=True)
        tree.column("size", width=theme.px(self, 64), minwidth=theme.px(self, 48), anchor="e", stretch=False)
        for tag, color in _TAG_COLORS.items():
            tree.tag_configure(tag, background=color)
        # 合計の右に、mints の上限（1回のアップロードの合計。制約調査報告書・Q&A 9頁）を出す
        # （2026-11-17 班長指示。合計と並べて、どこまで小さくすればよいかを分かるように）。
        # 一覧の幅は狭いので、上限の文は、決めた位置で2行に分ける（Tk に任せると、空白の
        # 位置で折り返したり、「。」だけが次の行に残ったりする）。
        totals = ttk.Frame(parent, padding=(4, 3, 4, 0))
        totals.pack(side=tk.BOTTOM, fill=tk.X)
        self._total_label = ttk.Label(totals, text="", anchor="nw", justify="left")
        self._total_label.grid(row=0, column=0, sticky="nw")
        self._limit_label = ttk.Label(totals, text="", anchor="nw", justify="left", foreground=theme.NOTE)
        self._limit_label._text = (
            f"mintsに1回で送れるのは\n合計{self.config_obj.upload.max_total_mb:g}MBまでです。"
        )
        self._limit_label.grid(row=0, column=1, sticky="nw", padx=(theme.px(self, 12), 0))
        scroll = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree.bind("<<TreeviewSelect>>", self._on_select_file)
        # 名前の変更（Ver300）：ダブルクリック・F2・右クリックのメニュー。
        tree.bind("<Double-1>", self._begin_rename)
        tree.bind("<F2>", self._begin_rename)
        tree.bind("<Button-3>", self._on_tree_context)
        # ファイルの追加（Ver300）：コピーしたファイルの貼り付けと、エクスプローラーからの
        # ドラッグアンドドロップ。どちらも下書きで、更新すると対象フォルダに写す。
        tree.bind("<Control-v>", self._paste_files)
        tree.bind("<Control-V>", self._paste_files)
        try:
            platform_support.enable_file_drop(tree, self._add_paths)
        except Exception as exc:  # noqa: BLE001 - ドロップできないだけで、ほかの操作はできる
            self._errors.record(exc, "ドラッグアンドドロップの準備")
        self._tree = tree

    def _build_preview(self, parent: ttk.Frame) -> None:
        """中央のペイン：選んだファイルのプレビュー（チェックした処理を行った状態）。"""
        # ページの縮小画像の列（Ver300）：クリックで表示、Ctrl・Shift で複数選択、ドラッグで
        # 並べ替え、右クリックでメニュー。
        self._strip = PageStrip(
            parent, on_current=self._on_strip_current, on_move=self._on_strip_move,
            on_context=self._on_strip_context, render=self._render_thumb,
        )
        self._strip.pack(side=tk.LEFT, fill=tk.Y, padx=(8, 0))
        # ページ送りは、右端のスクロールバーで行う（2026-11-16 班長指示。「前のページ」
        # 「次のページ」のボタンは外した）。1ページを枠に収めて表示し、つまみ・矢印・
        # ホイールで前後のページへ移る。
        self._page_scroll = ttk.Scrollbar(parent, orient="vertical", command=self._on_page_scroll)
        self._page_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        # いま何ページ目か（検出結果は「3ページ目」のようにページで示すため、複数ページの
        # ときだけ小さく出す）。
        self._page_label = ttk.Label(parent, text="", anchor="e", foreground=theme.NOTE,
                                     padding=(8, 1, 8, 0))
        self._page_label.pack(side=tk.BOTTOM, fill=tk.X)

        # 画像を表示するLabelは、読み込み前の大きさを確保できるclassicのtk.Labelを使う。
        self._preview_label = tk.Label(
            parent, anchor="center", justify="center", bg=theme.WINDOW_BG, fg=theme.NOTE,
            bd=0, highlightthickness=1, highlightbackground=theme.BORDER,
            text="［ファイルを読み込み］で読み込んだファイルを、左の一覧で選ぶと、\nここにプレビューを表示します。",
        )
        self._preview_label.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=(8, 2))
        self._preview_label.bind("<Configure>", self._on_preview_resize)
        self._preview_label.bind("<MouseWheel>", self._on_page_wheel)
        self._update_page_nav()

    def _build_options_pane(self, parent: ttk.Frame) -> None:
        """右のペイン：処理の項目（縦に長くなり得るので、スクロールできる枠に入れる）。"""
        canvas = tk.Canvas(parent, bg=theme.WINDOW_BG, bd=0, highlightthickness=0)
        scroll = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        inner = ttk.Frame(canvas, padding=(8, 2, 8, 8))
        window = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))

        def _on_canvas_resize(event: tk.Event) -> None:
            canvas.itemconfigure(window, width=event.width)
            self._apply_wraplength()

        canvas.bind("<Configure>", _on_canvas_resize)
        self._options_canvas = canvas
        self._options_frame = inner
        self._show_options_placeholder()

    def _options_wraplength(self) -> int:
        return max(theme.px(self, 160), self._options_canvas.winfo_width() - theme.px(self, 48))

    def _apply_wraplength(self) -> None:
        wrap = self._options_wraplength()
        for w in self._wrap_widgets:
            if w.winfo_exists():
                w.configure(wraplength=wrap - getattr(w, "_indent", 0))

    def _bind_wheel(self, widget: tk.Misc) -> None:
        """右ペインの中のどこでホイールを回しても、右ペインがスクロールするようにする。"""
        def _wheel(event: tk.Event) -> str:
            first, last = self._options_canvas.yview()
            if first <= 0.0 and last >= 1.0:
                return "break"  # 全部見えているときは動かさない
            self._options_canvas.yview_scroll(
                platform_support.mousewheel_units(self, event.delta, -int(event.delta / 120) * 3), "units"
            )
            return "break"

        widget.bind("<MouseWheel>", _wheel)
        for child in widget.winfo_children():
            self._bind_wheel(child)

    # 説明書き（読み込み前・記録のないときは、押せない理由を先に出す）
    def _update_tip(self) -> str:
        base = (
            "すべてのファイルを、右の「処理の項目」でチェックの入っている内容のとおりに更新します。"
            "押すと、処理ごとの件数を示して確認します。"
            f"更新する前のファイルは、同じフォルダの「{BACKUP_DIRNAME}」フォルダに残ります。"
        )
        if self._workspace is None:
            return "先に［ファイルを読み込み］でファイルを読み込むと、押せるようになります。" + base
        return base

    def _restore_tip(self) -> str:
        base = (
            f"［ファイルを更新］で更新したファイルを、「{BACKUP_DIRNAME}」フォルダに残した"
            "最初に更新する前のファイルに戻します。更新の後に他のアプリで直した内容も、元に戻ります。"
        )
        if "disabled" in self._restore_button.state():
            return "対象フォルダに、このツールで更新したファイルがあるときに押せます。" + base
        return base

    # ------------------------------------------------------------------ フォルダ選択
    def _choose_input_dir(self) -> None:
        selected = filedialog.askdirectory(title="対象フォルダを選択", mustexist=True)
        if selected:
            self._input_dir_var.set(selected)

    # ------------------------------------------------------------------ 読み込み実行
    def _start_scan(self) -> None:
        input_dir = self._input_dir
        if input_dir is None:
            messagebox.showerror(APP_TITLE, "対象フォルダを選択してください。")
            return
        if not input_dir.is_dir():
            messagebox.showerror(APP_TITLE, f"対象フォルダが見つかりません：{input_dir}")
            return
        if input_dir.name == BACKUP_DIRNAME:
            messagebox.showerror(APP_TITLE,
                f"「{BACKUP_DIRNAME}」フォルダは、更新する前のファイルを残しておくフォルダのため、"
                "対象フォルダにできません。1つ上のフォルダを選んでください。",
            )
            return
        if not self._confirm_discard_edits("読み込み直す"):
            return
        self._scan(input_dir)

    def _scan(self, input_dir: Path) -> None:
        self._scan_button.config(state=tk.DISABLED)
        # 読み込み直しの間は、更新も復元も押せなくする（結果が入れ替わる途中のため）。
        self._update_button.state(["disabled"])
        self._restore_button.state(["disabled"])
        self._status_label.config(text="読み込み中...")
        self._clear_file_list()

        # input_dir はメインスレッドで確定させ、ワーカースレッドには値として渡す
        # （別スレッドから直接Tkの変数を読み取らない）。
        thread = threading.Thread(target=self._run_scan_worker, args=(input_dir,), daemon=True)
        thread.start()
        self.after(100, self._poll_scan_queue)

    def _run_scan_worker(self, input_dir: Path) -> None:
        try:
            result = scan_folder(input_dir, self.config_obj)
            self._queue.put(result)
        except Exception as exc:  # noqa: BLE001 - UIスレッドへエラーを引き渡す
            self._queue.put(exc)

    def _poll_scan_queue(self) -> None:
        try:
            item = self._queue.get_nowait()
        except queue.Empty:
            self.after(100, self._poll_scan_queue)
            return

        self._scan_button.config(state=tk.NORMAL)
        if isinstance(item, Exception):
            self._errors.record(item, "読み込み")
            # 前回の読み込み結果を破棄する（残したままだと、一覧は空なのに
            # ［ファイルを更新］が前回の結果に基づく紛らわしい動きをする）。
            self._scan_result = None
            self._workspace = None
            self._set_sequence_text("")
            self._status_label.config(text="読み込みに失敗しました。")
            messagebox.showerror(APP_TITLE, f"読み込み中にエラーが発生しました：\n{item}")
            return

        self._scan_result = item
        try:
            manifest = Manifest.load(item.root)
        except ValueError as exc:
            self._errors.record(exc, "更新記録の読み込み")
            manifest = Manifest()
            messagebox.showwarning(APP_TITLE,
                f"「{BACKUP_DIRNAME}」フォルダの更新の記録を読めませんでした。\n"
                "以前の更新で済ませた処理（証拠番号スタンプなど）にも、チェックが付きます。"
                "プレビューで確かめてから更新してください。［元のファイルに戻す］は使えません。",
            )
        self._workspace = Workspace(item, self.config_obj, manifest)
        self._strip.forget_images()  # 更新でファイルの中身が変わったので、縮小画像を描き直す
        self._populate_file_list(item)
        self._update_button.state(["!disabled"])
        self._restore_button.state(["!disabled"] if manifest else ["disabled"])

    # ------------------------------------------------------------------ 左ペイン
    def _clear_file_list(self) -> None:
        self._tree.delete(*self._tree.get_children())
        self._row_records = {}
        self._total_label.configure(text="")
        self._limit_label.configure(text="")
        self._current = None
        self._show_options_placeholder()
        self._show_preview_text(
            "［ファイルを読み込み］で読み込んだファイルを、左の一覧で選ぶと、\nここにプレビューを表示します。"
        )
        self._page_index, self._page_count = 0, 1
        self._update_page_nav()
        self._strip.forget_images()
        self._strip.set_pages([], 0, False)
        if hasattr(self, "_page_buttons"):
            self._sync_page_buttons()

    def _set_sequence_text(self, text: str) -> None:
        """号証の連番・欠番の疑いの行。疑いのないときは、行ごと隠す。"""
        self._sequence_label.config(text=text)
        if text:
            self._sequence_label.pack(side=tk.BOTTOM, fill=tk.X, before=self._panes)
        else:
            self._sequence_label.pack_forget()

    def _populate_file_list(self, result: ScanResult) -> None:
        select_iid = None
        for record in result.records:
            name = display_name(record, result.root)
            iid = self._tree.insert(
                "", "end", text=name, tags=(_CLASSIFICATION_TAGS[record.classification],)
            )
            self._row_records[iid] = record
            if name == self._reselect_name:
                select_iid = iid
        self._reselect_name = None
        self._refresh_sizes()

        # 要件定義書4.7「号証の連番・欠番チェック」：読み込んだ時点のファイル名で判定する。
        sequence_lines = format_evidence_sequence_warnings(
            (r.path.name for r in self._workspace.eligible), self.config_obj.evidence_number
        )
        if sequence_lines:
            shown = "　".join(line.removeprefix("・") for line in sequence_lines[:6])
            if len(sequence_lines) > 6:
                shown += f"　...ほか{len(sequence_lines) - 6}件"
            self._set_sequence_text(f"号証の連番・欠番の疑い：{shown}")
        else:
            self._set_sequence_text("")
        self._status_label.config(text="読み込み完了")

        rows = self._tree.get_children()
        if rows:
            target = select_iid or rows[0]
            self._tree.selection_set(target)
            self._tree.focus(target)
            self._tree.see(target)

    def _on_select_file(self, _event: tk.Event | None = None) -> None:
        selection = self._tree.selection()
        if not selection or self._workspace is None:
            self._sync_page_buttons()
            return
        focus = self._tree.focus()
        record = self._row_records.get(focus if focus in selection else selection[-1])
        if record is None or record is self._current:
            self._sync_page_buttons()
            return
        self._current = record
        self._workspace.mark_viewed(record)
        self._page_index = 0
        ws = self._workspace
        count = ws.page_count(record) if ws.can_edit_pages(record) else get_page_count(record)
        self._page_count = max(1, count)
        self._update_page_nav()
        self._refresh_strip()
        self._rebuild_options()
        self._schedule_preview(0)
        self._sync_page_buttons()

    # ------------------------------------------------------------------ 右ペイン
    def _reset_options_frame(self) -> None:
        for child in self._options_frame.winfo_children():
            child.destroy()
        self._wrap_widgets = []
        self._position_label = None
        self._options_canvas.yview_moveto(0)

    def _wrap_label(self, text: str, indent: int = 0, **kw) -> ttk.Label:
        """右ペインの、折り返す文。indent：左の字下げ（画素）。その分、折り返し幅を狭める。"""
        label = ttk.Label(
            self._options_frame, text=text, wraplength=self._options_wraplength() - indent,
            justify="left", **kw
        )
        label._indent = indent
        self._wrap_widgets.append(label)
        return label

    def _show_options_placeholder(self) -> None:
        """ファイルを選ぶ前：すべての項目を、操作不可で並べておく。"""
        self._reset_options_frame()
        self._build_process_items(None)
        self._bind_wheel(self._options_frame)

    def _rebuild_options(self) -> None:
        record, ws = self._current, self._workspace
        self._reset_options_frame()
        if record is None or ws is None:
            self._build_process_items(None)
            self._bind_wheel(self._options_frame)
            return
        frame = self._options_frame

        self._wrap_label(record.path.name, font=("", 10, "bold")).pack(
            side=tk.TOP, anchor="w", pady=(0, 6)
        )

        view = ws.pane_view(record)
        # 本ツールでは対処できない（検出のみの）事項を先に、赤字で出す（要件定義書 第4章。
        # 対処が必要な検出結果が、必ず先に目に入るように）。
        # 赤字の注意と備考は、同じ内容が二度出ないように組み立てる（build_pane_warnings）。
        if view.selection is None:
            # C（要変換）・D（対象外）：何をすればよいかの案内。処理の項目はすべて操作不可。
            labels = [describe_expected_processing(record), "このファイルは更新の対象になりません。"]
            intro = None
        else:
            labels = [o.label for o in view.selection.options if not o.enabled]
            intro = (
                "以下は本ツールでは対処できません（検出のみ）。他のアプリで"
                "対処のうえ、本ツールに再度読み込んで再検証してください。"
            )
        warnings, notes = build_pane_warnings(
            record, labels, is_group=view.is_group, is_target=view.selection is not None
        )
        if warnings and intro:
            self._wrap_label(intro, foreground=_UNFIXABLE_TEXT_COLOR).pack(
                side=tk.TOP, anchor="w", pady=1
            )
        for warning in warnings:
            bullet = "・" if intro else ""
            self._wrap_label(bullet + warning.text, foreground=_UNFIXABLE_TEXT_COLOR).pack(
                side=tk.TOP, anchor="w", pady=1
            )
            # 細目は、字下げを余白で付ける（空白で付けると、Tk がその空白で折り返し、
            # 「－」だけが1行に残る）。
            indent = theme.px(self, 14)
            for detail in warning.details:
                self._wrap_label(
                    f"－{detail}", indent=indent, foreground=_UNFIXABLE_TEXT_COLOR
                ).pack(side=tk.TOP, anchor="w", padx=(indent, 0))
        if record.exceeds_single_file_limit and view.selection is not None:
            # 単体の上限超過は、このツールの中で対処できる（Ver300。要件定義書 4.10・4.13）。
            self._wrap_label(
                "→ 単体で上限を超えるファイルは、下の「画像を軽くする」にチェックを入れて、"
                "大きさの見込みとプレビューを確かめるか、ページを選んで［ファイルを分割］で"
                "分けてください。",
                foreground=_UNFIXABLE_TEXT_COLOR,
            ).pack(side=tk.TOP, anchor="w", pady=1)
        if warnings:
            ttk.Separator(frame, orient="horizontal").pack(side=tk.TOP, fill=tk.X, pady=(6, 6))

        # 向きの注意（4.6）：回すかどうかは利用者が決め、ページの回転で回す（2026-11-17 班長決定。
        # 右のペインの「向き補正」は廃止した）。［そのページを選ぶ］で、縮小画像で選んだ状態にする。
        positions = ws.orientation_pages(record) if view.selection is not None else []
        notice = orientation_notice(record, positions)
        if notice:
            self._wrap_label(notice).pack(side=tk.TOP, anchor="w", pady=1)
            b = ttk.Button(
                frame, text="そのページを選ぶ", command=lambda p=list(positions): self._select_pages(p)
            )
            b.pack(side=tk.TOP, anchor="w", pady=(4, 2))
            b.tooltip = Tooltip(
                b, "向きの注意のページを、プレビューの左の縮小画像で選んだ状態にします。"
                "続けて、リボンの［回転］から［左に90°回転］か［右に90°回転］を選んでください。",
            )
            ttk.Separator(frame, orient="horizontal").pack(side=tk.TOP, fill=tk.X, pady=(6, 6))

        # ファイルの結合（Ver300）：結合するファイルと、別々の証拠を1つにする注意。
        absorbed = ws.absorbed_into(record)
        if absorbed:
            names = "、".join(r.path.name for r in absorbed)
            self._wrap_label(
                f"ファイルの結合：{names} を、このファイルの後ろにつなげます（左の一覧の並び順）。"
                f"更新すると、結合したファイルは「{BACKUP_DIRNAME}」フォルダへ移ります。"
            ).pack(side=tk.TOP, anchor="w", pady=1)
            caution = ws.join_warning(record)
            if caution:
                self._wrap_label(caution, foreground=_UNFIXABLE_TEXT_COLOR).pack(
                    side=tk.TOP, anchor="w", pady=1
                )
            ttk.Separator(frame, orient="horizontal").pack(side=tk.TOP, fill=tk.X, pady=(6, 6))

        # ファイルの分割（Ver300）：分けて作るファイルと、分ける前のファイル。
        source = ws.split_source(record) if ws.is_created(record) else None
        children = ws.split_children(record)
        if source is not None or children:
            if source is not None:
                text = (
                    f"ファイルの分割：「{ws.name_of(source)}」から分けて、新しく作るファイルです。"
                    "更新すると、このファイルができます。名前は、左の一覧でダブルクリック"
                    "（又はF2キー）して変えられます。分けるのをやめるときは、元に戻す（Ctrl+Z）を使います。"
                )
            else:
                names = "、".join(ws.name_of(r) for r in children)
                text = f"ファイルの分割：このファイルの後ろのページを、{names} に分けます。"
            self._wrap_label(text).pack(side=tk.TOP, anchor="w", pady=1)
            ttk.Separator(frame, orient="horizontal").pack(side=tk.TOP, fill=tk.X, pady=(6, 6))

        # ファイルの追加（Ver300）：元の場所と、更新するとどうなるか。
        if ws.is_added(record):
            self._wrap_label(
                f"追加するファイル：{record.path}\n更新すると、対象フォルダに写します"
                "（元の場所のファイルは、そのままです）。追加をやめるときは、元に戻す（Ctrl+Z）か、"
                "一覧から削除（Deleteキー）を使います。"
            ).pack(side=tk.TOP, anchor="w", pady=1)
            ttk.Separator(frame, orient="horizontal").pack(side=tk.TOP, fill=tk.X, pady=(6, 6))

        self._build_process_items(view)

        if notes:
            ttk.Separator(frame, orient="horizontal").pack(side=tk.TOP, fill=tk.X, pady=(10, 6))
            self._wrap_label("備考", foreground=theme.NOTE).pack(side=tk.TOP, anchor="w")
            for note in notes:
                self._wrap_label(f"・{note}", foreground=theme.NOTE).pack(side=tk.TOP, anchor="w")

        self._bind_wheel(frame)

    def _checkbutton(
        self, text: str, var: tk.BooleanVar, enabled: bool = True,
        parent: tk.Misc | None = None, indent: int = 0,
    ) -> tk.Checkbutton:
        # ttk.Checkbuttonはwraplengthオプションを持たないため、長い説明文を
        # 折り返し表示できる標準tk.Checkbuttonを使う。
        # parent・indent：枠（圧縮の枠）の中に置くときの親と、枠の分だけ狭める幅。
        cb = tk.Checkbutton(
            parent if parent is not None else self._options_frame,
            variable=var,
            text=text,
            wraplength=self._options_wraplength() - indent,
            justify="left",
            anchor="w",
            state=tk.NORMAL if enabled else tk.DISABLED,
        )
        if not enabled:
            cb.configure(disabledforeground=theme.DISABLED_TEXT)
        cb._indent = indent
        self._wrap_widgets.append(cb)
        return cb

    def _build_process_items(self, view: PaneView | None) -> None:
        """処理の項目を、いつも同じ並び（PROCESS_ITEMS）ですべて表示する。

        そのファイルに当てはまる項目は、内容を書いた操作できるチェックボックス
        （適用するものにチェック）。当てはまらない項目・ファイルを選ぶ前・更新の
        対象でないファイルは、名前だけの、チェックなし・操作不可のチェックボックス。
        """
        sel = view.selection if view is not None else None
        available = {o.key: o for o in sel.options if o.enabled} if sel is not None else {}
        # 圧縮の2つの項目と大きさの見込みは、1つの枠（ファイルを小さくする）にまとめ、
        # 見込みが枠の中の選択で決まることを、形で分かるようにする（2026-11-17 班長指示）。
        group: ttk.LabelFrame | None = None
        group_indent = theme.px(self, 24)
        for key, name in PROCESS_ITEMS:
            parent, indent = None, 0
            if key in COMPRESS_KEYS:
                if group is None:
                    group = ttk.LabelFrame(self._options_frame, text="ファイルを小さくする")
                    group.pack(side=tk.TOP, anchor="w", fill=tk.X, pady=(6, 3))
                parent, indent = group, group_indent
            if key == MERGE_KEY and view is not None and view.merge_option is not None:
                var = tk.BooleanVar(value=view.merge_checked)
                cb = self._checkbutton(view.merge_option.label, var)
                cb.configure(command=lambda v=var: self._on_toggle_merge(v.get()))
            elif key in available:
                var = tk.BooleanVar(value=sel.choices.get(key, False))
                cb = self._checkbutton(available[key].label, var, parent=parent, indent=indent)
                cb.configure(command=lambda k=key, v=var: self._on_toggle_option(sel, k, v.get()))
            else:
                cb = self._checkbutton(
                    name, tk.BooleanVar(value=False), enabled=False, parent=parent, indent=indent
                )
            if parent is not None:
                cb.pack(side=tk.TOP, anchor="w", pady=3, padx=(theme.px(self, 6), 0), fill=tk.X)
            else:
                cb.pack(side=tk.TOP, anchor="w", pady=3, fill=tk.X)
            # スタンプ済みのファイルは位置を変えられない（付け直すには、いったん外して
            # 更新してから、もう一度付ける）ので、位置調整は出さない。
            if key in available and key == "stamp" and not sel.stamped:
                self._build_position_controls(sel)
            if key in available and key == "reduce":
                self._build_reduce_controls(sel, group)
        self._estimate_label = None
        if group is not None and ("compress" in available or "reduce" in available):
            # 大きさの見込み（枠の中の圧縮にチェックがあるとき。計算は裏で行う）。区切り線と
            # 見込みの行は、見込みがあるときだけ出す（_refresh_estimate）。
            # mints の上限（合計200MB）は、左の一覧の合計の右に出す（2026-11-17 班長指示）。
            self._estimate_label = ttk.Label(
                group, text="", justify="left",
                wraplength=self._options_wraplength() - group_indent,
            )
            self._estimate_label._indent = group_indent
            self._estimate_label._sep = ttk.Separator(group, orient="horizontal")
            self._wrap_widgets.append(self._estimate_label)
            self._refresh_estimate()

    def _on_toggle_option(self, sel: Selection, key: str, checked: bool) -> None:
        sel.choices[key] = checked
        if key in ("compress", "reduce"):
            self._refresh_estimate()
        self._after_choice_changed()

    def _build_reduce_controls(self, sel: Selection, parent: tk.Misc) -> None:
        """②画像を軽くするときの解像度（設定ファイルの reduce_dpi から選ぶ）。
        圧縮の枠の中で、［画像を軽くする］の下に1行で置く（枠の中に枠を作らない）。"""
        frame = ttk.Frame(parent)
        frame.pack(side=tk.TOP, anchor="w", pady=(0, 4), padx=(theme.px(self, 28), 0))
        ttk.Label(frame, text="解像度").pack(side=tk.LEFT, padx=(0, 10))
        row = ttk.Frame(frame)
        row.pack(side=tk.LEFT)
        var = tk.IntVar(value=self._workspace.reduce_dpi_of(sel))

        def _changed() -> None:
            sel.reduce_dpi = var.get()
            self._refresh_estimate()
            self._schedule_preview(0)

        for dpi in self.config_obj.compress.reduce_dpi:
            r = ttk.Radiobutton(row, text=f"{dpi}dpi", variable=var, value=dpi, command=_changed)
            r.pack(side=tk.LEFT, padx=(0, 12))
            r.tooltip = Tooltip(
                r,
                f"画像を {dpi}dpi（A4 の紙に置いたときの細かさ）まで下げます。数字が小さいほど軽く"
                "なりますが、粗くなります。プレビューで、字が読めることを確かめてください。",
            )
        frame._var = var  # GC対策で参照を保持

    # ------------------------------------------------------------------ 大きさの見込み（4.13）
    def _refresh_estimate(self) -> None:
        """いま選んでいるファイルの、圧縮の後の大きさの見込みを出す（まだなら裏で計算する）。"""
        self._refresh_sizes()  # 圧縮の選択が変わると、一覧の下の合計も変わる
        label = self._estimate_label
        ws, record = self._workspace, self._current
        if label is None or ws is None or record is None:
            return
        try:
            if not label.winfo_exists():
                return
        except tk.TclError:
            return
        key = ws.estimate_key(record)
        if key is None:
            # 見込みがないときは、行ごと隠す（空の行が残らないように）。
            label.configure(text="")
            label.pack_forget()
            label._sep.pack_forget()
            return
        if not label.winfo_manager():
            label._sep.pack(side=tk.TOP, fill=tk.X, padx=theme.px(self, 6), pady=(4, 2))
            label.pack(side=tk.TOP, anchor="w", padx=(theme.px(self, 8), 0), pady=(2, 6))
        before = f"{record.size_bytes / (1024 * 1024):.1f}MB"
        size = ws.estimate(record)
        head = "上で選んだ圧縮の後の大きさ："
        if size is None:
            label.configure(text=f"{head}{before}→計算しています...")
            self._start_estimate(ws, key)
        elif size < 0:
            label.configure(text="大きさの見込みを出せませんでした（更新はできます）。")
        elif size >= record.size_bytes * 0.98:  # 2% 未満しか小さくならない
            label.configure(text=f"{head}{before}→ほとんど変わりません")
        else:
            label.configure(text=f"{head}{before}→約{size / (1024 * 1024):.1f}MB")

    def _start_estimate(self, ws: Workspace, key: tuple) -> None:
        """大きさの見込み（compressed_size）を裏で計算し、終わったら ws に入れて表示し直す。"""
        path, fmt, lossless, dpi, quality = key

        def _compute() -> int:
            with tempfile.TemporaryDirectory(prefix="va_mints_estimate_") as work:
                return compressed_size(path, fmt, lossless, dpi, quality, Path(work))

        def _done(size: int | None, exc: BaseException | None) -> None:
            if exc is not None:
                self._errors.record(exc, "大きさの見込み")
                size = -1  # 見込みを出せないだけ
            ws.set_estimate(key, size)
            if ws is self._workspace:
                self._refresh_estimate()
                self._refresh_sizes()  # 合計を出し直し、まだのファイルがあれば次を計算する

        self._background.submit((id(ws), key), _compute, _done)

    def _on_toggle_merge(self, checked: bool) -> None:
        # 結合のON/OFFで、右ペインの項目（グループの項目⇔このファイルの項目）が入れ替わる。
        try:
            self._workspace.set_merge(self._current, checked)
        except ValueError as exc:
            messagebox.showinfo(APP_TITLE, str(exc), parent=self)
        self._rebuild_options()
        self._after_choice_changed()

    def _after_choice_changed(self) -> None:
        self._refresh_strip()
        self._schedule_preview(0)

    def _stamp_margins(self, sel: Selection) -> tuple[float, float]:
        cfg = self.config_obj.evidence_stamp
        return sel.stamp_margin_mm or (cfg.margin_top_mm, cfg.margin_right_mm)

    def _position_label_text(self, sel: Selection) -> str:
        top, right = self._stamp_margins(sel)
        return f"現在位置：上端から{top:.0f}mm、右端から{right:.0f}mm"

    def _build_position_controls(self, sel: Selection) -> None:
        """スタンプ位置調整UI（要件定義書4.7）。上下左右ボタンでこのファイル
        （又は結合グループ）限りの余白を調整し、プレビューへ即座に反映する。"""
        frame = ttk.LabelFrame(self._options_frame, text="スタンプ位置調整（右上からの余白）")
        frame.pack(side=tk.TOP, anchor="w", pady=(2, 6), padx=(22, 0), fill=tk.X)
        self._position_label = ttk.Label(frame, text=self._position_label_text(sel))
        self._position_label.pack(side=tk.TOP, anchor="w", padx=8, pady=(6, 4))

        def _adjust(dx: int = 0, dy: int = 0) -> None:
            # dx=+1は右方向へ移動（右余白を減らす）、dy=+1は下方向へ移動（上余白を増やす）。
            # ページ外（負の余白）にはみ出さないよう0mm未満にはしない。
            top, right = self._stamp_margins(sel)
            sel.stamp_margin_mm = (
                max(0.0, top + dy * _POSITION_STEP_MM),
                max(0.0, right - dx * _POSITION_STEP_MM),
            )
            self._position_label.configure(text=self._position_label_text(sel))
            self._schedule_preview(0)

        def _reset() -> None:
            sel.stamp_margin_mm = None
            self._position_label.configure(text=self._position_label_text(sel))
            self._schedule_preview(0)

        grid = ttk.Frame(frame)
        grid.pack(side=tk.TOP, anchor="w", padx=8, pady=(0, 8))
        scope = "動かした位置は、このファイルだけに使われます。"
        for text, delta, row, column, tip in (
            ("↑", dict(dy=-1), 0, 1, "スタンプを上へ動かします。"),
            ("←", dict(dx=-1), 1, 0, "スタンプを左へ動かします。"),
            ("→", dict(dx=1), 1, 2, "スタンプを右へ動かします。"),
            ("↓", dict(dy=1), 2, 1, "スタンプを下へ動かします。"),
        ):
            b = ttk.Button(grid, text=text, width=3, command=lambda d=delta: _adjust(**d), takefocus=False)
            b.grid(row=row, column=column)
            b.tooltip = Tooltip(b, tip + scope)
        b = ttk.Button(grid, text="リセット", command=_reset, takefocus=False)
        b.grid(row=1, column=1, padx=4)
        b.tooltip = Tooltip(b, "スタンプの位置を、設定ファイル（config.yaml）の既定の余白に戻します。")

    # ------------------------------------------------------------------ プレビュー
    def _update_page_nav(self) -> None:
        has_pages = self._current is not None and self._page_count > 1
        self._page_label.configure(
            text=f"{self._page_index + 1} / {self._page_count} ページ" if has_pages else ""
        )
        if has_pages:
            self._page_scroll.set(
                self._page_index / self._page_count, (self._page_index + 1) / self._page_count
            )
        else:
            self._page_scroll.set(0.0, 1.0)  # 送るページがない（つまみが全体を占める）

    def _on_page_scroll(self, action: str, value: str, unit: str | None = None) -> None:
        """スクロールバーの操作を、ページの移動にする。"""
        if self._current is None or self._page_count <= 1:
            return
        if action == "moveto":
            target = int(float(value) * self._page_count + 0.5)
        else:  # "scroll"：矢印（units）もレールの押下（pages）も、1ページずつ送る
            target = self._page_index + int(value)
        self._go_to_page(target)

    def _on_page_wheel(self, event: tk.Event) -> str:
        self._change_page(platform_support.mousewheel_units(self, event.delta, -int(event.delta / 120)))
        return "break"

    def _go_to_page(self, index: int) -> None:
        index = max(0, min(self._page_count - 1, index))
        if index == self._page_index:
            self._update_page_nav()
            return
        self._page_index = index
        self._update_page_nav()
        self._strip.set_current(index)
        self._schedule_preview(0)

    def _change_page(self, delta: int) -> None:
        self._go_to_page(self._page_index + delta)

    def _on_preview_resize(self, event: tk.Event) -> None:
        size = (event.width, event.height)
        if size == self._preview_size:
            return
        self._preview_size = size
        if self._current is not None:
            self._schedule_preview(200)

    def _schedule_preview(self, delay_ms: int) -> None:
        """プレビューを描き直す（続けて起きた変更は、最後の1回にまとめる）。"""
        if self._preview_job is not None:
            self.after_cancel(self._preview_job)
        self._preview_job = self.after(delay_ms, self._refresh_preview)

    def _show_preview_text(self, text: str) -> None:
        self._preview_photo = None
        self._preview_label.configure(image="", text=text)

    def _refresh_preview(self) -> None:
        self._preview_job = None
        record, ws = self._current, self._workspace
        if record is None or ws is None:
            return
        label, margins = ws.preview_state(record)
        page_index, only_first = self._page_index, True
        if ws.can_edit_pages(record):
            # ページの下書き（Ver300）：表の何ページ目が、どのファイルの何ページ目か。
            info = ws.preview_page(record, self._page_index)
            if info is None:
                shown, label, rotation = None, None, 0
            else:
                path, page_index, rotation, label = info
                shown = dataclasses.replace(record, path=path)
                only_first = False
        else:
            # 描くファイル：ふつうは record。スタンプ済みでチェックを外しているときは、
            # スタンプのない姿（一時フォルダに作ったもの）。
            shown = ws.preview_record(record)
            rotation = 0
        # ②画像を軽くする（4.13）にチェックがあれば、そのページを軽くした姿を描く（画質を
        # 確かめるため。スタンプは、その上に重ねる）。
        _lossless, reduce_dpi = ws.compress_choice(record)
        if reduce_dpi is not None and shown is not None:
            fmt = "PDF" if Path(shown.path).suffix.lower() == ".pdf" else record.detected_format
            reduced = ws.reduced_preview(Path(shown.path), page_index, fmt, reduce_dpi)
            if reduced is not None:
                # 軽くした姿は1ページだけのファイル。1ページ目だけに押すスタンプは、元の
                # ページの番号で決める。
                if only_first and page_index != 0:
                    label = None
                only_first = False
                shown = dataclasses.replace(shown, path=reduced[0], detected_format=fmt)
                page_index = reduced[1]
        pad = theme.px(self, 12)
        # 枠の大きさに収める。下限を枠より大きくすると、狭い枠では画像の左右が切れ、
        # 写真の縦横比が変わったように見える（2026-11-16 班長指摘）。まだ枠の大きさが
        # 決まっていない（1 画素）ときだけ、仮の大きさを使う。
        def fit(size: int) -> int:
            return theme.px(self, 120) if size <= 1 else max(theme.px(self, 16), size - pad)

        max_w = fit(self._preview_label.winfo_width())
        max_h = fit(self._preview_label.winfo_height())
        try:
            if shown is None:
                image = None
            elif label is not None:
                stamp_config: EvidenceStampConfig = self.config_obj.evidence_stamp
                if margins is not None:
                    stamp_config = dataclasses.replace(
                        stamp_config, margin_top_mm=margins[0], margin_right_mm=margins[1]
                    )
                image = render_preview_with_stamp(
                    shown, label, stamp_config, page_index=page_index,
                    max_width=max_w, max_height=max_h, rotate_degrees=rotation,
                    only_first_page=only_first,
                )
            else:
                image = render_preview(
                    shown, page_index=page_index,
                    max_width=max_w, max_height=max_h, rotate_degrees=rotation,
                )
        except Exception as exc:  # noqa: BLE001 - プレビューの失敗で画面を止めない
            self._errors.record(exc, "プレビュー")
            image = None
        if image is not None:
            # 枠より小さい画像（画素数の少ないJPEG・PNG）は、枠いっぱいまで拡大する。
            image = fit_preview(image, max_w, max_h)
            # ページの端が分かるよう、細い枠を付ける（白い紙が地の色に溶けないように）。
            image = ImageOps.expand(image.convert("RGB"), border=1, fill=theme.BORDER)
            self._preview_photo = ImageTk.PhotoImage(image, master=self)
            self._preview_label.configure(image=self._preview_photo, text="")
        else:
            self._show_preview_text(preview_placeholder_text(record))

    # ------------------------------------------------------------------ ファイルを更新
    def _start_update(self) -> None:
        ws = self._workspace
        if ws is None or self._scan_result is None:
            messagebox.showerror(APP_TITLE, "先にフォルダを読み込んでください。")
            return
        root = ws.root
        if not root.is_dir():
            messagebox.showerror(APP_TITLE, f"対象フォルダが見つかりません：{root}")
            return

        plan = ws.plan_update()
        if not plan.has_work:
            note = f"\n\n{unstamped_image_line(plan)}" if plan.unstamped_image_count else ""
            messagebox.showinfo(APP_TITLE,
                "更新する項目がありません。\n"
                "右の「処理の項目」に、まだ行っていない処理がなく、ファイルの仕分けも不要です"
                "（以前の更新で済ませた証拠番号スタンプ・圧縮は、チェックの入った状態で表示されます）。"
                + note,
            )
            return

        # CLAUDE.md「絶対に譲れない設計原則」3：ファイルを変更する処理は、実行前に必ず
        # ユーザーの確認を得る。処理の種類ごとの件数と、まだプレビューを表示して
        # いないファイルの数を示す（2026-11-16 班長決定「案A」）。
        if not messagebox.askokcancel(APP_TITLE,
            describe_update_plan(plan, self.config_obj.upload.max_total_mb),
            icon=messagebox.WARNING if (plan.unviewed_count or plan.oversized) else messagebox.QUESTION,
            default=messagebox.CANCEL if plan.unviewed_count else messagebox.OK,
        ):
            return

        self._update_button.state(["disabled"])
        self._restore_button.state(["disabled"])
        self._scan_button.config(state=tk.DISABLED)
        self._status_label.config(text="更新中...")
        self.update_idletasks()

        # 1件の失敗（他のアプリが開いている・破損PDF等）で全体が止まり、画面が
        # 操作不能のまま残らないよう、1件ごとに例外を捕捉し、finallyでボタンを戻す。
        # 提出回ごとの、実際に置いたファイルの数（完了のお知らせに出す）。
        batch_counts: dict[int, int] = {}
        # 更新できなかったファイル（「名前（理由）」）。完了のお知らせに並べる
        # （2026-11-16 班長決定。レポートは廃止した）。
        skipped: list[str] = []
        batch_index_of = {
            id(item): batch.index for batch in plan.batches for item in batch.items
        }
        total = len(plan.updates)
        changed = moved = 0
        aborted = False
        try:
            manifest = Manifest.load(root)
            # ほかのファイルのページを使う計画（分割・挿入）のため、どれを更新するよりも前に、
            # 使うページのファイルの写しを取る（先に更新したファイルの中身を読まないように）。
            prepared = isolate_shared_pages(root, [u.item for u in plan.updates])
            # 一覧から削除したファイルを「元のファイル」フォルダへ移す（写しを取った後、
            # ほかの更新より前に。ui/workspace.py の apply_all_updates と同じ順）。
            for path in plan.removals:
                try:
                    remove_file(root, path, manifest)
                    changed += 1
                except Exception as exc:  # noqa: BLE001 - 1件の失敗を全体停止にしない
                    self._errors.record(exc, "更新")
                    skipped.append(describe_failure(path.name, exc))
            for i, update in enumerate(plan.updates, start=1):
                item = update.item
                name = (
                    item.output_filename
                    if item.merge_group is None
                    else "、".join(p.name for p in update.source_paths)
                )
                if update.keys or update.moves or update.unstamp or update.added:
                    self._status_label.config(text=f"更新中：{i} / {total} 件目...")
                    self.update_idletasks()
                try:
                    # スタンプの消去・更新・スタンプの選択の記録（ui/workspace.py の apply_update）。
                    outcome = apply_update(root, update, prepared[i - 1], manifest, self.config_obj)
                except Exception as exc:  # noqa: BLE001 - 1件の失敗を全体停止にしない
                    self._errors.record(exc, "更新")
                    proceed = messagebox.askyesno(APP_TITLE,
                        f"「{name}」の更新に失敗しました：\n{exc}\n\n"
                        "（このファイルを他のアプリで開いている場合は、閉じてから"
                        "やり直してください。このファイルは更新前のままです）\n\n"
                        "この1件をとばして続けますか？"
                        "（「いいえ」で残りの更新を中止します）",
                    )
                    skipped.append(describe_failure(name, exc))
                    if proceed:
                        continue
                    aborted = True
                    break
                if outcome is not None:
                    changed += 1 if outcome.changed or update.unstamp else 0
                    moved += 1 if outcome.moved else 0
                index = batch_index_of[id(item)]
                batch_counts[index] = batch_counts.get(index, 0) + 1
            save_choices(root, manifest)
        except Exception as exc:  # noqa: BLE001 - 想定外の失敗でも画面を操作可能なまま残す
            self._errors.record(exc, "更新")
            messagebox.showerror(APP_TITLE,
                f"想定外のエラーのため更新を中断しました：\n{exc}\n\n"
                "ここまでに更新したファイルは更新後のまま、残りは更新前のままです。",
            )
            aborted = True
        finally:
            self._scan_button.config(state=tk.NORMAL)
            try:
                # 中止・失敗で使われなかった写しを片付ける。
                discard_unused_materials(root, Manifest.load(root))
            except Exception as exc:  # noqa: BLE001 - 片付けの失敗で画面を止めない
                self._errors.record(exc, "更新")

        skip_block = format_skipped(skipped)
        result_line = f"{changed} 件のファイルを更新しました。"
        if moved:
            result_line += f"（「提出用_NN」フォルダとの間で移したファイル：{moved} 件）"
        backup_line = (
            f"更新する前のファイルは「{BACKUP_DIRNAME}」フォルダにあります。"
            "［元のファイルに戻す］で元に戻せます。"
        )
        if aborted:
            self._status_label.config(text=f"更新を中止しました（{changed} 件を更新）")
            self._show_completion_dialog(
                f"{changed} 件を更新した時点で中止しました。{skip_block}\n\n{backup_line}",
                folder=root, warning=True,
            )
        elif len(plan.batches) >= 2:
            # 要件定義書4.5「通知の必須化」：分割が発生する場合、処理完了時の通知は
            # 省略してはならない。
            batch_summary = "\n".join(
                f"・提出用_{index:02d}：{count} 件" for index, count in sorted(batch_counts.items())
            )
            limit_mb = f"{self.config_obj.upload.max_total_mb:g}MB"
            first_folder = f"提出用_{min(batch_counts):02d}" if batch_counts else "提出用_01"
            self._status_label.config(text=f"更新完了（{changed} 件・{len(plan.batches)} 回分）")
            self._show_completion_dialog(
                f"{result_line}{skip_block}\n\n"
                f"合計サイズが{limit_mb}を超えるため、{len(plan.batches)}回に分けて提出する"
                f"必要があります。「{first_folder}」から順にご提出ください。\n\n{batch_summary}\n\n"
                "各「提出用_NN」フォルダの中身を、提出回ごとに手作業でmintsに"
                f"アップロードしてください。\n\n{backup_line}",
                folder=root, warning=True,
            )
        else:
            self._status_label.config(text=f"更新完了（{changed} 件）")
            self._show_completion_dialog(
                f"{result_line}{skip_block}\n\n{backup_line}",
                folder=root, warning=bool(skipped),
            )

        # 読み込み直す。済んだ処理は検出されなくなる（スタンプは更新記録で分かる）ので、
        # 済んだ項目のチェックは外れる。
        self._rescan()

    def _rescan(self) -> None:
        if self._scan_result is None:
            return
        if self._current is not None:
            self._reselect_name = display_name(self._current, self._scan_result.root)
        self._scan(self._scan_result.root)

    # ------------------------------------------------------------------ 元のファイルに戻す
    def _start_restore(self) -> None:
        ws = self._workspace
        if ws is None:
            return
        root = ws.root
        if not has_restorable_update(root):
            messagebox.showinfo(APP_TITLE, "このフォルダには、元に戻せる更新がありません。")
            self._restore_button.state(["disabled"])
            return
        count = len(Manifest.load(root).entries)
        if not messagebox.askokcancel(APP_TITLE,
            f"このツールで更新した {count} 件のファイルを、「{BACKUP_DIRNAME}」フォルダに残した"
            "最初に更新する前のファイルに戻します。\n\n"
            "・名前を変えたファイル、結合したファイル、「提出用_NN」フォルダへ移したファイルも、"
            "元の名前・元の位置に戻ります。\n"
            "・一覧から削除したファイルは、元の位置に戻ります。追加したファイル（このフォルダに"
            "写したもの）は消します（元の場所のファイルは、そのままです）。\n"
            "・更新の後に、他のアプリでこれらのファイルを直していた場合、その内容は失われます。\n"
            + ("・まだ更新していない編集（一覧の名前に「＊」の付いたファイル）も失われます。\n"
               if self._workspace is not None and self._workspace.has_edits else "")
            + 
            f"・すべて戻すと、「{BACKUP_DIRNAME}」フォルダはなくなります。\n\n"
            "元に戻しますか？",
            icon=messagebox.WARNING,
            default=messagebox.CANCEL,
        ):
            return
        self._status_label.config(text="元のファイルに戻しています...")
        self.update_idletasks()
        try:
            result = restore_all(root)
        except Exception as exc:  # noqa: BLE001 - 失敗しても画面を操作可能なまま残す
            self._errors.record(exc, "元のファイルに戻す")
            messagebox.showerror(APP_TITLE,
                f"元に戻す処理でエラーが発生しました：\n{exc}\n\n"
                f"最初に更新する前のファイルは「{BACKUP_DIRNAME}」フォルダに残っています。",
            )
            self._rescan()
            return
        if result.failures:
            shown = "\n".join(f"・{f}" for f in result.failures[:15])
            if len(result.failures) > 15:
                shown += f"\n...ほか{len(result.failures) - 15}件"
            messagebox.showwarning(APP_TITLE,
                f"{result.restored} 件を元に戻しましたが、次のファイルは戻せませんでした。\n\n{shown}\n\n"
                f"戻せなかったファイルの、最初に更新する前のものは、「{BACKUP_DIRNAME}」フォルダに残っています。",
            )
        else:
            messagebox.showinfo(APP_TITLE, f"{result.restored} 件のファイルを、元のファイルに戻しました。")
        self._rescan()

    # ------------------------------------------------------------------ 完了のお知らせ
    def _show_completion_dialog(
        self, message: str, *, folder: Path, warning: bool = False
    ) -> None:
        """更新の完了・中止のお知らせ。

        「OK」に加えて「フォルダを開く」ボタンを併置する。フォルダを開く操作自体は
        ファイルを変更しないためCLAUDE.mdの確認UI原則（設計原則3）の対象外だが、
        ユーザーの意図しない画面遷移を避けるため、無条件には開かず選択式とする。
        """
        dialog = tk.Toplevel(self)
        dialog.title(APP_TITLE)
        dialog.transient(self)
        dialog.resizable(False, False)

        open_folder = tk.BooleanVar(value=False)

        body = ttk.Frame(dialog, padding=(16, 12))
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        icon = "⚠" if warning else "ℹ"
        ttk.Label(body, text=icon, font=("", 20)).pack(side=tk.LEFT, anchor="n", padx=(0, 12))
        ttk.Label(body, text=message, wraplength=520, justify="left").pack(
            side=tk.LEFT, fill=tk.BOTH, expand=True
        )

        button_frame = ttk.Frame(dialog, padding=(16, 0, 16, 12))
        button_frame.pack(side=tk.BOTTOM, fill=tk.X)

        def _close(open_after: bool) -> None:
            open_folder.set(open_after)
            dialog.destroy()

        open_button = ttk.Button(
            button_frame, text="フォルダを開く", command=lambda: _close(True)
        )
        open_button.pack(side=tk.RIGHT, padx=(6, 0))
        open_button.tooltip = Tooltip(
            open_button, "このお知らせを閉じて、更新したフォルダをエクスプローラーで開きます。", above=True
        )
        ok_button = ttk.Button(
            button_frame, text="OK", command=lambda: _close(False), style="Accent.TButton"
        )
        ok_button.pack(side=tk.RIGHT)
        ok_button.tooltip = Tooltip(
            ok_button, "このお知らせを閉じます（フォルダは開きません）。", above=True
        )

        dialog.protocol("WM_DELETE_WINDOW", lambda: _close(False))
        dialog.bind("<Return>", lambda _e: _close(False))
        ok_button.focus_set()
        _grab_when_viewable(dialog)
        self.wait_window(dialog)

        if open_folder.get():
            platform_support.open_folder(folder)


def main() -> None:
    try:
        app = MainWindow()
    except Exception as exc:  # noqa: BLE001 - 起動失敗を無言終了にしない
        diagnostics.ErrorLog().record(exc, "起動")
        # config.yamlの検証エラー・不存在等（load_configがYAML構文エラー・
        # キー欠落・型違いも日本語メッセージのValueErrorへ変換する）に加え、
        # 想定外の例外も含めて捕捉する。--windowedのexeでは標準エラー出力が
        # 見えず、未捕捉例外はダイアログなしの無言終了になるため、起動段階の
        # 失敗は必ずダイアログで原因を表示する（利用者がテキストエディタで
        # config.yamlを編集する運用〔CLAUDE.md〕を前提とした導線）。
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(APP_TITLE,
            "起動に失敗しました。設定ファイル（config.yaml）の内容に問題がある"
            f"可能性があります。\n\n{exc}",
        )
        root.destroy()
        return
    app.mainloop()


if __name__ == "__main__":
    main()
