"""逐次書き出しレビュー画面（要件定義書4.5・4.7、CLAUDE.md「絶対に譲れない設計
原則」3の具体的なUI実装）。

書き出し対象ファイル（又は枝番ファイル結合グループ）を1件ずつ順に処理する。各回、
画面には「今どのファイルを処理しているか」（進捗表示）・対象ファイルの
プレビュー（証拠番号を既に記載済みでないかを目視確認できるように）・
適用する処理（ファイル名整形／メタデータ除去／証拠番号スタンプ）を選択する
チェックボックス（ファイルの状態に応じた既定値つき、手動で変更可）を表示する。

証拠番号スタンプのチェックボックスがON/OFFされた際は、プレビューを即座に
再生成し、ON時は実際にラベルを焼き込んだ見た目、OFF時は未適用の見た目に
切り替える（原本に証拠番号が既に記載されている場合に重複スタンプを避ける
判断を助けるため）。

証拠番号スタンプのチェックがONの間は、スタンプ位置調整（要件定義書4.7
「スタンプ位置調整」）の上下左右ボタンを表示する。既定位置（config.yamlの
margin_top_mm／margin_right_mm）からのオフセットをこの画面上でのみ調整
でき、調整結果はこのファイル（又は結合グループ）にのみ適用される
（config.yamlの既定値自体は変更しない）。

チェックボックスの一覧には、本ツールでは対処できない（検出のみの）項目
（パスワード・操作制限・用紙寸法・向き不一致・墨消し完全性の残存・個人情報
パターン・単体200MB超過）を先頭に、グレーアウト・赤字・OFF状態で表示し、
区切り線を挟んで実行可否を選べる処理を続ける。これにより、書き出し完了後に
まとめて警告する画面を別途設けなくても、その場で必ずすべての検出結果が
（対処できないものから先に）目に入る。

「キャンセル」ボタンは、この1件（又は1結合グループ）を書き出さずに次の
対象へ進む（要件定義書4.5・4.7の逐次処理はここで打ち切らない）。複数件を
まとめて処理を打ち切りたい場合は、ウィンドウを閉じる操作（×ボタン）を使う
（残りすべてが未処理のまま入力フォルダに残る、従来の「中止」と同じ挙動）。
"""

from __future__ import annotations

import dataclasses
import tkinter as tk
from tkinter import ttk

from PIL import ImageTk

from mints_prep.config import EvidenceStampConfig, FilenameConfig
from mints_prep.models import FileRecord
from mints_prep.processors.output_writer import PlannedCopy
from mints_prep.ui.preview import get_page_count, render_preview, render_preview_with_stamp
from mints_prep.ui.review_logic import ReviewItem, apply_review_choices, build_review_options

# スタンプ位置調整ボタン1回分の移動量（mm）。一次資料に定めはなく、UI操作上の
# 実装値（CLAUDE.md「数値パラメータの設定ファイル化」原則が対象とする
# mints側の制約値〔200MB・100文字等〕とは性質が異なるため、設定ファイル化は
# せずここに定数として置く）。
_POSITION_STEP_MM = 2.0

# 本ツールでは対処できない（検出のみの）項目のチェックボックス文字色。
# グレーアウト（操作不可）に加えて赤字にすることで、対処が必要な検出事項
# であることを一目で分かるようにする。
_UNFIXABLE_TEXT_COLOR = "#c00000"


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


def _describe_item(item: ReviewItem) -> str:
    prelim = item.preliminary
    if prelim.merge_group is not None:
        names = "、".join(m.record.path.name for m in prelim.merge_group.members)
        return f"{names}\n→ {prelim.output_filename}（枝番ファイル結合）"
    return prelim.record.path.name


def _preview_placeholder_text(record: FileRecord) -> str:
    if record.pdf_finding is not None and record.pdf_finding.has_password:
        return "プレビュー非対応\n（パスワード付きファイル）"
    if record.detected_format not in ("PDF", "JPEG", "PNG"):
        return "プレビュー非対応\n（音声・動画ファイル）"
    return "プレビューを生成できませんでした"


