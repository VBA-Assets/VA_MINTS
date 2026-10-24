"""VA mints メインウィンドウ（tkinter）。

要件定義書4.1「フォルダ選択・スキャン機能」・4.2「ファイル状態把握機能」
（4.6「墨消し完全性の検証」を含む）・4.3「出力ファイル名入力機能」・
4.5「適合化処理」・4.7「証拠番号処理」・4.8「基本レポート出力機能」及び出力
フォルダへの書き出し（第3章処理フロー⑥）を対象とする。

4.3の範囲は「ファイル名が証拠番号パターンに合致するかどうかの判定と、
4.7（証拠番号処理）の適用候補への反映」にとどめる。ファイル名の内容が
適切かどうかの判断（標目の付け方等）は利用者があらかじめ（エクスプローラー
等で）行っている前提とし、ツール側での編集・自動採番機能は持たない。ファイル名が
技術的にmintsの制約に反する場合（100文字超過・禁止文字）のみ、4.2の検出結果
としてB（要修正）に反映する。

書き出し対象はA（適合済み）・B（要修正）のうち記録対応形式のみ（C＝要変換は
形式変換機能〔4.4〕がチェック専念方針により廃止のため対象外、D＝処理対象外も
対象外）。ファイル名整形・メタデータ除去・
証拠番号スタンプ・枝番ファイル結合はいずれもファイルの内容・体裁を変更する処理のため、
CLAUDE.md「絶対に譲れない設計原則」3に従い、実行前に必ずユーザーの確認を得る。
その具体的なUI実装として、書き出し対象を1件（又は1枝番ファイル結合グループ）ずつ
処理する逐次レビュー画面（ui/write_review_dialog.py）を用いる。各回、対象の
プレビュー（原本に証拠番号が既に記載されていないかを目視確認できるように）と、
適用する処理を選ぶチェックボックス（ファイルの状態に応じた既定値つき、手動で
変更可）を表示し、確定した内容をその場で書き出す。枝番ファイル結合のみ複数ファイルに
またがる決定のため、対象一覧をグループ単位のチェックボックスでまとめて確認する
別画面（ui/checklist_dialog.py）を経る。
"""

from __future__ import annotations

import dataclasses
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from mints_prep.config import Config, FilenameConfig, load_config
from mints_prep.models import Classification, FileRecord, ScanResult
from mints_prep.naming.evidence_number import match_evidence_number
from mints_prep.processors.batch_planner import (
    Batch,
    find_batch_conflicts,
    plan_batches,
    resolve_batch_dir,
)
from mints_prep.processors.evidence_merger import BranchGroup, find_branch_merge_groups
from mints_prep.processors.filename_sanitizer import needs_sanitization, sanitize_filename
from mints_prep.processors.output_writer import (
    PlannedCopy,
    copy_to_output,
    make_deduped_name,
    plan_output_filenames,
    select_eligible_records,
)
from mints_prep.processors.report_writer import (
    build_report_text,
    format_evidence_sequence_warnings,
    write_report,
)
from mints_prep.scanner.walker import scan_folder
from mints_prep.ui.checklist_dialog import ChecklistItem, ask_checklist
from mints_prep.ui import platform_support
from mints_prep.ui.formatting import (
    CLASSIFICATION_LABELS,
    describe_expected_processing,
    format_size_display,
)
from mints_prep.ui.review_logic import ReviewItem
from mints_prep.ui.theme import apply_theme
from mints_prep.ui.wrapping_table import ColumnSpec, WrappingTable
from mints_prep.ui.write_review_dialog import _grab_when_viewable, review_and_confirm

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

# 証拠番号スタンプ（4.7）の対象形式。音声・動画には「ページ」の概念がなく
# 焼き込み対象にならないため対象外とする。
_STAMPABLE_FORMATS = {"PDF", "JPEG", "PNG"}

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


# 結果一覧の列構成。Tkのインスタンス化を伴わずに単体テストできるよう、
# モジュールレベルの定数として切り出す。
RESULT_TABLE_COLUMNS = [
    ColumnSpec("filename", "ファイル名", 220),
    ColumnSpec("classification", "分類", 90),
    ColumnSpec("format", "検出形式", 80),
    ColumnSpec("size", "サイズ", 80),
    ColumnSpec("processing", "検出事項", 400),
    ColumnSpec("notes", "備考", 350),
]


