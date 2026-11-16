"""VA mints メインウィンドウ（tkinter）。

Ver200 から、画面を3つのペインに分け、読み込んだフォルダの中のファイルを
その場で更新する方式にした（2026-11-16 班長指示。要件定義書 第3章・第4章冒頭）。

- 左のペイン：読み込んだファイルの名前だけ（分類A〜Dで行を色分け。見出し・列はない）。
- 中央のペイン：左で選んだファイルのプレビュー。右のペインでチェックを付けた処理
  （証拠番号スタンプ・向き補正）を行った状態を表示し、チェックを付け外しすると
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
import queue
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import ImageOps, ImageTk

from mints_prep import __version__, diagnostics
from mints_prep.config import Config, EvidenceStampConfig, load_config
from mints_prep.models import Classification, FileRecord, ScanResult
from mints_prep.processors.report_writer import format_evidence_sequence_warnings
from mints_prep.processors.updater import (
    BACKUP_DIRNAME,
    Manifest,
    has_restorable_update,
    record_stamp_choice,
    remove_stamp,
    restore_all,
    save_choices,
    update_item,
)
from mints_prep.scanner.walker import scan_folder
from mints_prep.ui import platform_support, theme
from mints_prep.ui.formatting import describe_expected_processing
from mints_prep.ui.icons import IconSet
from mints_prep.ui.preview import (
    fit_preview,
    get_page_count,
    render_preview,
    render_preview_with_stamp,
)
from mints_prep.ui.review_logic import build_pane_warnings
from mints_prep.ui.ribbon import Ribbon
from mints_prep.ui.theme import apply_theme
from mints_prep.ui.tooltip import Tooltip
from mints_prep.user_settings import (
    STAMP_FONT_SIZE_MAX,
    STAMP_FONT_SIZE_MIN,
    UserSettings,
    apply_user_settings,
    load_user_settings,
    save_user_settings,
)
from mints_prep.ui.workspace import (
    MERGE_KEY,
    PaneView,
    Selection,
    Workspace,
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
    ("rotate", "向き補正"),
    ("stamp", "証拠番号スタンプ"),
    (MERGE_KEY, "枝番ファイル結合"),
)

# スタンプ位置調整ボタン1回分の移動量（mm）。一次資料に定めはなく、UI操作上の
# 実装値（CLAUDE.md「数値パラメータの設定ファイル化」原則が対象とする
# mints側の制約値〔200MB・100文字等〕とは性質が異なるため、設定ファイル化は
# せずここに定数として置く）。
_POSITION_STEP_MM = 2.0

# 本ツールでは対処できない（検出のみの）項目の文字色。操作不可に加えて赤字に
# することで、対処が必要な検出事項であることを一目で分かるようにする。
_UNFIXABLE_TEXT_COLOR = "#c00000"


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
        self.geometry("1300x760")
        self.minsize(960, 560)

        self._input_dir_var = tk.StringVar(value="")
        # 要件定義書4.1「入力フォルダ選択」：サブフォルダを含めるかはオプション。
        # 意図せず対象外の書類（他事件のファイル等）まで読み込む事故を避ける
        # ため、既定はOFF（トップレベルのみ）とする。
        self._include_subfolders_var = tk.BooleanVar(value=False)
        self._scan_result: ScanResult | None = None
        self._workspace: Workspace | None = None
        self._queue: "queue.Queue[object]" = queue.Queue()
        # 起きた誤りの記録（型と場所だけ。診断情報〔ヘルプ→診断情報をコピー〕に載せる）。
        self._errors = diagnostics.ErrorLog()

        # いま左ペインで選んでいるファイルと、そのプレビューの状態。
        self._current: FileRecord | None = None
        self._row_records: dict[str, FileRecord] = {}
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

    def report_callback_exception(self, exc_type, exc, tb) -> None:
        """画面の処理（ボタン・キーなど）で起きた、予期しない誤りを覚える（Tk の既定どおり、出力にも出す）。"""
        self._errors.record(exc, "画面の処理")
        super().report_callback_exception(exc_type, exc, tb)

    # フォルダパスはテキストボックス（手動入力可）と連動させるため、常に
    # StringVarから読み取る（ボタンでの選択と手動入力の両方を単一の入力源にする）。
    @property
    def _input_dir(self) -> Path | None:
        text = self._input_dir_var.get().strip()
        return Path(text) if text else None

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

    # ------------------------------------------------------------------ オプション
    def _open_options(self) -> None:
        """［ファイル］→［オプション］：証拠番号スタンプの文字の大きさを変える。

        既定は config.yaml の値（16ポイント）。変えた値は利用者ごとに保存し、次に
        起動したときも使う。すでにスタンプを付けたファイルには影響しない（付け直すには、
        スタンプのチェックを外して更新し、入れ直してもう一度更新する）。
        """
        default_size = self._base_config.evidence_stamp.font_size_pt
        current = self.config_obj.evidence_stamp.font_size_pt

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
            text=(
                f"既定：{default_size:g} ポイント（{STAMP_FONT_SIZE_MIN}〜{STAMP_FONT_SIZE_MAX}）\n"
                "すでにスタンプを付けたファイルの大きさは変わりません。"
            ),
            foreground=theme.NOTE, justify="left",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))

        buttons = ttk.Frame(dialog, padding=(16, 4, 16, 14))
        buttons.pack(side=tk.BOTTOM, fill=tk.X)

        def _reset() -> None:
            size_var.set(f"{default_size:g}")

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
            settings = UserSettings(stamp_font_size_pt=None if size == default_size else size)
            try:
                save_user_settings(settings)
            except OSError as exc:
                self._errors.record(exc, "オプションの保存")
                messagebox.showerror(
                    APP_TITLE,
                    "設定を保存できませんでした。この画面を閉じるまでの間だけ、変えた大きさを使います。",
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
        b.tooltip = Tooltip(b, f"文字の大きさを、既定（{default_size:g} ポイント）に戻します。", above=True)

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
                  f"右 {c.evidence_stamp.margin_right_mm:g}mm・見た目 {c.evidence_stamp.style}",
                  f"個人情報パターン：{len(c.pii_detection.patterns)} 件", ""]
        lines.append("■ 状態")
        path = self._input_dir
        if path is None:
            lines.append("入力フォルダの欄：設定なし")
        else:
            try:
                exists = "フォルダがある" if path.is_dir() else "見つからない"
            except OSError:
                exists = "確かめられない"
            lines.append(f"入力フォルダの欄：設定あり（{exists}）")
        lines.append(f"サブフォルダも含める：{'はい' if self._include_subfolders_var.get() else 'いいえ'}")
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
        ttk.Label(box, text="入力フォルダ", style="Ribbon.TLabel").grid(
            row=0, column=0, sticky="w", padx=(2, 6), pady=1
        )
        # フォルダパス欄は、画面全幅まで伸ばさず固定幅にとどめる（従来方針）。
        e = ttk.Entry(box, textvariable=self._input_dir_var, width=42)
        e.grid(row=0, column=1, sticky="w", pady=1)
        e.tooltip = Tooltip(e, "読み込むファイルの入ったフォルダです。手で入力するか、右の［...］で選びます。")
        # フォルダ選択ボタンは、Windows標準の「参照」ダイアログの慣行に倣い
        # 「...」の省略表記にとどめる。
        b = ttk.Button(box, text="...", width=3, command=self._choose_input_dir,
                       style="Ribbon.TButton", takefocus=False)
        b.grid(row=0, column=2, sticky="w", padx=(2, 0), pady=1)
        b.tooltip = Tooltip(b, "入力フォルダを選びます。")
        # 'clam'テーマのttk.Checkbuttonはチェック時のインジケータが「✕」
        # 形状に描画され、選択済みではなく利用不可のように見えるため、
        # 標準tk.Checkbuttonを使う。
        cb = tk.Checkbutton(
            box,
            text="サブフォルダ内のファイルも含める",
            variable=self._include_subfolders_var,
        )
        cb.grid(row=1, column=0, columnspan=3, sticky="w")
        cb.tooltip = Tooltip(
            cb,
            "チェックを入れると、入力フォルダの下のサブフォルダの中のファイルも読み込みます。"
            "既定は入力フォルダ直下のみです（他事件のファイル等を意図せず読み込む事故を避けるため）。"
            f"「{BACKUP_DIRNAME}」フォルダは、どちらの場合も読み込みません。",
        )
        g.block(box)
        # ボタン名は「スキャン」を避ける（実物のスキャナー〔ScanSnap等〕による
        # 読み取り操作と紛らわしいため）。
        self._scan_button = g.large(
            "ファイルを読み込み", self._start_scan, style="RibbonAccent.TButton",
            tip="入力フォルダの中のファイルを読み込み、mintsの制約（ファイル形式・サイズ・"
                "ファイル名等）に適合するかを検査して、一覧に表示します。ファイルは変更しません。",
        )

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
        # ファイルを読み込むまでは、更新は押せない（グレーアウト。2026-11-13 班長指示）。
        # ［元のファイルに戻す］は、読み込んだフォルダに更新の記録があるときだけ押せる。
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
        tree = ttk.Treeview(parent, show="tree", selectmode="browse", style="Files.Treeview")
        tree.column("#0", width=theme.px(self, 300), minwidth=theme.px(self, 100), stretch=True)
        for tag, color in _TAG_COLORS.items():
            tree.tag_configure(tag, background=color)
        scroll = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree.bind("<<TreeviewSelect>>", self._on_select_file)
        self._tree = tree

    def _build_preview(self, parent: ttk.Frame) -> None:
        """中央のペイン：選んだファイルのプレビュー（チェックした処理を行った状態）。"""
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
            return "読み込んだフォルダに、このツールで更新したファイルがあるときに押せます。" + base
        return base

    # ------------------------------------------------------------------ フォルダ選択
    def _choose_input_dir(self) -> None:
        selected = filedialog.askdirectory(title="入力フォルダを選択", mustexist=True)
        if selected:
            self._input_dir_var.set(selected)

    # ------------------------------------------------------------------ 読み込み実行
    def _start_scan(self) -> None:
        input_dir = self._input_dir
        if input_dir is None:
            messagebox.showerror(APP_TITLE, "入力フォルダを選択してください。")
            return
        if not input_dir.is_dir():
            messagebox.showerror(APP_TITLE, f"入力フォルダが見つかりません：{input_dir}")
            return
        if input_dir.name == BACKUP_DIRNAME:
            messagebox.showerror(APP_TITLE,
                f"「{BACKUP_DIRNAME}」フォルダは、更新する前のファイルを残しておくフォルダのため、"
                "入力フォルダにできません。1つ上のフォルダを選んでください。",
            )
            return
        self._scan(input_dir, self._include_subfolders_var.get())

    def _scan(self, input_dir: Path, include_subfolders: bool) -> None:
        self._scan_button.config(state=tk.DISABLED)
        # 読み込み直しの間は、更新も復元も押せなくする（結果が入れ替わる途中のため）。
        self._update_button.state(["disabled"])
        self._restore_button.state(["disabled"])
        self._status_label.config(text="読み込み中...")
        self._clear_file_list()

        # input_dir・サブフォルダ設定はメインスレッドで確定させ、ワーカー
        # スレッドには値として渡す（別スレッドから直接Tkの変数を読み取らない）。
        thread = threading.Thread(
            target=self._run_scan_worker, args=(input_dir, include_subfolders), daemon=True
        )
        thread.start()
        self.after(100, self._poll_scan_queue)

    def _run_scan_worker(self, input_dir: Path, include_subfolders: bool) -> None:
        try:
            result = scan_folder(
                input_dir, self.config_obj, include_subfolders=include_subfolders
            )
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
        self._populate_file_list(item)
        self._update_button.state(["!disabled"])
        self._restore_button.state(["!disabled"] if manifest else ["disabled"])

    # ------------------------------------------------------------------ 左ペイン
    def _clear_file_list(self) -> None:
        self._tree.delete(*self._tree.get_children())
        self._row_records = {}
        self._current = None
        self._show_options_placeholder()
        self._show_preview_text(
            "［ファイルを読み込み］で読み込んだファイルを、左の一覧で選ぶと、\nここにプレビューを表示します。"
        )
        self._page_index, self._page_count = 0, 1
        self._update_page_nav()

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
            return
        record = self._row_records.get(selection[0])
        if record is None or record is self._current:
            return
        self._current = record
        self._workspace.mark_viewed(record)
        self._page_index = 0
        self._page_count = max(1, get_page_count(record))
        self._update_page_nav()
        self._rebuild_options()
        self._schedule_preview(0)

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
        if warnings:
            ttk.Separator(frame, orient="horizontal").pack(side=tk.TOP, fill=tk.X, pady=(6, 6))

        self._build_process_items(view)

        if notes:
            ttk.Separator(frame, orient="horizontal").pack(side=tk.TOP, fill=tk.X, pady=(10, 6))
            self._wrap_label("備考", foreground=theme.NOTE).pack(side=tk.TOP, anchor="w")
            for note in notes:
                self._wrap_label(f"・{note}", foreground=theme.NOTE).pack(side=tk.TOP, anchor="w")

        self._bind_wheel(frame)

    def _checkbutton(self, text: str, var: tk.BooleanVar, enabled: bool = True) -> tk.Checkbutton:
        # ttk.Checkbuttonはwraplengthオプションを持たないため、長い説明文を
        # 折り返し表示できる標準tk.Checkbuttonを使う。
        cb = tk.Checkbutton(
            self._options_frame,
            variable=var,
            text=text,
            wraplength=self._options_wraplength(),
            justify="left",
            anchor="w",
            state=tk.NORMAL if enabled else tk.DISABLED,
        )
        if not enabled:
            cb.configure(disabledforeground=theme.DISABLED_TEXT)
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
        for key, name in PROCESS_ITEMS:
            if key == MERGE_KEY and view is not None and view.merge_option is not None:
                var = tk.BooleanVar(value=view.merge_checked)
                cb = self._checkbutton(view.merge_option.label, var)
                cb.configure(command=lambda v=var: self._on_toggle_merge(v.get()))
            elif key in available:
                var = tk.BooleanVar(value=sel.choices.get(key, False))
                cb = self._checkbutton(available[key].label, var)
                cb.configure(command=lambda k=key, v=var: self._on_toggle_option(sel, k, v.get()))
            else:
                cb = self._checkbutton(name, tk.BooleanVar(value=False), enabled=False)
            cb.pack(side=tk.TOP, anchor="w", pady=3, fill=tk.X)
            if key in available and key == "rotate":
                self._build_rotation_controls(sel)
            # スタンプ済みのファイルは位置を変えられない（付け直すには、いったん外して
            # 更新してから、もう一度付ける）ので、位置調整は出さない。
            if key in available and key == "stamp" and not sel.stamped:
                self._build_position_controls(sel)

    def _on_toggle_option(self, sel: Selection, key: str, checked: bool) -> None:
        sel.choices[key] = checked
        self._after_choice_changed()

    def _on_toggle_merge(self, checked: bool) -> None:
        # 結合のON/OFFで、右ペインの項目（グループの項目⇔このファイルの項目）が入れ替わる。
        self._workspace.set_merge(self._current, checked)
        self._rebuild_options()
        self._after_choice_changed()

    def _after_choice_changed(self) -> None:
        self._schedule_preview(0)

    def _build_rotation_controls(self, sel: Selection) -> None:
        """向き補正（4.6）の回転方向選択。内容の向きはツールには解析できない
        ため、右90°／左90°をユーザーが選び、プレビューで結果を確認する。"""
        frame = ttk.LabelFrame(self._options_frame, text="向き補正の回転方向")
        frame.pack(side=tk.TOP, anchor="w", pady=(2, 6), padx=(22, 0), fill=tk.X)
        row = ttk.Frame(frame)
        row.pack(side=tk.TOP, anchor="w", padx=8, pady=(6, 8))
        var = tk.IntVar(value=sel.rotate_degrees)

        def _changed() -> None:
            sel.rotate_degrees = var.get()
            self._schedule_preview(0)

        for text, value, tip in (
            ("左に90°", 270, "ページを左（反時計回り）に90°回転します。"),
            ("右に90°", 90, "ページを右（時計回り）に90°回転します。"),
        ):
            r = ttk.Radiobutton(row, text=text, variable=var, value=value, command=_changed)
            r.pack(side=tk.LEFT, padx=(0, 12))
            r.tooltip = Tooltip(
                r, tip + "選ぶとプレビューがすぐ切り替わるので、読める向きになることを確かめてください。"
            )
        frame._var = var  # GC対策で参照を保持

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
        label, margins, rotate_degrees, rotate_on = ws.preview_state(record)
        # 描くファイル：ふつうは record。スタンプ済みでチェックを外しているときは、
        # スタンプのない姿（一時フォルダに作ったもの）。
        shown = ws.preview_record(record)
        # 更新のときの回転は、向き不一致のページだけに行う（output_writer）。
        # プレビューも同じ条件のページだけを回して、見た目を一致させる。
        rotation = 0
        if rotate_on and record.pdf_finding is not None:
            if (self._page_index + 1) in record.pdf_finding.orientation_mismatch_pages:
                rotation = rotate_degrees
        pad = theme.px(self, 12)
        max_w = max(theme.px(self, 120), self._preview_label.winfo_width() - pad)
        max_h = max(theme.px(self, 120), self._preview_label.winfo_height() - pad)
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
                    shown, label, stamp_config, page_index=self._page_index,
                    max_width=max_w, max_height=max_h, rotate_degrees=rotation,
                )
            else:
                image = render_preview(
                    shown, page_index=self._page_index,
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
            messagebox.showerror(APP_TITLE, f"読み込んだフォルダが見つかりません：{root}")
            return

        plan = ws.plan_update()
        if not plan.has_work:
            note = f"\n\n{unstamped_image_line(plan)}" if plan.unstamped_image_count else ""
            messagebox.showinfo(APP_TITLE,
                "更新する項目がありません。\n"
                "右の「処理の項目」でチェックの入っている処理がなく、ファイルの仕分けも不要です。" + note,
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
            for i, update in enumerate(plan.updates, start=1):
                item = update.item
                name = (
                    item.output_filename
                    if item.merge_group is None
                    else "、".join(p.name for p in update.source_paths)
                )
                if update.keys or update.moves or update.unstamp:
                    self._status_label.config(text=f"更新中：{i} / {total} 件目...")
                    self.update_idletasks()
                    try:
                        if update.unstamp:
                            # 証拠番号スタンプのチェックを外したファイル：スタンプを消す。
                            remove_stamp(root, update.source_paths[0], manifest, self.config_obj)
                        outcome = update_item(
                            root, item, update.dest_dir, manifest,
                            self.config_obj.evidence_stamp, self.config_obj.filename,
                        )
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
                    changed += 1 if outcome.changed or update.unstamp else 0
                    moved += 1 if outcome.moved else 0
                    updated_path = outcome.path
                else:
                    updated_path = update.source_paths[0]
                # 証拠番号スタンプを付けないと決めたファイルかどうかを覚える（読み込み
                # 直したときに、スタンプへ自動でチェックを付けないため）。
                record_stamp_choice(root, updated_path, manifest, update.declines_stamp)
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
        self._scan(self._scan_result.root, self._scan_result.include_subfolders)

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
            "・更新の後に、他のアプリでこれらのファイルを直していた場合、その内容は失われます。\n"
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