def _preview_members(item: ReviewItem) -> list[tuple[FileRecord, str | None]]:
    """プレビュー対象の (レコード, スタンプラベル) の一覧を返す。

    結合グループは全メンバーを列挙する（スタンプは各メンバーの全ページに
    そのメンバー自身の枝番ラベルで焼き込まれるため、「原本に証拠番号が既に
    記載されていないか」の目視確認は代表メンバーだけでは足りない。要件定義書
    第4章「プレビューによる目視確認を必須とする」）。各メンバーのラベルは
    merge_branch_groupが実際に焼き込むものと同じ（そのメンバー自身の枝番）。
    単独ファイルは1件のみ。
    """
    prelim = item.preliminary
    if prelim.merge_group is not None:
        return [
            (m.record, m.match.evidence_number_label) for m in prelim.merge_group.members
        ]
    return [(item.preview_record, prelim.stamp_label)]


class WriteReviewDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        item: ReviewItem,
        index: int,
        total: int,
        filename_config: FilenameConfig,
        evidence_stamp_config: EvidenceStampConfig,
    ) -> None:
        super().__init__(parent)
        self.title("書き出し内容の確認")
        # プレビュー画像（既定最大560px）＋ページ送りUIが、既定のウィンドウ
        # 高さでは収まらず下端が見切れることがあったため、縦方向に広げた。
        # ただし1366×768等の低解像度ノートPCでは780pxが画面からはみ出し、
        # 下端の確定・キャンセルボタンが押せなくなる（モーダルのため実質
        # 操作不能になる）ので、画面高に収まるようクランプする。
        height = min(780, max(560, self.winfo_screenheight() - 80))
        self.geometry(f"950x{height}")
        self.minsize(800, min(620, height))
        self.transient(parent)
        _grab_when_viewable(self)

        self.result: PlannedCopy | None = None
        # ウィンドウを閉じる操作（×ボタン）でのみTrueになる。「キャンセル」
        # ボタンはこの1件だけをスキップする（残りの逐次処理は続行）。
        self.abort_all = False
        self._item = item
        self._filename_config = filename_config
        self._evidence_stamp_config = evidence_stamp_config
        # プレビュー対象（結合グループは全メンバー、単独ファイルは1件）と、
        # 各対象に焼き込まれるスタンプラベル。ファイル送りUIで切り替える。
        self._members = _preview_members(item)
        self._member_index = 0
        self._choice_vars: dict[str, tk.BooleanVar] = {}
        self._preview_label: ttk.Label | None = None
        self._preview_photo: ImageTk.PhotoImage | None = None  # GC対策で参照を保持
        # プレビュー画面ですべてのページを確認できるようにする（原本に既に
        # 証拠番号が記載されていないかの確認は1ページ目に限らない）。
        self._page_index = 0
        self._page_count = max(1, get_page_count(self._current_record()))
        self._page_label: ttk.Label | None = None
        self._prev_page_button: ttk.Button | None = None
        self._next_page_button: ttk.Button | None = None
        self._member_label: ttk.Label | None = None
        self._prev_member_button: ttk.Button | None = None
        self._next_member_button: ttk.Button | None = None
        # 要件定義書4.7「スタンプ位置調整」：このファイル（又は結合グループ）
        # のみに適用する余白。config.yamlの既定値から開始し、上下左右ボタンで
        # このダイアログ限りで調整できる（config.yamlの既定値自体は変更しない）。
        self._stamp_margin_top_mm = evidence_stamp_config.margin_top_mm
        self._stamp_margin_right_mm = evidence_stamp_config.margin_right_mm
        self._position_label: ttk.Label | None = None
        # 向き補正（4.6）の回転方向（時計回りの度数。右90°=90、左90°=270）。
        # 内容の向きはツールには解析できないため、プレビューを見たユーザーが
        # 方向を選ぶ。既定は左90°。
        self._rotate_degrees_var = tk.IntVar(value=270)

        ttk.Label(
            self,
            text=f"処理中：{index} / {total} 件目",
            padding=(12, 12, 12, 0),
            font=("", 11, "bold"),
        ).pack(side=tk.TOP, fill=tk.X)
        ttk.Label(
            self,
            text=_describe_item(item),
            padding=(12, 2, 12, 10),
            wraplength=920,
            justify="left",
        ).pack(side=tk.TOP, fill=tk.X)

        body = ttk.Frame(self)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=12)

        left = ttk.Frame(body)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        right = ttk.Frame(body, width=440)
        right.pack(side=tk.RIGHT, fill=tk.Y, padx=(12, 0))
        right.pack_propagate(False)

        self._build_preview(right)
        self._build_options(left)
        self._refresh_preview()

        button_frame = ttk.Frame(self, padding=12)
        button_frame.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Button(
            button_frame, text="キャンセル（この文書はスキップ）", command=self._on_cancel
        ).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(button_frame, text="この内容で書き出す", command=self._on_confirm).pack(
            side=tk.RIGHT
        )

        self.protocol("WM_DELETE_WINDOW", self._on_abort)

    def _build_options(self, parent: tk.Misc) -> None:
        options = build_review_options(self._item, self._filename_config)
        if not options:
            ttk.Label(
                parent,
                text="検出された問題はありません。このまま書き出します。",
                wraplength=460,
                justify="left",
            ).pack(side=tk.TOP, anchor="w", pady=8)
            return

        # build_review_optionsは、本ツールでは対処できない（検出のみの）項目
        # （enabled=False）を先に並べ、実行可否を選べる項目（enabled=True）を
        # 続けて並べる。検出のみの項目を目立たせるため一覧の先頭に置き、
        # 実行可否を選べる項目との境界に区切り線を挟む。
        has_info = any(not opt.enabled for opt in options)
        if has_info:
            ttk.Label(
                parent,
                text="以下は本ツールでは対処できません（検出のみ）。他のアプリで"
                "対処のうえ、本ツールに再度読み込んで再検証してください。",
                wraplength=460,
                justify="left",
                foreground=_UNFIXABLE_TEXT_COLOR,
            ).pack(side=tk.TOP, anchor="w", pady=(0, 6))

        separator_shown = False
        for opt in options:
            if opt.enabled and has_info and not separator_shown:
                ttk.Separator(parent, orient="horizontal").pack(
                    side=tk.TOP, fill=tk.X, pady=(6, 10)
                )
                separator_shown = True

            var = tk.BooleanVar(value=opt.default_checked)
            self._choice_vars[opt.key] = var
            if opt.key in ("stamp", "rotate"):
                # 証拠番号スタンプ・向き補正の選択はプレビューの見た目に直結
                # するため、チェックボックスのON/OFFに連動してプレビューを
                # 再生成する（スタンプ：重複スタンプの目視確認、向き補正：
                # 回転後に内容が読める向きになるかの目視確認）。
                var.trace_add("write", lambda *_args: self._refresh_preview())
            # ttk.Checkbuttonはwraplengthオプションを持たないため、長い説明文を
            # 折り返し表示できる標準tk.Checkbuttonを使う。
            checkbutton = tk.Checkbutton(
                parent,
                variable=var,
                text=opt.label,
                wraplength=440,
                justify="left",
                anchor="w",
                state=tk.NORMAL if opt.enabled else tk.DISABLED,
            )
            if not opt.enabled:
                # 検出のみ・対処不可であることが一目で分かるよう、グレーアウト
                # （操作不可）に加えて文字色も赤にする。
                checkbutton.configure(
                    disabledforeground=_UNFIXABLE_TEXT_COLOR, fg=_UNFIXABLE_TEXT_COLOR
                )
            checkbutton.pack(side=tk.TOP, anchor="w", pady=6, fill=tk.X)

        if "rotate" in self._choice_vars:
            self._build_rotation_controls(parent)
        if "stamp" in self._choice_vars:
            self._build_position_controls(parent)

    def _build_rotation_controls(self, parent: tk.Misc) -> None:
        """向き補正（4.6）の回転方向選択。内容の向きはツールには解析できない
        ため、右90°／左90°をユーザーが選び、プレビューで結果を確認する。
        """
        frame = ttk.LabelFrame(parent, text="向き補正の回転方向")
        frame.pack(side=tk.TOP, anchor="w", pady=(4, 6), fill=tk.X)
        row = ttk.Frame(frame)
        row.pack(side=tk.TOP, anchor="w", padx=8, pady=(6, 8))
        # 既定（左に90°）を先頭に並べる。
        ttk.Radiobutton(
            row,
            text="左に90°",
            variable=self._rotate_degrees_var,
            value=270,
            command=self._refresh_preview,
        ).pack(side=tk.LEFT)
        ttk.Radiobutton(
            row,
            text="右に90°",
            variable=self._rotate_degrees_var,
            value=90,
            command=self._refresh_preview,
        ).pack(side=tk.LEFT, padx=(12, 0))

    def _build_position_controls(self, parent: tk.Misc) -> None:
        """スタンプ位置調整UI（要件定義書4.7）。上下左右ボタンでこのファイル
        （又は結合グループ）限りの余白を調整し、プレビューへ即座に反映する。
        """
        frame = ttk.LabelFrame(parent, text="スタンプ位置調整（右上からの余白）")
        frame.pack(side=tk.TOP, anchor="w", pady=(10, 6), fill=tk.X)

        self._position_label = ttk.Label(frame, text=self._position_label_text())
        self._position_label.pack(side=tk.TOP, anchor="w", padx=8, pady=(6, 4))

        grid = ttk.Frame(frame)
        grid.pack(side=tk.TOP, padx=8, pady=(0, 8))
        ttk.Button(
            grid, text="↑", width=3, command=lambda: self._adjust_stamp_position(dy=-1)
        ).grid(row=0, column=1)
        ttk.Button(
            grid, text="←", width=3, command=lambda: self._adjust_stamp_position(dx=-1)
        ).grid(row=1, column=0)
        ttk.Button(grid, text="リセット", command=self._reset_stamp_position).grid(
            row=1, column=1, padx=4
        )
        ttk.Button(
            grid, text="→", width=3, command=lambda: self._adjust_stamp_position(dx=1)
        ).grid(row=1, column=2)
        ttk.Button(
            grid, text="↓", width=3, command=lambda: self._adjust_stamp_position(dy=1)
        ).grid(row=2, column=1)

    def _position_label_text(self) -> str:
        return (
            f"現在位置：上端から{self._stamp_margin_top_mm:.0f}mm、"
            f"右端から{self._stamp_margin_right_mm:.0f}mm"
        )

    def _adjust_stamp_position(self, dx: int = 0, dy: int = 0) -> None:
        # dx=+1は右方向へ移動（margin_right_mmを減らす）、dy=+1は下方向へ移動
        # （margin_top_mmを増やす）。ページ外（負の余白）にはみ出さないよう
        # 0mm未満にはならないようクランプする。
        self._stamp_margin_right_mm = max(0.0, self._stamp_margin_right_mm - dx * _POSITION_STEP_MM)
        self._stamp_margin_top_mm = max(0.0, self._stamp_margin_top_mm + dy * _POSITION_STEP_MM)
        if self._position_label is not None:
            self._position_label.configure(text=self._position_label_text())
        self._refresh_preview()

    def _reset_stamp_position(self) -> None:
        self._stamp_margin_top_mm = self._evidence_stamp_config.margin_top_mm
        self._stamp_margin_right_mm = self._evidence_stamp_config.margin_right_mm
        if self._position_label is not None:
            self._position_label.configure(text=self._position_label_text())
        self._refresh_preview()

    def _effective_stamp_config(self) -> EvidenceStampConfig:
        return dataclasses.replace(
            self._evidence_stamp_config,
            margin_top_mm=self._stamp_margin_top_mm,
            margin_right_mm=self._stamp_margin_right_mm,
        )

    def _current_record(self) -> FileRecord:
        return self._members[self._member_index][0]

    def _current_stamp_label(self) -> str | None:
        return self._members[self._member_index][1]

    def _build_preview(self, parent: tk.Misc) -> None:
        # 結合グループ（メンバー2件以上）のみ、ファイル送りUIを表示する。
        # スタンプは全メンバーに焼き込まれるため、重複スタンプの目視確認は
        # 代表メンバーだけでは足りない（要件定義書第4章）。
        if len(self._members) > 1:
            member_nav = ttk.Frame(parent)
            member_nav.pack(side=tk.TOP, fill=tk.X, pady=(0, 6))
            self._prev_member_button = ttk.Button(
                member_nav, text="◀ 前のファイル", command=lambda: self._change_member(-1)
            )
            self._prev_member_button.pack(side=tk.LEFT)
            self._member_label = ttk.Label(
                member_nav, text=self._member_label_text(), anchor="center"
            )
            self._member_label.pack(side=tk.LEFT, expand=True, fill=tk.X)
            self._next_member_button = ttk.Button(
                member_nav, text="次のファイル ▶", command=lambda: self._change_member(1)
            )
            self._next_member_button.pack(side=tk.RIGHT)
        # ページ送りは、初期表示ページが1枚でもメンバー切替で複数ページに
        # なり得るため、結合グループでは常に表示する（端では無効化される）。
        if self._page_count > 1 or len(self._members) > 1:
            nav = ttk.Frame(parent)
            nav.pack(side=tk.TOP, fill=tk.X, pady=(0, 6))
            self._prev_page_button = ttk.Button(
                nav, text="◀ 前のページ", command=lambda: self._change_page(-1)
            )
            self._prev_page_button.pack(side=tk.LEFT)
            self._page_label = ttk.Label(nav, text=self._page_label_text(), anchor="center")
            self._page_label.pack(side=tk.LEFT, expand=True, fill=tk.X)
            self._next_page_button = ttk.Button(
                nav, text="次のページ ▶", command=lambda: self._change_page(1)
            )
            self._next_page_button.pack(side=tk.RIGHT)
        self._update_page_nav_state()
        self._update_member_nav_state()
        self._preview_label = ttk.Label(parent, anchor="center", justify="center")
        self._preview_label.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    def _page_label_text(self) -> str:
        return f"{self._page_index + 1} / {self._page_count} ページ"

    def _member_label_text(self) -> str:
        name = self._current_record().path.name
        return f"ファイル {self._member_index + 1} / {len(self._members)}：{name}"

    def _update_member_nav_state(self) -> None:
        if self._member_label is not None:
            self._member_label.configure(text=self._member_label_text())
        if self._prev_member_button is not None:
            self._prev_member_button.configure(
                state=tk.NORMAL if self._member_index > 0 else tk.DISABLED
            )
        if self._next_member_button is not None:
            self._next_member_button.configure(
                state=tk.NORMAL if self._member_index < len(self._members) - 1 else tk.DISABLED
            )

    def _change_member(self, delta: int) -> None:
        new_index = self._member_index + delta
        if not (0 <= new_index < len(self._members)):
            return
        self._member_index = new_index
        self._page_index = 0
        self._page_count = max(1, get_page_count(self._current_record()))
        self._update_member_nav_state()
        self._update_page_nav_state()
        self._refresh_preview()

    def _update_page_nav_state(self) -> None:
        if self._page_label is not None:
            self._page_label.configure(text=self._page_label_text())
        if self._prev_page_button is not None:
            self._prev_page_button.configure(
                state=tk.NORMAL if self._page_index > 0 else tk.DISABLED
            )
        if self._next_page_button is not None:
            self._next_page_button.configure(
                state=tk.NORMAL if self._page_index < self._page_count - 1 else tk.DISABLED
            )

    def _change_page(self, delta: int) -> None:
        new_index = self._page_index + delta
        if not (0 <= new_index < self._page_count):
            return
        self._page_index = new_index
        self._update_page_nav_state()
        self._refresh_preview()

    def _current_page_rotation(self) -> int:
        """向き補正チェックがONで、表示中のページが向き不一致ページの場合の
        プレビュー回転角（時計回り）。それ以外は0（回転しない）。

        書き出し時の回転は不一致ページのみに適用されるため（output_writer
        参照）、プレビューも同じ条件でのみ回転させて見た目を一致させる。
        （向き補正は単独ファイルのみに提供されるため、結合グループでは
        rotateキー自体が存在せず常に0になる。）
        """
        rotate_checked = (
            self._choice_vars["rotate"].get() if "rotate" in self._choice_vars else False
        )
        if not rotate_checked:
            return 0
        pf = self._current_record().pdf_finding
        if pf is None or (self._page_index + 1) not in pf.orientation_mismatch_pages:
            return 0
        return self._rotate_degrees_var.get()

    def _refresh_preview(self) -> None:
        stamp_checked = self._choice_vars["stamp"].get() if "stamp" in self._choice_vars else False
        rotation = self._current_page_rotation()
        record = self._current_record()
        stamp_label = self._current_stamp_label()

        if stamp_checked and stamp_label is not None:
            image = render_preview_with_stamp(
                record,
                stamp_label,
                self._effective_stamp_config(),
                page_index=self._page_index,
                rotate_degrees=rotation,
            )
        else:
            image = render_preview(record, page_index=self._page_index, rotate_degrees=rotation)

        if image is not None:
            self._preview_photo = ImageTk.PhotoImage(image)
            self._preview_label.configure(image=self._preview_photo, text="")
        else:
            self._preview_photo = None
            self._preview_label.configure(
                image="", text=_preview_placeholder_text(record)
            )

    def _on_confirm(self) -> None:
        choices = {key: var.get() for key, var in self._choice_vars.items()}
        stamp_offset_mm = (self._stamp_margin_top_mm, self._stamp_margin_right_mm)
        self.result = apply_review_choices(
            self._item,
            choices,
            self._filename_config,
            stamp_offset_mm=stamp_offset_mm,
            rotate_degrees=self._rotate_degrees_var.get(),
        )
        self.destroy()

    def _on_cancel(self) -> None:
        # この1件だけをスキップする。abort_allはFalseのまま＝呼び出し側は
        # 次の対象の逐次レビューへ進む。
        self.result = None
        self.destroy()

    def _on_abort(self) -> None:
        # ウィンドウを閉じる操作（×ボタン）専用。残り全件を打ち切る。
        self.result = None
        self.abort_all = True
        self.destroy()


def review_and_confirm(
    parent: tk.Misc,
    item: ReviewItem,
    index: int,
    total: int,
    filename_config: FilenameConfig,
    evidence_stamp_config: EvidenceStampConfig,
) -> tuple[PlannedCopy | None, bool]:
    """逐次レビュー画面を表示し、(確定したPlannedCopy又はNone, 残り全件を中断するか) を返す。

    - 「この内容で書き出す」：(PlannedCopy, False)
    - 「キャンセル」（この1件をスキップ）：(None, False)。呼び出し側は次の
      対象の逐次レビューへ進むこと。
    - ウィンドウを閉じる操作（残り全件を中断）：(None, True)。呼び出し側は
      以降の処理を打ち切ること。
    """
    dialog = WriteReviewDialog(parent, item, index, total, filename_config, evidence_stamp_config)
    parent.wait_window(dialog)
    return dialog.result, dialog.abort_all