class MainWindow(tk.Tk):
    def __init__(self, config: Config | None = None) -> None:
        super().__init__()
        self.config_obj = config or load_config()
        # 画面全体のテーマ（フラットデザイン・Microsoft系ダイアログの質感）。
        # ウィジェット生成前に一度だけ適用する（詳細はui/theme.py参照）。
        apply_theme(self)
        # ウィンドウタイトルに、ツール名に続けてサブタイトル（このツールが
        # 何であるか）を「ー」区切りで併記する（画面内のサブタイトル行は
        # 廃止し、タイトルバーに集約）。
        self.title("VA mints ー mints（民事裁判書類電子提出システム）「記録」提出前チェック・準備ツール")
        # タイトルバー・タスクバーのアイコン（実行ファイル自体のアイコンは
        # mints_prep.specのEXE(icon=...)で別途埋め込まれる）。アイコンが
        # 見つからない・読めない場合は既定のままにする（起動を妨げない）。
        icon_path = _window_icon_path()
        if icon_path.exists():
            try:
                self.iconbitmap(str(icon_path))
            except tk.TclError:
                pass
        self.geometry("1300x700")
        self.minsize(900, 500)

        self._input_dir_var = tk.StringVar(value="")
        self._output_dir_var = tk.StringVar(value="")
        # 要件定義書4.1「入力フォルダ選択」：サブフォルダを含めるかはオプション。
        # 意図せず対象外の書類（他事件のファイル等）まで読み込む事故を避ける
        # ため、既定はOFF（トップレベルのみ）とする。
        self._include_subfolders_var = tk.BooleanVar(value=False)
        self._scan_result: ScanResult | None = None
        self._queue: "queue.Queue[object]" = queue.Queue()

        self._build_widgets()

    # フォルダパスはテキストボックス（手動入力可）と連動させるため、常に
    # StringVarから読み取る（ボタンでの選択と手動入力の両方を単一の入力源にする）。
    @property
    def _input_dir(self) -> Path | None:
        text = self._input_dir_var.get().strip()
        return Path(text) if text else None

    @property
    def _output_dir(self) -> Path | None:
        text = self._output_dir_var.get().strip()
        return Path(text) if text else None

    # ------------------------------------------------------------------ UI構築
    def _build_widgets(self) -> None:
        top = ttk.Frame(self, padding=8)
        top.pack(side=tk.TOP, fill=tk.X)

        # フォルダパス欄は、これまで画面全幅まで伸びて見づらかったため、
        # 文字数指定の固定幅（画面全幅の半分程度）にとどめる。
        _FOLDER_ENTRY_WIDTH = 60

        ttk.Label(top, text="入力フォルダ：").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Entry(top, textvariable=self._input_dir_var, width=_FOLDER_ENTRY_WIDTH).grid(
            row=0, column=1, sticky="w", padx=(4, 4), pady=2
        )
        # フォルダ選択ボタンは、フォルダ参照ダイアログを開く操作であることが
        # 隣接するテキスト欄から自明なため、Windows標準の「参照」ダイアログの
        # 慣行に倣い「...」の省略表記にとどめる。
        ttk.Button(top, text="...", width=3, command=self._choose_input_dir).grid(
            row=0, column=2, padx=(0, 4)
        )
        # ボタン名は「スキャン」を避ける（実物のスキャナー〔ScanSnap等〕による
        # 読み取り操作と紛らわしいため）。要件定義書自体はこの処理を「スキャン」と
        # 呼ぶが、これはツール内部の機能名であり、画面表示は「フォルダの中身を
        # ツールに取り込む」動作として利用者に自然な「読み込み」に統一する。
        self._scan_button = ttk.Button(
            top, text="ファイルを読み込み", command=self._start_scan, style="Emphasis.TButton"
        )
        self._scan_button.grid(row=0, column=3, padx=(4, 0))

        # 'clam'テーマのttk.Checkbuttonはチェック時のインジケータが「✕」
        # 形状に描画され、選択済みではなく利用不可のように見えるため、
        # 標準tk.Checkbuttonを使う（write_review_dialogと同方針）。
        tk.Checkbutton(
            top,
            text="サブフォルダ内のファイルも含める（既定は入力フォルダ直下のみ）",
            variable=self._include_subfolders_var,
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(0, 6))

        ttk.Label(top, text="").grid(row=2, column=0, columnspan=4)

        ttk.Label(top, text="出力フォルダ：").grid(row=3, column=0, sticky="w", pady=2)
        ttk.Entry(top, textvariable=self._output_dir_var, width=_FOLDER_ENTRY_WIDTH).grid(
            row=3, column=1, sticky="w", padx=(4, 4), pady=2
        )
        ttk.Button(top, text="...", width=3, command=self._choose_output_dir).grid(
            row=3, column=2, padx=(0, 4)
        )
        self._write_button = ttk.Button(
            top, text="ファイルを書き出し", command=self._start_output_write, style="Emphasis.TButton"
        )
        self._write_button.grid(row=3, column=3, padx=(4, 0))

        ttk.Label(
            top,
            text="※ 書き出し対象はA（適合済み）・B（要修正）のみ（C＝要変換／D＝対象外は対象外）です",
            foreground="#666666",
        ).grid(row=4, column=0, columnspan=4, sticky="w", pady=(0, 6))

        # ttk.Treeviewはセル内テキストの折り返し表示に対応していない（列幅を
        # 超える文章は単純に切り詰められる）ため、Frame/Labelベースの自作表
        # （WrappingTable）を使う。列は固定幅とする（ウィンドウ幅に応じて
        # 伸縮する列を作ると、伸縮後の実際の幅とLabelのwraplengthがずれて
        # 余白ばかり広がる見た目になるため）。
        self._table = WrappingTable(
            self, RESULT_TABLE_COLUMNS, tag_colors=_TAG_COLORS, with_checkbox=True
        )
        self._table.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=8)

        # ステータス行：画面の最下部に、見出しなしで配置する（「状況：」の
        # 見出し付き表示は見た目がしっくりこないとのフィードバックにより撤回）。
        # フラットデザイン方針のため立体的な枠線（sunken）は使わず、上側に
        # 細い区切り線（Separator）を1本引くだけの最下部バーとする。
        # side=BOTTOMは先にpackした要素ほど画面の下端に近づくため、
        # ステータス行→区切り線→サマリ行の順にpackする。
        self._status_label = ttk.Label(self, text="", anchor="w", padding=(8, 4))
        self._status_label.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Separator(self, orient="horizontal").pack(side=tk.BOTTOM, fill=tk.X)

        self._summary_label = ttk.Label(self, text="", padding=8, anchor="w", justify="left")
        self._summary_label.pack(side=tk.BOTTOM, fill=tk.X)

    # ------------------------------------------------------------------ フォルダ選択
    def _choose_input_dir(self) -> None:
        selected = filedialog.askdirectory(title="入力フォルダを選択", mustexist=True)
        if selected:
            self._input_dir_var.set(selected)

    def _choose_output_dir(self) -> None:
        selected = filedialog.askdirectory(title="出力フォルダを選択", mustexist=True)
        if not selected:
            return
        candidate = Path(selected)
        if self._input_dir is not None and candidate == self._input_dir:
            # 要件定義書4.1「出力フォルダ選択」：入力フォルダと同一指定も許容するが、
            # 上書き確認を必須とする。
            proceed = messagebox.askyesno(
                "確認",
                "出力フォルダが入力フォルダと同一です。\n"
                "処理結果の書き出し時に元ファイルを上書きする可能性があります。続行しますか？",
            )
            if not proceed:
                return
        self._output_dir_var.set(selected)

    # ------------------------------------------------------------------ 読み込み実行
    def _start_scan(self) -> None:
        input_dir = self._input_dir
        if input_dir is None:
            messagebox.showerror("エラー", "入力フォルダを選択してください。")
            return
        if not input_dir.is_dir():
            messagebox.showerror("エラー", f"入力フォルダが見つかりません：{input_dir}")
            return

        self._scan_button.config(state=tk.DISABLED)
        self._status_label.config(text="読み込み中...")
        self._table.clear()

        # input_dir・サブフォルダ設定はメインスレッドで確定させ、ワーカー
        # スレッドには値として渡す（別スレッドから直接Tkの変数を読み取らない）。
        include_subfolders = self._include_subfolders_var.get()
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
            # 前回の読み込み結果を破棄する（残したままだと、テーブルは空なのに
            # 「ファイルを書き出し」が前回結果に基づく紛らわしい動きをする）。
            self._scan_result = None
            self._summary_label.config(text="")
            self._status_label.config(text="読み込みに失敗しました。")
            messagebox.showerror("エラー", f"読み込み中にエラーが発生しました：\n{item}")
            return

        self._scan_result = item
        self._populate_table(item)

    # ------------------------------------------------------------------ 結果表示
    def _populate_table(self, result: ScanResult) -> None:
        counts = {c: 0 for c in Classification}
        for record in result.records:
            counts[record.classification] += 1
            evidence_match = match_evidence_number(
                record.path.name, self.config_obj.evidence_number
            )
            # 「証拠番号処理の適用候補」表示は、実際に逐次レビューでスタンプ・
            # 結合の選択肢が提示されるファイルに限る。MP3等の非対象形式や
            # パスワード付きPDFにまで候補表示すると、一覧では「適用候補」なのに
            # レビュー画面では選択肢が出ない、という不整合になる
            # （_build_plan_and_batchesのスタンプ対象判定と同じ条件）。
            if evidence_match is not None:
                stampable = record.detected_format in _STAMPABLE_FORMATS and not (
                    record.pdf_finding is not None and record.pdf_finding.has_password
                )
                if not stampable:
                    evidence_match = None
            # 対象選択チェックボックス（4.1節）：書き出し対象になり得るのは
            # A・B分類のみ（C・Dはselect_eligible_recordsで常に除外される）
            # のため、C・Dの行はチェックOFF・操作不可（グレーアウト）で表示
            # し、選択の余地がないことを視覚的に示す。
            selectable = record.classification in (
                Classification.A_COMPLIANT,
                Classification.B_NEEDS_FIX,
            )
            self._table.add_row(
                (
                    record.path.name,
                    CLASSIFICATION_LABELS[record.classification],
                    record.detected_format,
                    format_size_display(record.size_bytes),
                    describe_expected_processing(record, evidence_match),
                    "; ".join(record.notes),
                ),
                tag=_CLASSIFICATION_TAGS[record.classification],
                checked=selectable,
                checkable=selectable,
            )

        total = len(result.records)
        summary = (
            f"合計 {total} 件　|　"
            f"A：適合済み {counts[Classification.A_COMPLIANT]} 件　"
            f"B：要修正 {counts[Classification.B_NEEDS_FIX]} 件　"
            f"C：要変換 {counts[Classification.C_NEEDS_CONVERSION]} 件　"
            f"D：対象外 {counts[Classification.D_UNSUPPORTED]} 件"
        )
        self._summary_label.config(text=summary)
        self._status_label.config(text="読み込み完了")

    def _build_plan_and_batches(
        self, records: list[FileRecord], merge_groups: list[BranchGroup]
    ) -> tuple[list[PlannedCopy], list[Batch]]:
        """既定推奨（自動検出結果をすべて適用したと仮定）の書き出し計画と
        バッチ割当てを構築する。

        merge_groupsに含まれるメンバーは結合対象として除外し、残りを個別に
        扱う。上書き警告を最優先で表示するための事前計画（結合候補すべてを
        仮に結合したもの）と、枝番結合確認後の本番計画（確定した結合グループ
        のみ）の両方をこの関数で組み立てる（重複を避けるための共通化）。
        """
        merged_member_paths = {m.record.path for group in merge_groups for m in group.members}
        remaining = [r for r in records if r.path not in merged_member_paths]

        sanitized_names: dict[Path, str] = {
            r.path: sanitize_filename(r.path.name, self.config_obj.filename)
            for r in remaining
            if needs_sanitization(r.filename_finding)
        }
        strip_metadata_paths: set[Path] = {
            r.path
            for r in remaining
            if r.metadata_finding is not None and r.metadata_finding.has_metadata
        }
        stamp_labels: dict[Path, str] = {}
        for r in remaining:
            if r.detected_format not in _STAMPABLE_FORMATS:
                continue
            if r.pdf_finding is not None and r.pdf_finding.has_password:
                # パスワード付きPDFは復号キーがないと内容を読めず、スタンプ
                # 処理自体が実行できない（先にパスワード解除が必要）。
                continue
            match = match_evidence_number(r.path.name, self.config_obj.evidence_number)
            if match is not None:
                stamp_labels[r.path] = match.evidence_number_label

        planned = plan_output_filenames(
            remaining,
            sanitized_names,
            strip_metadata_paths,
            stamp_labels=stamp_labels,
            merge_groups=merge_groups,
            stamp_merge_sources=True,
            filename_config=self.config_obj.filename,
        )

        # 要件定義書4.5「200MB分割」：1回のアップロード合計上限（200MB）を
        # 超える場合に備え、提出回ごとのバッチへ仕分ける。ファイル自体を
        # 変更しない処理のため確認UIの対象外。バッチの割当ては、後続の逐次
        # レビューでの個別選択（整形の有無等）とは独立に決定してよい
        # （割当ての基準は証拠番号のまとまりと合計サイズであり、ファイル名の
        # 整形有無では変わらないため）。
        batches = plan_batches(planned, self.config_obj.upload, self.config_obj.evidence_number)
        return planned, batches

    # ------------------------------------------------------------------ 出力フォルダへの書き出し
    def _start_output_write(self) -> None:
        if self._scan_result is None:
            messagebox.showerror("エラー", "先にフォルダを読み込んでください。")
            return
        output_dir = self._output_dir
        if output_dir is None:
            messagebox.showerror("エラー", "出力フォルダを選択してください。")
            return
        if not output_dir.is_dir():
            messagebox.showerror("エラー", f"出力フォルダが見つかりません：{output_dir}")
            return

        all_eligible = select_eligible_records(self._scan_result.records)
        if not all_eligible:
            messagebox.showinfo("書き出し", "書き出し対象のファイル（A・B分類）がありません。")
            return

        # スキャン結果一覧の対象選択チェックボックス（4.1節）で対象から外された
        # ファイルは、この回の書き出しから除外する。外部アプリで一部のファイル
        # だけを修正して再処理したい場合に、フォルダ全体を毎回やり直さずに
        # 済むようにするための機能。行の並び順（get_checked_states）は
        # _populate_tableでのadd_row呼び出し順＝self._scan_result.recordsの
        # 順序と一致する前提。
        checked_states = self._table.get_checked_states()
        checked_paths = {
            record.path
            for record, checked in zip(self._scan_result.records, checked_states)
            if checked
        }
        eligible = [r for r in all_eligible if r.path in checked_paths]
        if not eligible:
            messagebox.showinfo(
                "書き出し",
                "書き出し対象として選択されているファイルがありません。"
                "一覧左端のチェックボックスをご確認ください。",
            )
            return

        # 要件定義書4.7「枝番ファイル結合」の候補検出（判定のみ、確認はまだ
        # 行わない）。上書き警告を最優先で表示するための事前計画づくりに使う。
        merge_groups = find_branch_merge_groups(eligible, self.config_obj.evidence_number)

        # 出力フォルダに既に同名ファイルがある場合の上書き警告を、他の確認・
        # 警告より先に最優先で表示する。ユーザーが以降の枝番結合確認・
        # ファイル別逐次レビューに時間をかける前に、続行するかどうかを
        # 早期に判断できるようにするため。結合候補は（実際の確認前だが）
        # すべて結合されるものと仮定した既定計画で判定する。レビューでの
        # 選択変更（整形の辞退・結合の辞退等）により最終ファイル名がこの
        # 事前判定と変わる場合は、書き出し直前に確定名で再チェックする
        # （後述の逐次ループ内）。
        _, preview_batches = self._build_plan_and_batches(eligible, merge_groups)
        conflicts = find_batch_conflicts(preview_batches, output_dir)
        if conflicts:
            conflict_list = "\n".join(f"・{name}" for name in conflicts[:20])
            if len(conflicts) > 20:
                conflict_list += f"\n...ほか{len(conflicts) - 20}件"
            proceed = messagebox.askyesno(
                "上書きの確認",
                f"出力フォルダには同名のファイルが既に{len(conflicts)}件あります。"
                "続行すると、これらのファイルは上書きされます。\n\n"
                f"{conflict_list}\n\n続行しますか？",
            )
            if not proceed:
                return

        # 要件定義書4.5「単体ファイル200MB超過の検出・警告」：合計サイズの
        # 200MB分割では解決できない、圧縮・分割（チェック専念方針により
        # 実行機能は廃止）なしには原理的に提出不可能な重大警告のため、
        # 書き出し実行の直前に必ず明示する（検出・警告にとどめ、処理は
        # ブロックしない）。
        oversized = [r for r in eligible if r.exceeds_single_file_limit]
        if oversized:
            limit_mb = f"{self.config_obj.upload.max_total_mb:g}MB"
            oversized_list = "\n".join(
                f"・{r.path.name}（{r.size_mb:.1f}MB）" for r in oversized[:20]
            )
            if len(oversized) > 20:
                oversized_list += f"\n...ほか{len(oversized) - 20}件"
            messagebox.showwarning(
                f"単体で{limit_mb}を超えるファイルがあります",
                f"以下の{len(oversized)}件は単体で{limit_mb}を超えており、圧縮又は分割を"
                "行わない限りmintsへ提出できません。\n\n"
                f"{oversized_list}\n\n"
                "このツールは検出・警告のみを行い、圧縮・分割の実行機能はまだ"
                "提供していません。書き出し自体は続行しますが、これらのファイルは"
                "別途対処が必要です。",
            )

        # 要件定義書4.7「枝番ファイル結合」：複数ファイルにまたがる決定のため、以降の
        # 1件単位の逐次レビュー画面にはなじまず、候補一覧をグループ単位の
        # チェックボックスでまとめて提示する（CLAUDE.md「絶対に譲れない設計
        # 原則」3「チェックボックスで選択」）。結合対象となったファイルは、
        # 以降の個別処理（ファイル名整形・単独スタンプ等）の対象から除外する。
        confirmed_merge_groups: list[BranchGroup] = []
        if merge_groups:
            checklist_items = [
                ChecklistItem(
                    key=str(i),
                    before_text="、".join(m.record.path.name for m in group.members),
                    after_text=f"→ {group.output_filename} へ結合（{len(group.members)}件）",
                    default_checked=True,
                )
                for i, group in enumerate(merge_groups)
            ]
            choices = ask_checklist(
                self,
                "枝番ファイル結合の確認",
                "以下のファイル群は、同一の証拠番号（枝番違い）と判定されました。"
                "準備の手引の規定により、枝番のある証拠は1つのファイルにまとめて"
                "提出することが想定されています。結合するグループのチェックを"
                "入れたまま実行してください（外すと、そのグループは結合せず"
                "個別のまま書き出します）。",
                checklist_items,
            )
            if choices:
                confirmed_merge_groups = [
                    group for i, group in enumerate(merge_groups) if choices.get(str(i), False)
                ]

        # 要件定義書4.5「200MB分割」：確定した結合グループを反映し、実際に
        # 書き出す計画・バッチ割当てを構築する。
        planned, batches = self._build_plan_and_batches(eligible, confirmed_merge_groups)

        # 要件定義書4.7「号証の連番・欠番チェック」：複数ファイルにまたがる
        # 検出結果のため、1件ずつの逐次レビュー画面（下記ループ）には自然に
        # 収まらない。対象ファイル確定時点（この時点のplanned）の出力ファイル名
        # から算出し、逐次レビューが始まる前に一度だけ表示する。個別のレビューで
        # チェックを外して未対処にできる項目とは異なり、この結果自体を本ツールが
        # 直接解消する手段はないため、確認のみ（実行可否の選択は伴わない）。
        sequence_lines = format_evidence_sequence_warnings(
            (item.output_filename for item in planned), self.config_obj.evidence_number
        )
        if sequence_lines:
            shown = "\n".join(sequence_lines[:20])
            if len(sequence_lines) > 20:
                shown += f"\n...ほか{len(sequence_lines) - 20}件"
            messagebox.showwarning(
                "号証の連番・欠番チェック",
                "号証の連番・欠番の疑いがあります。提出前にご確認ください。\n\n" + shown,
            )

        self._write_button.config(state=tk.DISABLED)
        self._status_label.config(text="書き出し中...")
        self.update_idletasks()

        # 事前の上書き警告で承認済みの衝突名（バッチフォルダ相対）。レビューでの
        # 選択変更により最終名がこれと異なる場合、書き出し直前に個別再確認する。
        preconfirmed_conflicts = set(conflicts)

        # 要件定義書4.5・4.7「ファイル別・逐次レビュー」：バッチ内の並び順の
        # とおり、1件（又は1枝番ファイル結合グループ）ずつプレビュー付きの確認画面を
        # 表示し、確定した内容を即座に書き出す。モーダルダイアログと1件ずつの
        # 書き込みが交互に発生するため、バックグラウンドスレッドは使わず
        # メインスレッドで同期的に処理する。すべての検出結果（本ツールで対処
        # できないものを含む）はこのレビュー画面自体に表示されるため、書き出し
        # 完了後にまとめて警告する画面は設けない（実行前に必ず目に入る一本化）。
        # 1件の書き込み失敗（他プロセスによるロック・破損PDF等）で全体が
        # 停止しUIが操作不能のまま残らないよう、書き込みは1件ごとに例外を
        # 捕捉し、finallyでボタンの復帰を保証する。
        actual_planned: list[PlannedCopy] = []
        report_batches: dict[int, list[PlannedCopy]] = {}
        skipped_descriptions: list[str] = []
        seen_names: dict[str, int] = {}
        total_items = sum(len(batch.items) for batch in batches)
        processed = 0
        aborted = False

        try:
            for batch in batches:
                batch_dir = resolve_batch_dir(batches, batch, output_dir)
                for prelim_item in batch.items:
                    processed += 1
                    preview_record = (
                        prelim_item.merge_group.representative.record
                        if prelim_item.merge_group is not None
                        else prelim_item.record
                    )
                    review_item = ReviewItem(
                        preliminary=prelim_item, preview_record=preview_record, batch_dir=batch_dir
                    )
                    final_item, abort_all = review_and_confirm(
                        self,
                        review_item,
                        processed,
                        total_items,
                        self.config_obj.filename,
                        self.config_obj.evidence_stamp,
                    )
                    if abort_all:
                        aborted = True
                        break
                    if final_item is None:
                        # 「キャンセル」：この1件だけをスキップし、次の対象へ進む。
                        # スキップ理由も併記する（上書き辞退・書き出しエラーの
                        # スキップと完了メッセージ・レポート上で区別するため）。
                        name = (
                            prelim_item.output_filename
                            if prelim_item.merge_group is None
                            else "、".join(
                                m.record.path.name for m in prelim_item.merge_group.members
                            )
                        )
                        skipped_descriptions.append(f"{name}（レビューでキャンセル）")
                        continue

                    final_name = self._dedupe_output_filename(
                        final_item.output_filename, seen_names, self.config_obj.filename
                    )
                    if final_name != final_item.output_filename:
                        final_item = dataclasses.replace(
                            final_item, output_filename=final_name, renamed_for_collision=True
                        )

                    # 上書きの再確認：事前警告は「全結合・全整形適用」仮定の
                    # 名前で行っているため、レビューで整形・結合を辞退した
                    # 場合の最終名は未確認のまま既存ファイルを上書きし得る。
                    # 確定名が事前承認済みの衝突名と異なり、かつ出力先に既に
                    # 存在する場合のみ、個別に確認する。
                    rel_name = (
                        f"{batch.folder_name}/{final_name}" if len(batches) > 1 else final_name
                    )
                    if rel_name not in preconfirmed_conflicts and (batch_dir / final_name).exists():
                        if not messagebox.askyesno(
                            "上書きの確認",
                            f"出力先に同名のファイルが既にあります：\n{rel_name}\n\n"
                            "上書きしますか？（「いいえ」でこの1件をスキップします）",
                        ):
                            skipped_descriptions.append(f"{final_name}（上書きを辞退）")
                            continue

                    self._status_label.config(
                        text=f"書き出し中：{processed} / {total_items} 件目..."
                    )
                    self.update_idletasks()
                    try:
                        # サブフォルダ（提出用_NN）は実際に書き出す時点で作る
                        # （全件キャンセルされたバッチの空フォルダを残さない）。
                        batch_dir.mkdir(parents=True, exist_ok=True)
                        copy_to_output([final_item], batch_dir, self.config_obj.evidence_stamp)
                    except Exception as exc:  # noqa: BLE001 - 1件の失敗を全体停止にしない
                        proceed = messagebox.askyesno(
                            "書き出しエラー",
                            f"「{final_name}」の書き出しに失敗しました：\n{exc}\n\n"
                            "（出力先のファイルを他のアプリで開いている場合は閉じてから"
                            "やり直してください）\n\n"
                            "この1件をスキップして続行しますか？"
                            "（「いいえ」で残りの書き出しを中止します）",
                        )
                        if proceed:
                            skipped_descriptions.append(f"{final_name}（書き出しエラー：{exc}）")
                            continue
                        aborted = True
                        break

                    actual_planned.append(final_item)
                    report_batches.setdefault(batch.index, []).append(final_item)
                if aborted:
                    break
        except Exception as exc:  # noqa: BLE001 - 想定外の失敗でもUIを操作可能なまま残す
            messagebox.showerror(
                "書き出し処理でエラーが発生しました",
                f"想定外のエラーのため書き出しを中断しました：\n{exc}\n\n"
                "ここまでに書き出し済みのファイルはそのまま残っています。",
            )
            aborted = True
        finally:
            self._write_button.config(state=tk.NORMAL)

        # 要件定義書4.8「基本レポート出力機能」：処理結果・警告一覧・バッチ構成・
        # 未対応形式一覧を出力フォルダ直下にテキストで生成する。集計・整形の
        # みの読み取り専用処理のため確認UIの対象外（自動生成）。中止された
        # 場合も、実際に処理済みの範囲でレポートを生成する。
        report_batches_list = [
            Batch(index=idx, items=tuple(items)) for idx, items in sorted(report_batches.items())
        ]
        try:
            report_text = build_report_text(
                input_dir=self._input_dir,
                output_dir=output_dir,
                all_records=list(self._scan_result.records),
                planned=actual_planned,
                batches=report_batches_list,
                evidence_number_config=self.config_obj.evidence_number,
                skipped_items=skipped_descriptions,
            )
            report_path = write_report(report_text, output_dir)
            report_note = f"処理内容の詳細は「{report_path.name}」をご確認ください。"
        except Exception as exc:  # noqa: BLE001 - レポート保存失敗を完了通知の妨げにしない
            report_note = f"（レポートの保存に失敗しました：{exc}）"

        total_written = len(actual_planned)
        total_skipped = len(skipped_descriptions)
        batch_count = len(report_batches_list)

        # スキップ（レビューでのキャンセル・上書き辞退・書き出しエラー）で、
        # 書き出し前の事前チェック時にはなかった欠番が新たに生じた場合のみ、
        # 追加で警告する（事前ポップアップは全件書き出し前提の判定のため、
        # スキップ起因の欠番はレポートを開かない限り気づけないままになる）。
        if total_skipped and actual_planned:
            post_sequence_lines = format_evidence_sequence_warnings(
                (item.output_filename for item in actual_planned),
                self.config_obj.evidence_number,
            )
            new_sequence_lines = [l for l in post_sequence_lines if l not in sequence_lines]
            if new_sequence_lines:
                messagebox.showwarning(
                    "スキップにより欠番が生じています",
                    "スキップしたファイルの分、実際の書き出し結果に新たな欠番の"
                    "疑いが生じています。\n\n" + "\n".join(new_sequence_lines[:20]),
                )

        # すべての検出結果（本ツールで対処できないものを含む）は、逐次レビュー
        # 画面（1件ずつ必ず目に入る）にすべて表示済みのため、書き出し完了後に
        # まとめて警告する画面は設けない。号証の連番・欠番チェックのみ、対象
        # ファイル確定時点で書き出し開始前に一度表示済み（複数ファイルにまたがる
        # 検出結果のため）。

        if aborted:
            self._status_label.config(
                text=(
                    f"書き出しを中止しました（{total_written} 件書き出し・"
                    f"{total_skipped} 件スキップ／全 {total_items} 件）"
                )
            )
            skip_note = f"（{total_skipped} 件はスキップ。内訳はレポート参照）" if total_skipped else ""
            self._show_completion_dialog(
                "書き出しを中止しました",
                f"{total_written} 件を書き出した時点で中止しました{skip_note}。\n\n"
                f"{report_note}",
                output_dir=output_dir,
                warning=True,
            )
            return

        if total_written == 0:
            # 全件キャンセル：「書き出し完了（0 件・0 回分）」という不自然な
            # 完了表示を避け、何も書き出していないことを明示する。
            self._status_label.config(text="書き出したファイルはありません（全件スキップ）")
            self._show_completion_dialog(
                "書き出したファイルはありません",
                f"全 {total_items} 件をスキップしたため、ファイルは書き出していません。\n\n"
                f"{report_note}",
                output_dir=output_dir,
                warning=True,
            )
            return

        skip_line = (
            f"\n（{total_skipped} 件はスキップして書き出しませんでした。内訳はレポート参照）"
            if total_skipped
            else ""
        )
        self._status_label.config(text=f"書き出し完了（{total_written} 件・{batch_count} 回分）")

        if batch_count >= 2:
            # 要件定義書4.5「通知の必須化」：分割が発生する場合、確認（実行可否
            # の選択）は不要だが、処理完了時の通知は省略してはならない。
            # フォルダ名は実際に書き出した回（report_batches_list）から取る
            # （キャンセルで先頭バッチが空になった場合、「提出用_01から」という
            # 固定文言は実態と食い違うため）。
            batch_summary = "\n".join(
                f"・{b.folder_name}：{len(b.items)} 件" for b in report_batches_list
            )
            first_folder = report_batches_list[0].folder_name
            limit_mb = f"{self.config_obj.upload.max_total_mb:g}MB"
            self._show_completion_dialog(
                "複数回に分けて提出する必要があります",
                f"合計サイズが{limit_mb}を超えるため、{batch_count}回に分けて提出する"
                f"必要があります。「{first_folder}」から順にご提出ください。"
                f"\n\n{batch_summary}{skip_line}\n\n"
                f"出力先：{self._output_dir}\n"
                "各「提出用_NN」フォルダの中身を、提出回ごとに手作業でmintsに"
                "アップロードしてください。\n\n"
                f"{report_note}",
                output_dir=output_dir,
                warning=True,
            )
        else:
            # 元の計画が複数バッチだった場合、書き出し先は出力フォルダ直下では
            # なく「提出用_NN」サブフォルダの中（resolve_batch_dirは元の計画
            # バッチ数で判定する）。出力先直下を見てファイルがないと誤認され
            # ないよう、その旨を明記する。
            subfolder_note = ""
            if len(batches) >= 2 and report_batches_list:
                subfolder_note = (
                    f"\nファイルは「{report_batches_list[0].folder_name}」フォルダ内に"
                    "書き出されています。"
                )
            self._show_completion_dialog(
                "書き出し完了",
                f"{total_written} 件のファイルを書き出しました。{skip_line}\n"
                f"出力先：{self._output_dir}{subfolder_note}\n\n"
                f"{report_note}",
                output_dir=output_dir,
            )

    def _show_completion_dialog(
        self, title: str, message: str, *, output_dir: Path, warning: bool = False
    ) -> None:
        """書き出し完了・中止時の通知ダイアログ。

        「OK」に加えて「フォルダを開く」ボタンを併置し、別ダイアログで
        「出力フォルダを開きますか？」と聞き直す手間（従来方式）を省く。
        「フォルダを開く」を選ぶと、閉じると同時に出力フォルダを
        エクスプローラーで開く。フォルダを開く操作自体はファイルを変更しない
        ためCLAUDE.mdの確認UI原則（設計原則3）の対象外だが、ユーザーの意図
        しない画面遷移を避けるため、無条件には開かず選択式とする。
        """
        dialog = tk.Toplevel(self)
        dialog.title(title)
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

        ttk.Button(
            button_frame, text="フォルダを開く", command=lambda: _close(True)
        ).pack(side=tk.RIGHT, padx=(6, 0))
        ok_button = ttk.Button(button_frame, text="OK", command=lambda: _close(False))
        ok_button.pack(side=tk.RIGHT)

        dialog.protocol("WM_DELETE_WINDOW", lambda: _close(False))
        dialog.bind("<Return>", lambda _e: _close(False))
        ok_button.focus_set()
        _grab_when_viewable(dialog)
        self.wait_window(dialog)

        if open_folder.get():
            platform_support.open_folder(output_dir)

    @staticmethod
    def _dedupe_output_filename(
        name: str, seen: dict[str, int], filename_config: FilenameConfig | None = None
    ) -> str:
        """出力ファイル名の重複を防ぐ。

        逐次レビューでのチェックボックス選択により、事前計画時に想定していた
        名前と実際の最終ファイル名が変わり得るため（例：ファイル名整形の
        チェックを外し、元の〔重複し得る〕名前に戻す等）、書き出しのたびに
        実際の最終ファイル名を基準に重複チェックする
        （output_writer.make_deduped_nameと同じ命名規則。casefoldキーにより
        Windows〔大文字小文字非区別〕での実質衝突も検出し、filename_configが
        あれば連番付与後も100文字上限に収まるよう幹を再切り詰めする）。
        """
        key = name.casefold()
        if key not in seen:
            seen[key] = 1
            return name
        seen[key] += 1
        candidate = make_deduped_name(name, seen[key], filename_config)
        while candidate.casefold() in seen:
            seen[key] += 1
            candidate = make_deduped_name(name, seen[key], filename_config)
        seen[candidate.casefold()] = 1
        return candidate


def main() -> None:
    try:
        app = MainWindow()
    except Exception as exc:  # noqa: BLE001 - 起動失敗を無言終了にしない
        # config.yamlの検証エラー・不存在等（load_configがYAML構文エラー・
        # キー欠落・型違いも日本語メッセージのValueErrorへ変換する）に加え、
        # 想定外の例外も含めて捕捉する。--windowedのexeでは標準エラー出力が
        # 見えず、未捕捉例外はダイアログなしの無言終了になるため、起動段階の
        # 失敗は必ずダイアログで原因を表示する（利用者がテキストエディタで
        # config.yamlを編集する運用〔CLAUDE.md〕を前提とした導線）。
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "起動エラー",
            "起動に失敗しました。設定ファイル（config.yaml）の内容に問題がある"
            f"可能性があります。\n\n{exc}",
        )
        root.destroy()
        return
    app.mainloop()


if __name__ == "__main__":
    main()
