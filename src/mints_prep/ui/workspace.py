"""3ペインの画面（ui/app.py）の、選択の状態と更新の計画（Ver200）。

画面の見た目（Tkinter）から切り離した純粋なロジックとして実装し、単体テストで
検証できるようにする（ui/review_logic.py と同じ方針）。

持つもの：
- ファイルごとの選択（右ペインの処理の項目のチェック・スタンプ位置・回転方向）。
  既定は、読み込みのときの検出結果から自動で付ける（review_logic.build_review_options）。
- 枝番ファイル結合のグループごとの選択。結合は複数ファイルにまたがる決定なので、
  グループのどのファイルを選んでいても同じ1つのチェック（merge）を見せ、ONの間は
  グループとしての項目（結合後の名前の整形・メタデータ除去・枝番ラベルのスタンプ）、
  OFFの間はファイルごとの項目を見せる。
- 利用者が左ペインで選んでプレビューを見たファイル（更新の確認で、まだ見ていない
  ファイルの数を示すため。要件定義書 第4章「スタンプはプレビューで目視確認」）。

更新の計画（plan_update）は、いまの選択から、何をどこへ更新するかを組み立てる。
実行は processors/updater.py。
"""

from __future__ import annotations

import dataclasses
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from mints_prep.config import Config
from mints_prep.models import FileRecord, ScanResult
from mints_prep.naming.evidence_number import match_evidence_number
from mints_prep.processors.batch_planner import Batch, plan_batches
from mints_prep.processors.evidence_merger import BranchGroup, find_branch_merge_groups
from mints_prep.processors.filename_sanitizer import needs_sanitization, sanitize_filename
from mints_prep.processors.metadata_remover import can_strip_metadata
from mints_prep.processors.output_writer import (
    PlannedCopy,
    _synthetic_merge_record,
    select_eligible_records,
)
from mints_prep.processors.report_writer import format_evidence_sequence_warnings
from mints_prep.processors.pages import PageRef
from mints_prep.processors.pdf_cleanup import clean_pdf
from mints_prep.processors.updater import (
    ACTION_REBUILD,
    ACTION_UNSTAMP,
    APPLIED_COMPRESS,
    APPLIED_FIT_A4,
    APPLIED_JOIN,
    APPLIED_MERGE,
    APPLIED_PAGES,
    APPLIED_REDUCE,
    APPLIED_RENAME,
    APPLIED_SANITIZE,
    APPLIED_SPLIT,
    APPLIED_STAMP,
    APPLIED_STRIP_HIDDEN,
    APPLIED_STRIP_METADATA,
    BACKUP_DIRNAME,
    STAMP_DECLINED,
    Manifest,
    ManifestEntry,
    UpdateOutcome,
    add_file,
    applied_keys,
    build_without_stamp,
    compose_pages,
    discard_unused_materials,
    is_batch_dir_name,
    isolate_shared_pages,
    record_stamp_choice,
    remove_file,
    remove_stamp,
    save_choices,
    update_item,
)
from mints_prep.ui.review_logic import (
    ReviewItem,
    ReviewOption,
    apply_review_choices,
    build_review_options,
)
from mints_prep.ui.selection import Selection
from mints_prep.ui.workspace_compress import CompressMixin
from mints_prep.ui.workspace_description import DescriptionJob, DescriptionMixin
from mints_prep.ui.workspace_files import FilesMixin
from mints_prep.ui.workspace_join_split import JoinSplitMixin
from mints_prep.ui.workspace_names import NamesMixin
from mints_prep.ui.workspace_pages import PagesMixin

# 証拠番号スタンプ（4.7）の対象形式。音声・動画には「ページ」の概念がなく
# 焼き込み対象にならないため対象外とする。
STAMPABLE_FORMATS = {"PDF", "JPEG", "PNG"}

MERGE_KEY = "merge"
# 更新の確認に出す件数のうち、更新記録の処理の名前（APPLIED_*）にないもの。
COUNT_DIVISIONS = "divisions"
COUNT_SPLIT_NEW = "split_new"  # 分割で新しく作るファイルの数
COUNT_ADD = "add"  # 左の一覧へ追加したファイルの数（対象フォルダに写す）
COUNT_REMOVE = "remove"  # 左の一覧から削除したファイルの数（「元のファイル」フォルダへ移す）
CLEANUP_KEYS = ("fit_a4", "strip_hidden")  # 用紙を A4 にそろえる（4.15）・付箋等の除去（4.16）


@dataclass
class GroupSelection:
    group: BranchGroup
    merge: bool
    selection: Selection


@dataclass(frozen=True)
class PaneView:
    """右ペインに出すもの（選んでいるファイル1つ分）。"""

    merge_option: ReviewOption | None  # 枝番ファイル結合の項目（候補のファイルだけ）
    merge_checked: bool
    selection: Selection | None  # いま効いている選択（結合ONならグループ、それ以外はファイル）
    is_group: bool


@dataclass(frozen=True)
class PlannedUpdate:
    item: PlannedCopy
    dest_dir: Path
    keys: tuple[str, ...]  # 行う処理（updater.applied_keys）。空は処理なし
    moves: bool  # 位置（フォルダ）が変わるか
    source_paths: tuple[Path, ...]
    unstamp: bool = False  # 証拠番号スタンプを消すか（updater.remove_stamp）
    # 更新の後、このファイルが「証拠番号スタンプを付けないと決めたファイル」になるか
    # （スタンプの項目があり、チェックが入っていない）。更新記録に覚えさせ、読み込み
    # 直したときに、スタンプへ自動でチェックを付けない（updater.record_stamp_choice）。
    declines_stamp: bool = False
    # 左の一覧へ追加したファイル（Ver300）：item.record.path は元の場所のファイル。更新では、
    # まず対象フォルダに写し（updater.add_file）、写したものに処理をする。
    added: bool = False


@dataclass
class UpdatePlan:
    updates: list[PlannedUpdate] = field(default_factory=list)
    # 左の一覧から削除したファイル（Ver300）。更新で「元のファイル」フォルダへ移す。
    removals: list[Path] = field(default_factory=list)
    batches: list[Batch] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)  # 処理の名前 → 件数
    move_count: int = 0
    unviewed_count: int = 0  # 内容を変えるファイルのうち、まだプレビューを見ていない数
    sequence_warnings: list[str] = field(default_factory=list)
    oversized: list[FileRecord] = field(default_factory=list)
    # 証拠番号スタンプを付けられるのに、チェックが入っていない画像（JPEG・PNG）の数。
    # 画像のスタンプは自動ではチェックを付けない（要件定義書 第4章）ので、全ファイルを
    # まとめて更新する方式では、付け忘れに気づけるよう、確認の画面に示す
    # （2026-11-16 班長決定）。スタンプ済みの画像と、チェックを外したまま一度更新した
    # 画像（付けないと決めたもの）は数えない。
    unstamped_image_count: int = 0
    # 付箋・コメント・添付ファイルが残る PDF の数（取り除く項目があるのに、チェックが入って
    # いないもの。4.16）。自動ではチェックを付けないので、確認の画面に示す。
    hidden_remaining_count: int = 0
    # 証拠説明書の PDF（4.17。入力を保存したことのあるフォルダだけ。作らないときは None）。
    description: DescriptionJob | None = None

    @property
    def changed_count(self) -> int:
        return sum(1 for u in self.updates if u.keys or u.unstamp or u.added) + len(self.removals)

    @property
    def has_work(self) -> bool:
        return self.has_file_work or self.description is not None

    @property
    def has_file_work(self) -> bool:
        """証拠のファイルの更新（証拠説明書の PDF を除く）があるか。"""
        return bool(self.removals) or any(
            u.keys or u.moves or u.unstamp or u.added for u in self.updates
        )


class Workspace(PagesMixin, NamesMixin, JoinSplitMixin, CompressMixin, FilesMixin, DescriptionMixin):
    """3ペインの画面の、選択の状態と更新の計画。

    役目ごとに、次のモジュールに分けてある（状態はすべて、この __init__ が持つ）：
    - workspace_pages.py：ページの下書き・元に戻す・やり直す
    - workspace_names.py：ファイル名の変更・1つのファイルの中の枝番の区切り
    - workspace_join_split.py：ページの挿入・ファイルの結合・分割
    - workspace_compress.py：圧縮の選択・大きさの見込み
    - workspace_files.py：左の一覧のファイルの削除・追加
    - workspace_description.py：証拠説明書の行・入力・PDF（4.17）
    このファイルには、処理の項目の既定・右ペイン・プレビュー・更新の計画を置く。
    """

    def __init__(self, scan_result: ScanResult, config: Config, manifest: Manifest | None = None) -> None:
        self.root = scan_result.root
        self.config = config
        self.records = list(scan_result.records)
        self.manifest = manifest if manifest is not None else Manifest()
        # 証拠説明書の入力（4.17。workspace_description.py。「証拠説明書データ」フォルダの 証拠説明書.json）。
        # このツールで作った証拠説明書の PDF は、一覧で見るだけで、更新（スタンプ・仕分けなど）の
        # 対象にしない（見分けるのに入力を使うので、先に読む）。
        self._init_description()
        # 証拠説明書の PDF は、一覧のいちばん上に置く（200MB で分けるときは「提出用_01」〔一覧の先頭〕に
        # 入れるので、分けないときも先頭にそろえる。2026-11-18 班長指示）。
        pdf = self.description_pdf()
        self.records.sort(key=lambda r: r.path != pdf)  # 並びは、それ以外は変えない（安定な並べ替え）
        self.eligible = [
            r for r in select_eligible_records(self.records) if not self.is_description_file(r)
        ]
        self._eligible_paths = {r.path for r in self.eligible}
        self.viewed: set[Path] = set()
        # スタンプのチェックを外したときのプレビュー用に作った、スタンプのない姿
        # （一時フォルダ。このオブジェクトがなくなると消える）。
        self._unstamped_previews: dict[Path, FileRecord | None] = {}
        self._temp_dirs: list[tempfile.TemporaryDirectory] = []
        # ページの下書き（Ver300。要件定義書 4.0・4.10）：ファイルごとのページの表。
        # 表の各行は、いまのファイルの何ページ目を何度回したものか。編集していない
        # ファイルは持たない（_identity）。
        self._drafts: dict[Path, list[PageRef]] = {}
        self._identities: dict[Path, list[PageRef]] = {}
        # 元に戻す・やり直す：（種類〔"pages"・"name"〕、ファイル、変える前、変えた後）の並び。
        self._undo: list[tuple] = []
        self._redo: list[tuple] = []
        # 1つのファイルの中の枝番の区切り（Ver300。要件定義書 4.11）：ファイルごとに、
        # 新しい枝番が始まるページ（いまのファイルの何ページ目か〔0始まり〕で表す。ページを
        # 並べ替えても、そのページについて動く）。1ページ目は、いつも枝番1の始まり。
        self._divisions: dict[Path, frozenset[tuple[Path, int]]] = {}
        # ファイルの結合（Ver300。要件定義書 4.10）：結合して消えるファイル → 結合先のファイル。
        self._absorbed: dict[Path, Path] = {}
        # 挿入・結合の材料にした画像から作った、1ページの PDF（一時フォルダ。4.12）。
        self._image_pdfs: dict[Path, Path] = {}
        self._material_dir: Path | None = None
        self._order = {r.path: i for i, r in enumerate(self.records)}
        # ファイルの分割（Ver300。要件定義書 4.10）：分けて新しく作るファイル（まだない
        # ファイル。位置 → 記録）と、どのファイルから分けたか。元に戻したものは
        # _split_active から外す（やり直すと、同じものが戻る）。
        self._base_records = list(self.records)
        self._base_eligible = list(self.eligible)
        self._virtual: dict[Path, FileRecord] = {}
        self._split_parent: dict[Path, Path] = {}
        self._split_active: set[Path] = set()
        # 圧縮（Ver300。4.13）：大きさの見込み（estimate_key → バイト）と、②のプレビュー用の一時ファイル。
        self._estimates: dict[tuple, int] = {}
        self._reduced_previews: dict[tuple, tuple[Path, int] | None] = {}
        # 左の一覧のファイルの削除・追加（Ver300。workspace_files.py）：追加したファイル（元の
        # 場所 → 記録。元に戻しても記録は残し、やり直すと同じものが戻る）、いま追加している
        # もの（並び順）、そのうち更新の対象になるもの、削除したファイル。
        self._added: dict[Path, FileRecord] = {}
        self._added_active: list[Path] = []
        self._added_eligible: set[Path] = set()
        self._removed: set[Path] = set()
        # A4 にそろえた・付箋等を取り除いた姿（プレビュー用。(元, そろえる, 取り除く) → 一時ファイル）。
        self._cleaned_previews: dict[tuple, Path | None] = {}

        self._files: dict[Path, Selection] = {}
        for record in self.eligible:
            item = ReviewItem(
                preliminary=self._individual_preliminary(record),
                preview_record=record,
                batch_dir=record.path.parent,
            )
            applied = self.manifest.applied_for(self.root, record.path)
            options = build_review_options(item, config.filename, applied, config.compress)
            keys = {o.key for o in options}
            reduced_dpi = None
            if APPLIED_REDUCE in applied and "reduce" in keys:
                entry = self._entry(record.path)
                reduced_dpi = (entry.reduce_dpi if entry is not None else None) or config.compress.reduce_dpi[0]
            self._files[record.path] = Selection(
                item=item,
                options=options,
                choices={o.key: o.default_checked for o in options if o.enabled},
                stamped=APPLIED_STAMP in applied and "stamp" in keys,
                reduce_dpi=reduced_dpi,
                compressed=APPLIED_COMPRESS in applied and "compress" in keys,
                reduced_dpi=reduced_dpi,
            )

        self.groups: list[GroupSelection] = []
        self._group_of: dict[Path, GroupSelection] = {}
        for group in find_branch_merge_groups(self.eligible, config.evidence_number):
            prelim = PlannedCopy(
                record=_synthetic_merge_record(group, config.filename),
                output_filename=group.output_filename,
                merge_group=group,
                merge_stamp=True,
            )
            item = ReviewItem(
                preliminary=prelim,
                preview_record=group.representative.record,
                batch_dir=group.representative.record.path.parent,
            )
            options = build_review_options(item, config.filename, compress_config=config.compress)
            gs = GroupSelection(
                group=group,
                merge=True,
                selection=Selection(
                    item=item,
                    options=options,
                    choices={o.key: o.default_checked for o in options if o.enabled},
                ),
            )
            self.groups.append(gs)
            for member in group.members:
                self._group_of[member.record.path] = gs

    # ------------------------------------------------------------------ 既定の計画
    def _stamp_label(self, record: FileRecord, name: str | None = None) -> str | None:
        """証拠番号スタンプの候補（ラベル）。候補にならないファイルは None。
        name：名前を変えたときの新しい名前（その名前の先頭の証拠番号を使う）。"""
        if record.detected_format not in STAMPABLE_FORMATS:
            return None
        if record.pdf_finding is not None and record.pdf_finding.has_password:
            # パスワード付きPDFは復号キーがないと内容を読めず、スタンプできない。
            return None
        match = match_evidence_number(name or record.path.name, self.config.evidence_number)
        return match.evidence_number_label if match is not None else None

    def _individual_preliminary(self, record: FileRecord) -> PlannedCopy:
        """このファイルに、検出結果どおりの処理をすべて行うと仮定した計画。"""
        sanitize = needs_sanitization(record.filename_finding)
        return PlannedCopy(
            record=record,
            output_filename=(
                sanitize_filename(record.path.name, self.config.filename)
                if sanitize
                else record.path.name
            ),
            strip_metadata=record.metadata_finding is not None
            and record.metadata_finding.has_metadata
            and can_strip_metadata(record.detected_format),
            stamp_label=self._stamp_label(record),
            filename_sanitized=sanitize,
        )

    # ------------------------------------------------------------------ 共通の小さな手順
    def _entry(self, path: Path) -> ManifestEntry | None:
        """このファイルの更新記録（記録のないファイル・対象フォルダの外のファイルは None）。"""
        try:
            rel = path.relative_to(self.root).as_posix()
        except ValueError:
            return None
        return self.manifest.get(rel)

    def _single_original(self, path: Path) -> Path | None:
        """以前に更新したファイルの、「元のファイル」フォルダの元のファイル（1つだけのとき。
        なければ None）。"""
        entry = self._entry(path)
        if entry is None or len(entry.originals) != 1:
            return None
        original = self.root / BACKUP_DIRNAME / entry.originals[0]
        return original if original.exists() else None

    def _temp_dir(self, prefix: str) -> Path:
        """一時フォルダを作る（このオブジェクトがなくなると消える）。"""
        tmp = tempfile.TemporaryDirectory(prefix=prefix)
        self._temp_dirs.append(tmp)
        return Path(tmp.name)

    def _carried_cleanup(self, paths, item: PlannedCopy) -> dict:
        """元のファイルから作り直すときの、A4 にそろえる・付箋等の除去（4.15・4.16）。いまの
        選択（item）に加え、以前の更新で済ませたもの（更新記録）も、やり直す（元のファイルは、
        そろえる前・取り除く前のものなので）。"""
        applied: set[str] = set()
        for path in paths:
            entry = self._entry(path)
            if entry is not None:
                applied.update(entry.applied)
        return {
            "fit_a4": item.fit_a4 if item.fit_a4 is not None
            else (self.config.pdf if APPLIED_FIT_A4 in applied else None),
            "strip_hidden": item.strip_hidden or APPLIED_STRIP_HIDDEN in applied,
        }

    def cleaned_preview(self, path: Path, fit: bool, strip: bool) -> Path | None:
        """path の PDF に、A4 にそろえる・付箋等の除去を行った姿（一時フォルダ。プレビュー用。
        ページの数・並びは変わらない）。作れなければ None。"""
        if not (fit or strip):
            return path
        key = (path, fit, strip)
        if key not in self._cleaned_previews:
            out = self._temp_dir("va_mints_clean_") / path.name
            try:
                clean_pdf(path, out, self.config.pdf if fit else None, strip)
                self._cleaned_previews[key] = out
            except Exception:  # noqa: BLE001 - プレビューを出せないだけ。更新のときに理由が出る
                self._cleaned_previews[key] = None
        return self._cleaned_previews[key]

    def _cleaned(self, record: FileRecord, sel: Selection, path: Path) -> Path:
        """プレビューに描くファイルを、A4 にそろえる・付箋等の除去（4.15・4.16）のチェックが
        あれば、それを行った姿にする（作れなければ、そのまま）。"""
        if record.detected_format != "PDF" or path.suffix.lower() != ".pdf":
            return path
        fit, strip = sel.choices.get("fit_a4", False), sel.choices.get("strip_hidden", False)
        return self.cleaned_preview(path, fit, strip) or path

    # ------------------------------------------------------------------ 右ペイン
    def is_eligible(self, record: FileRecord) -> bool:
        return record.path in self._eligible_paths

    def group_for(self, record: FileRecord) -> GroupSelection | None:
        return self._group_of.get(record.path)

    def pane_view(self, record: FileRecord) -> PaneView:
        """このファイルを選んだとき、右ペインに出す項目。"""
        if not self.is_eligible(record):
            return PaneView(merge_option=None, merge_checked=False, selection=None, is_group=False)
        gs = self.group_for(record)
        if gs is None:
            return PaneView(None, False, self._files[record.path], False)
        names = "、".join(m.record.path.name for m in gs.group.members)
        merge_option = ReviewOption(
            key=MERGE_KEY,
            label=(
                f"枝番ファイル結合：{names} → 「{gs.group.output_filename}」"
                f"（{len(gs.group.members)}件を1つのファイルに結合。"
                "このチェックは、結合するすべてのファイルで共通です）"
            ),
            default_checked=True,
        )
        if gs.merge:
            return PaneView(merge_option, True, gs.selection, True)
        return PaneView(merge_option, False, self._files[record.path], False)

    def set_merge(self, record: FileRecord, checked: bool) -> None:
        """枝番ファイル結合のチェック。ほかのファイルと結合したファイルがグループにあるときは、
        入れられない（ValueError）。"""
        gs = self.group_for(record)
        if gs is None:
            return
        if checked and any(
            self.is_absorbed(m.record) or self.absorbed_into(m.record) for m in gs.group.members
        ):
            raise ValueError(
                "このグループのファイルは、ほかのファイルと結合しているため、枝番ファイル結合はできません"
                "（結合を元に戻してから、チェックを入れてください）。"
            )
        gs.merge = checked

    def mark_viewed(self, record: FileRecord) -> None:
        self.viewed.add(record.path)

    @property
    def has_edits(self) -> bool:
        """まだ更新していない編集（ページ・名前・結合・分割）があるか。"""
        return (
            self.has_page_edits
            or any(sel.renamed for sel in self._files.values())
            or bool(self._divisions)
            or bool(self._absorbed)
            or bool(self._split_active)
            or bool(self._added_active)
            or bool(self._removed)
        )

    def is_changed(self, record: FileRecord) -> bool:
        """このファイルに、まだ更新していない編集（ページ・名前）があるか。"""
        return self.is_edited(record) or self.is_renamed(record) or self.has_divisions(record)


    def preview_page(self, record: FileRecord, index: int) -> tuple[Path, int, int, str | None] | None:
        """下書きの index ページ目（0始まり）をプレビューに描くための（ファイル、そのファイルの
        ページ、回転、重ねて描くスタンプのラベル又は None）。描けないときは None。

        スタンプは下書きの1ページ目に重ねる（結合するグループのファイルは、それぞれの
        1ページ目に、それぞれの枝番。要件定義書 4.7）。スタンプ済みのファイルは、ページを
        編集したとき・スタンプのチェックを外したときは、スタンプのない姿（build_without_stamp。
        ページの並びは、いまのファイルと同じ）の上に描く（更新すると、元のファイルから
        作り直すため）。
        """
        view = self.pane_view(record)
        sel = view.selection
        if sel is None or not self.can_edit_pages(record):
            return record.path, index, 0, None
        pages = self.pages_of(record)
        if not 0 <= index < len(pages):
            return None
        ref = pages[index]
        if ref.path != record.path:
            # ほかのファイルから挿入・結合したページ：以前に更新したファイルなら、更新のときと
            # 同じく、「元のファイル」フォルダの元のファイルのページを描く（スタンプのない姿）。
            ref = compose_pages(self.root, self.manifest, [ref], self.config.pdf)[0]
        stamp_on = sel.choices.get("stamp", False)
        if view.is_group:
            gs = self.group_for(record)
            member = next(m for m in gs.group.members if m.record.path == record.path)
            label = member.match.evidence_number_label if stamp_on and index == 0 else None
            return self._cleaned(record, sel, ref.path), ref.index, ref.rotate, label
        path = ref.path
        branch = self.branch_labels(record)
        if branch:
            label = branch.get(index) if stamp_on else None
        else:
            label = sel.item.preliminary.stamp_label if stamp_on and index == 0 else None
        if sel.stamped and ref.path == record.path:
            if self.is_edited(record) or branch or not stamp_on:
                unstamped = self._unstamped_record(record)
                if unstamped is None:
                    return None
                path = unstamped.path
            else:
                label = None  # ファイルそのものにスタンプが入っている
        return self._cleaned(record, sel, path), ref.index, ref.rotate, label

    # ------------------------------------------------------------------ プレビュー
    def preview_state(self, record: FileRecord) -> tuple[str | None, tuple[float, float] | None]:
        """プレビューの描き方：（スタンプのラベル又はNone、スタンプ位置）。

        結合ONのグループのファイルは、そのファイル自身の枝番ラベルで、グループの
        スタンプの選択に従う（evidence_merger.merge_branch_group が実際に焼き込むものと同じ）。
        """
        view = self.pane_view(record)
        sel = view.selection
        if sel is None:
            return None, None
        stamp_on = sel.choices.get("stamp", False)
        if view.is_group:
            gs = self.group_for(record)
            member = next(m for m in gs.group.members if m.record.path == record.path)
            label = member.match.evidence_number_label if stamp_on else None
            return label, sel.stamp_margin_mm
        # スタンプ済みのファイルは、ファイルそのものにスタンプが入っている。プレビューで
        # 重ねて描かない（外したときの姿は preview_record が用意する）。
        label = sel.item.preliminary.stamp_label if stamp_on and not sel.stamped else None
        return label, sel.stamp_margin_mm

    def preview_record(self, record: FileRecord) -> FileRecord | None:
        """プレビューに描くファイル。

        ふつうは record そのもの。スタンプ済みのファイルでスタンプのチェックを外して
        いるときは、スタンプのない姿（更新したときにできるものと同じ。
        updater.build_without_stamp）を一時フォルダに作り、それを指す記録を返す。
        作れなかったとき（更新前のファイルがない等）は None。
        """
        view = self.pane_view(record)
        sel = view.selection
        if sel is None or view.is_group or not sel.stamped or sel.choices.get("stamp", False):
            return record
        return self._unstamped_record(record)

    def _unstamped_record(self, record: FileRecord) -> FileRecord | None:
        """スタンプ済みのファイルの、スタンプのない姿（一時フォルダに作る。作れなければ None）。"""
        if record.path not in self._unstamped_previews:
            preview: FileRecord | None = None
            try:
                entry = self._entry(record.path)
                folder = self._temp_dir("va_mints_preview_")
                path = build_without_stamp(self.root, entry, self.config, folder)
                preview = dataclasses.replace(record, path=path)
            except Exception:  # noqa: BLE001 - プレビューを出せないだけ。更新のときに理由が出る
                preview = None
            self._unstamped_previews[record.path] = preview
        return self._unstamped_previews[record.path]

    def output_name(self, record: FileRecord) -> str | None:
        """いまの選択で更新した後のファイル名（更新の対象でなければ None）。"""
        view = self.pane_view(record)
        if view.selection is None:
            return None
        return self._final(view.selection).output_filename

    # ------------------------------------------------------------------ 更新の計画
    def _edited_pages(self, record: FileRecord) -> list[PageRef]:
        """更新で作るページの表（元のファイルを指すもの。updater.compose_pages）。"""
        return compose_pages(self.root, self.manifest, self.pages_of(record), self.config.pdf)

    def _stamps_for(self, record: FileRecord, pages: list[PageRef], label: str) -> tuple[tuple[int, str], ...]:
        """ページを編集したファイルのスタンプ（位置、ラベル）。

        以前に枝番ファイル結合で作ったファイルは、結合した各材料の1ページ目が、編集の後に
        どこへ動いたかを探し、そこにその枝番を押す（消したページには押さない）。
        それ以外は、1ページ目に押す。
        """
        entry = self._entry(record.path)
        if entry is not None and APPLIED_MERGE in entry.applied and entry.originals:
            backup = self.root / BACKUP_DIRNAME
            stamps: list[tuple[int, str]] = []
            for original in entry.originals:
                match = match_evidence_number(Path(original).name, self.config.evidence_number)
                if match is None:
                    continue
                for position, ref in enumerate(pages):
                    if ref.path == backup / original and ref.index == 0:
                        stamps.append((position, match.evidence_number_label))
                        break
            return tuple(sorted(stamps))
        return ((0, label),)

    def _final(self, sel: Selection) -> PlannedCopy:
        item = self._final_body(sel)
        if sel.renamed:
            item = dataclasses.replace(item, output_filename=sel.renamed, filename_sanitized=False)
        if item.merge_group is None and item.pages is not None:
            if self.is_created(item.record):
                item = dataclasses.replace(item, created=True)
            elif self.split_children(item.record):
                item = dataclasses.replace(item, split=True)
        if item.merge_group is None:
            absorbed = self.absorbed_into(item.record)
            if absorbed:
                # 結合するファイル（更新で「元のファイル」フォルダへ移る）。200MB分割の見積もりの
                # ため、大きさは材料の合計とみなす。
                record = dataclasses.replace(
                    item.record,
                    size_bytes=item.record.size_bytes + sum(r.size_bytes for r in absorbed),
                )
                item = dataclasses.replace(
                    item, record=record, absorbed=tuple(r.path for r in absorbed)
                )
            else:
                # 圧縮の後の大きさで、200MB分割の仕分けをする（要件定義書 4.13）。見込みが
                # まだ出ていないときは、いまの大きさのまま（多めに見積もる側）。
                estimate = self.estimate(item.record)
                if estimate is not None and estimate < item.record.size_bytes:
                    item = dataclasses.replace(
                        item, record=dataclasses.replace(item.record, size_bytes=estimate)
                    )
        return item

    def _final_body(self, sel: Selection) -> PlannedCopy:
        prelim = sel.item.preliminary
        if prelim.merge_group is not None:
            members = [m for m in prelim.merge_group.members]
            # 材料のページを編集したとき・A4 にそろえる／付箋等を取り除くとき（4.15・4.16。
            # スタンプより前に行うため）は、ページの表から作る。
            if any(self.is_edited(m.record) for m in members) or any(
                sel.choices.get(k, False) for k in CLEANUP_KEYS
            ):
                # 結合する材料のページを編集した（Ver300）：材料ごとの表をつなぎ、各材料の
                # 最初のページにその枝番を押す。
                base = apply_review_choices(
                    sel.item, sel.choices, self.config.filename, stamp_offset_mm=sel.stamp_margin_mm,
                    **self._compress_args(sel),
                )
                pages: list[PageRef] = []
                stamps: list[tuple[int, str]] = []
                for m in members:
                    start = len(pages)
                    pages.extend(self._edited_pages(m.record))
                    if base.merge_stamp:
                        stamps.append((start, m.match.evidence_number_label))
                return dataclasses.replace(
                    base, pages=tuple(pages), stamps=tuple(stamps), merge_stamp=False,
                    **self._carried_cleanup([m.record.path for m in members], base),
                )
        elif (
            self.is_edited(prelim.record)
            or self.has_divisions(prelim.record)
            or (prelim.record.detected_format == "PDF" and self._compression_undone(sel))
        ):
            # ページを編集した（Ver300）：ページの表から作る。スタンプ済みのファイルも、
            # 元のファイルから作り直し、チェックがあればスタンプを押し直す。済ませた圧縮の
            # チェックを外した（解像度を変えた）PDF も、同じく元のファイルから作り直す。
            # 圧縮は、チェック（済んでいるものは入った状態）のとおりにやり直す。
            record = prelim.record
            base = apply_review_choices(
                sel.item,
                {**sel.choices, "stamp": False},
                self.config.filename,
                **self._compress_args(sel),
            )
            pages = self._edited_pages(record)
            stamps = ()
            margins = (None, None)
            branch = self.branch_labels(record)
            if sel.choices.get("stamp", False) and branch:
                stamps = tuple(sorted(branch.items()))
                margins = sel.stamp_margin_mm or (None, None)
            elif sel.choices.get("stamp", False) and prelim.stamp_label is not None:
                stamps = self._stamps_for(record, pages, prelim.stamp_label)
                margins = sel.stamp_margin_mm or (None, None)
            entry = self._entry(record.path)
            strip = base.strip_metadata or (
                entry is not None and APPLIED_STRIP_METADATA in entry.applied
            )
            return dataclasses.replace(
                base,
                pages=tuple(pages),
                stamps=stamps,
                **self._carried_cleanup([record.path], base),
                strip_metadata=strip,
                stamp_margin_top_mm=margins[0],
                stamp_margin_right_mm=margins[1],
            )
        choices = dict(sel.choices)
        rebuild = self._image_original(sel) if self._compression_undone(sel) else None
        if rebuild is not None:
            # 軽くした画像のチェックを外した（解像度を変えた）：元の画像から作り直す。スタンプ
            # 済みでチェックが入っていれば押し直し、済ませたメタデータ除去もやり直す。
            entry = self._entry(prelim.record.path)
            item = apply_review_choices(
                sel.item, choices, self.config.filename, **self._compress_args(sel),
                stamp_offset_mm=sel.stamp_margin_mm,
            )
            return dataclasses.replace(
                item,
                rebuild_from=rebuild,
                strip_metadata=item.strip_metadata or APPLIED_STRIP_METADATA in entry.applied,
            )
        if sel.stamped:
            # スタンプ済み：チェックが入っていても、もう一度は付けない。
            choices["stamp"] = False
        # 済ませた圧縮：チェックが入っていても（②は同じ解像度なら）、もう一度はしない。
        if sel.compressed:
            choices["compress"] = False
        if sel.reduced_dpi is not None and self.reduce_dpi_of(sel) == sel.reduced_dpi:
            choices["reduce"] = False
        return apply_review_choices(
            sel.item,
            choices,
            self.config.filename,
            **self._compress_args(sel),
            stamp_offset_mm=sel.stamp_margin_mm,
        )

    def _planned(self) -> list[tuple[PlannedCopy, Selection]]:
        planned: list[tuple[PlannedCopy, Selection]] = []
        done_groups: set[int] = set()
        for record in self.eligible:
            if self.is_absorbed(record):
                continue  # 結合先のファイルの計画に入る
            gs = self.group_for(record)
            if gs is not None and gs.merge:
                if id(gs) not in done_groups:
                    done_groups.add(id(gs))
                    planned.append((self._final(gs.selection), gs.selection))
                continue
            sel = self._files[record.path]
            planned.append((self._final(sel), sel))
        return planned

    def planned_items(self) -> list[PlannedCopy]:
        """いまの選択のとおりの計画（読み込みの並び順。結合グループは、最初に現れた
        メンバーの位置に置く）。処理のないファイルも含む（200MB分割の対象のため）。"""
        return [item for item, _sel in self._planned()]

    def _dest_dir(self, item: PlannedCopy, batches: list[Batch], batch: Batch) -> Path:
        """更新後のファイルを置くフォルダ。

        分割が要る（提出回が2つ以上）ときは、対象フォルダの直下の「提出用_NN」。
        要らないときは、いまの位置のまま。ただし、以前の更新で「提出用_NN」に入って
        いたファイルは、分割が要らなくなったら直下に戻す。
        """
        if len(batches) > 1:
            return self.root / batch.folder_name
        if item.merge_group is None and self.is_added(item.record):
            return self.root  # 追加したファイルは、対象フォルダの直下に写す
        source = (
            item.merge_group.representative.record.path
            if item.merge_group is not None
            else item.record.path
        )
        parent = source.parent
        if parent.parent == self.root and is_batch_dir_name(parent.name):
            return self.root
        return parent

    def plan_update(self) -> UpdatePlan:
        pairs = self._planned()
        planned = [item for item, _sel in pairs]
        selection_of = {id(item): sel for item, sel in pairs}
        batches = plan_batches(planned, self.config.upload, self.config.evidence_number)
        plan = UpdatePlan(batches=batches)
        counts = {
            APPLIED_SANITIZE: 0,
            APPLIED_STRIP_METADATA: 0,
            APPLIED_STAMP: 0,
            APPLIED_MERGE: 0,
            APPLIED_PAGES: 0,
            APPLIED_JOIN: 0,
            APPLIED_SPLIT: 0,
            COUNT_SPLIT_NEW: 0,
            APPLIED_REDUCE: 0,
            APPLIED_COMPRESS: 0,
            APPLIED_FIT_A4: 0,
            APPLIED_STRIP_HIDDEN: 0,
            APPLIED_RENAME: 0,
            COUNT_DIVISIONS: 0,
            ACTION_UNSTAMP: 0,
            ACTION_REBUILD: 0,
            COUNT_ADD: 0,
            COUNT_REMOVE: len(self.removed_records),
        }
        plan.removals = [r.path for r in self.removed_records]
        for batch in batches:
            for item in batch.items:
                added = item.merge_group is None and self.is_added(item.record)
                if item.merge_group is not None:
                    sources = tuple(m.record.path for m in item.merge_group.members)
                elif item.created or added:
                    # 分割で新しく作るファイル・追加したファイル：なくなるファイルはない
                    sources = ()
                else:
                    sources = (item.record.path, *item.absorbed)
                dest_dir = self._dest_dir(item, batches, batch)
                keys = tuple(applied_keys(item))
                if added:
                    # 追加したファイルの名前は、写すときに決める（変更とは数えない）。
                    keys = tuple(k for k in keys if k != APPLIED_RENAME)
                    counts[COUNT_ADD] += 1
                moves = bool(sources) and dest_dir != sources[0].parent
                sel = selection_of[id(item)]
                # ページを編集したファイルは、元のファイルから作り直すので、スタンプを消す
                # 手順（updater.remove_stamp）は要らない（チェックがなければ押さないだけ）。
                unstamp = (
                    sel.stamped
                    and not sel.choices.get("stamp", False)
                    and item.pages is None
                    and item.rebuild_from is None  # 元の画像から作り直すので、消す手順は要らない
                )
                declines = "stamp" in sel.choices and not sel.choices["stamp"]
                plan.updates.append(
                    PlannedUpdate(item, dest_dir, keys, moves, sources, unstamp, declines, added)
                )
                for key in keys:
                    counts[key] += 1
                # ページの表から作り直すファイルのうち、枝番の区切りだけを付けたもの・結合した
                # もの・分割したものは、確認の画面では「ページの編集」ではなく、それぞれの
                # 処理として数える。
                divisions = item.merge_group is None and self.has_divisions(item.record)
                if divisions:
                    counts[COUNT_DIVISIONS] += 1
                # 済ませた圧縮のチェックを外した（解像度を変えた）ので、元のファイルから作り直す
                # （PDF はページの表から作るが、ページを編集していなければ「ページの編集」とは数えない）。
                rebuild = item.merge_group is None and self._compression_undone(sel)
                if rebuild and ACTION_REBUILD not in keys:
                    counts[ACTION_REBUILD] += 1
                if APPLIED_PAGES in keys and (
                    (divisions and not self.is_edited(item.record))
                    # 枝番ファイル結合で、A4 にそろえる等のためにページの表から作るもの
                    or (
                        item.merge_group is not None
                        and not any(self.is_edited(m.record) for m in item.merge_group.members)
                    )
                    or APPLIED_JOIN in keys
                    or APPLIED_SPLIT in keys
                    or (rebuild and not divisions and not self.is_edited(item.record))
                ):
                    counts[APPLIED_PAGES] -= 1
                if item.created:
                    # 分けた数は、分ける前のファイルで数え、新しく作るファイルは別に数える。
                    counts[APPLIED_SPLIT] -= 1
                    counts[COUNT_SPLIT_NEW] += 1
                if unstamp:
                    counts[ACTION_UNSTAMP] += 1
                if moves:
                    plan.move_count += len(sources)
                if keys or unstamp:
                    looked = sources or (item.record.path,)
                    plan.unviewed_count += sum(1 for p in looked if p not in self.viewed)
        plan.counts = counts
        plan.description = self.description_job(
            plan.has_file_work, self.root / batches[0].folder_name if len(batches) > 1 else None
        )
        plan.hidden_remaining_count = sum(
            1 for _item, sel in pairs
            if any(o.key == "strip_hidden" for o in sel.options)
            and not sel.choices.get("strip_hidden", False)
        )
        plan.sequence_warnings = format_evidence_sequence_warnings(
            (item.output_filename for item in planned), self.config.evidence_number
        )
        limit = self.config.upload.max_total_bytes
        plan.oversized = [
            r for r in self.eligible
            if r.exceeds_single_file_limit
            and not self.is_absorbed(r)
            # 圧縮の見込みで上限に収まるものは、注意しない。
            and not ((self.estimate(r) or limit + 1) <= limit)
        ]
        plan.unstamped_image_count = sum(
            1
            for record in self.eligible
            if record.detected_format in ("JPEG", "PNG")
            and not self.is_absorbed(record)
            and self._files[record.path].choices.get("stamp") is False
            and not self._files[record.path].stamped
            # 付けないと決めて更新した画像は、もう数えない（毎回の確認に出し続けない）。
            and STAMP_DECLINED not in self.manifest.applied_for(self.root, record.path)
        )
        return plan


_COUNT_LABELS = (
    (APPLIED_SANITIZE, "ファイル名整形"),
    (APPLIED_STRIP_METADATA, "メタデータ除去"),
    (APPLIED_STAMP, "証拠番号スタンプ"),
    (APPLIED_MERGE, "枝番ファイル結合"),
    (APPLIED_PAGES, "ページの編集（削除・挿入・並べ替え・回転）"),
    (APPLIED_JOIN, "ファイルの結合"),
    (APPLIED_SPLIT, "ファイルの分割"),
    (COUNT_SPLIT_NEW, "分割で新しく作るファイル"),
    (APPLIED_REDUCE, "画像を軽くする（解像度を下げる。画質が落ちます）"),
    (APPLIED_COMPRESS, "画質を損なわない圧縮"),
    (APPLIED_FIT_A4, "用紙をA4にそろえる"),
    (APPLIED_STRIP_HIDDEN, "付箋・コメント・添付ファイルを取り除く"),
    (COUNT_DIVISIONS, "1つのファイルの中の枝番の区切り"),
    (APPLIED_RENAME, "ファイル名の変更"),
    (COUNT_ADD, "追加するファイル（対象フォルダに写します）"),
    (COUNT_REMOVE, "一覧から削除するファイル（「元のファイル」フォルダへ移します）"),
)


def apply_update(
    root: Path, update: PlannedUpdate, item: PlannedCopy, manifest: Manifest, config: Config
) -> UpdateOutcome | None:
    """［ファイルを更新］の1件分：（スタンプのチェックを外したファイルは）スタンプを消し、
    更新し、証拠番号スタンプを付けないと決めたかどうかを覚える（読み込み直したときに、
    スタンプへ自動でチェックを付けないため）。

    item は、update.item を isolate_shared_pages に通したもの。行う処理のないものは
    更新せず（None を返す）、スタンプの選択だけを覚える。失敗すると例外を送る（呼び出し側で、
    とばすか止めるかを決める）。覚えた選択の保存は、すべての後に save_choices で行う。
    """
    outcome = None
    if update.added:
        # 追加したファイル（Ver300）：まず対象フォルダに写し、写したものに処理をする。
        placed = add_file(
            root, item.record.path, update.dest_dir / item.output_filename, manifest, config.filename
        )
        item = dataclasses.replace(
            item,
            record=dataclasses.replace(item.record, path=placed),
            output_filename=placed.name,
            renamed_for_collision=False,
        )
        if update.keys:
            outcome = update_item(
                root, item, placed.parent, manifest, config.evidence_stamp, config.filename
            )
            path = outcome.path
        else:
            outcome = UpdateOutcome(path=placed, changed=True, moved=False)
            path = placed
    elif update.keys or update.moves or update.unstamp:
        if update.unstamp:
            remove_stamp(root, update.source_paths[0], manifest, config)
        outcome = update_item(
            root, item, update.dest_dir, manifest, config.evidence_stamp, config.filename
        )
        path = outcome.path
    else:
        path = update.source_paths[0] if update.source_paths else update.item.record.path
    record_stamp_choice(root, path, manifest, update.declines_stamp)
    return outcome


def apply_all_updates(
    root: Path, plan: UpdatePlan, config: Config, workspace: "Workspace | None" = None
) -> None:
    """計画のすべてを、画面の［ファイルを更新］と同じ手順で行う（失敗したら、そこで止まる）。
    画面を使わないとき（テスト）のためのもの。画面は、1件ずつ失敗を扱う（ui/app.py）。
    workspace：計画を作ったもの（証拠説明書〔4.17〕を作るとき）。"""
    manifest = Manifest.load(root)
    prepared = isolate_shared_pages(root, [u.item for u in plan.updates])
    try:
        # 削除は、ページの材料の写しを取った後、ほかの更新より前に行う（削除するファイルの
        # ページを、ほかのファイルに入れていても、写しから作れる）。
        for path in plan.removals:
            remove_file(root, path, manifest)
        results = []
        for update, item in zip(plan.updates, prepared):
            outcome = apply_update(root, update, item, manifest, config)
            results.append((update, update_result_path(update, outcome)))
        save_choices(root, manifest)
    finally:
        discard_unused_materials(root, Manifest.load(root))
    if plan.description is not None and workspace is not None:
        workspace.finish_description(plan.description, results)


def update_result_path(update: PlannedUpdate, outcome: UpdateOutcome | None) -> Path:
    """1件の更新の後の、そのファイルの位置（何もしなかったものは、いまの位置）。"""
    if outcome is not None:
        return outcome.path
    return update.source_paths[0] if update.source_paths else update.item.record.path


def describe_failure(name: str, exc: BaseException) -> str:
    """更新できなかったファイルの「名前（理由）」。完了のお知らせに並べる。"""
    if isinstance(exc, PermissionError):
        reason = "ファイルを書き換えられません。他のアプリで開いている可能性があります"
    elif isinstance(exc, FileNotFoundError):
        reason = "ファイルが見つかりません。読み込みの後に、移動・削除・改名された可能性があります"
    else:
        detail = " ".join(str(exc).split())
        if len(detail) > 80:
            detail = detail[:80] + "…"
        reason = f"{type(exc).__name__}：{detail}" if detail else type(exc).__name__
    return f"{name}（{reason}）"


def format_skipped(skipped: list[str], limit: int = 10) -> str:
    """完了のお知らせに足す、更新できなかったファイルの一覧（なければ空文字列）。"""
    if not skipped:
        return ""
    lines = ["", "", f"次の {len(skipped)} 件は更新できませんでした（更新前のままです）。"]
    lines.extend(f"・{item}" for item in skipped[:limit])
    if len(skipped) > limit:
        lines.append(f"...ほか{len(skipped) - limit}件")
    return "\n".join(lines)


def unstamped_image_line(plan: UpdatePlan) -> str:
    """「証拠番号スタンプを付けない画像：○件」の行（確認の画面・更新する項目がないときの知らせ）。"""
    return (
        f"・証拠番号スタンプを付けない画像：{plan.unstamped_image_count} 件"
        "（画像のスタンプは、自動ではチェックが付きません）"
    )


def describe_update_plan(plan: UpdatePlan, max_total_mb: float) -> str:
    """［ファイルを更新］を押したときの確認の文（CLAUDE.md 設計原則3：ファイルを
    変更する処理は、実行前に、処理の種類ごとに対象の数を示して確認を得る）。"""
    lines = ["次の内容で、フォルダの中のファイルを更新します。", ""]
    for key, label in _COUNT_LABELS:
        if plan.counts.get(key):
            unit = "組" if key == APPLIED_MERGE else "件"
            lines.append(f"・{label}：{plan.counts[key]} {unit}")
    if plan.counts.get(ACTION_REBUILD):
        lines.append(
            f"・圧縮をやめる・解像度を変える：{plan.counts[ACTION_REBUILD]} 件"
            "（「元のファイル」フォルダの更新前のファイルから作り直します。"
            "更新の後に他のアプリで直した内容があれば、失われます）"
        )
    if plan.counts.get(ACTION_UNSTAMP):
        lines.append(
            f"・証拠番号スタンプの消去：{plan.counts[ACTION_UNSTAMP]} 件"
            "（「元のファイル」フォルダの更新前のファイルから作り直します。"
            "更新の後に他のアプリで直した内容があれば、失われます）"
        )
    if plan.move_count:
        if len(plan.batches) > 1:
            lines.append(
                f"・「提出用_NN」フォルダへの仕分け：{plan.move_count} 件"
                f"（合計サイズが{max_total_mb:g}MBを超えるため、{len(plan.batches)} 回に分けて提出します）"
            )
        else:
            lines.append(f"・「提出用_NN」フォルダから直下へ戻す：{plan.move_count} 件（分割が不要になったため）")
    if plan.unstamped_image_count:
        lines.append(unstamped_image_line(plan))
    if plan.hidden_remaining_count:
        lines.append(
            f"・付箋・コメント・添付ファイルが残るファイル：{plan.hidden_remaining_count} 件"
            "（取り除く項目は、自動ではチェックが付きません）"
        )
    if plan.description is not None:
        job = plan.description
        if job.replaces is not None:
            how = f"前に作った「{job.replaces.name}」は消して、作り直します）"
        elif job.target.exists():
            how = "同じ名前のファイルを置き換えます）"
        else:
            how = "新しく作ります）"
        place = "対象フォルダ" if not is_batch_dir_name(job.target.parent.name) else f"「{job.target.parent.name}」フォルダ"
        lines.append(f"・証拠説明書の作成：1 件（{place}の「{job.target.name}」。" + how)
        if job.conflict:
            lines.append(f"　※ {job.conflict}")
        if job.unchosen:
            lines.append(f"　原本・写しの別を選んでいない行：{job.unchosen} 行")
        if job.blank:
            lines.append(f"　作成年月日・作成者・立証趣旨のどれかが空の行：{job.blank} 行")
    if plan.unviewed_count:
        lines.append("")
        lines.append(
            f"※ 内容を変えるファイルのうち {plan.unviewed_count} 件は、まだプレビューを表示していません。"
        )
        if plan.counts.get(APPLIED_STAMP):
            lines.append(
                "　証拠番号スタンプは、原本に証拠番号が既に記載されていないかを、"
                "プレビューで確かめてから付けてください。"
            )
    if plan.oversized:
        lines.append("")
        lines.append(
            f"※ 単体で{max_total_mb:g}MBを超えるファイルが {len(plan.oversized)} 件あります。"
            "圧縮又は分割を行わない限り、mintsへ提出できません（右のペインの「画像を軽くする」で"
            "小さくするか、ページを選んで［このページから別のファイルにする］で分けてください）。"
        )
        lines.extend(f"　・{r.path.name}（{r.size_mb:.1f}MB）" for r in plan.oversized[:5])
        if len(plan.oversized) > 5:
            lines.append(f"　...ほか{len(plan.oversized) - 5}件")
    if plan.sequence_warnings:
        lines.append("")
        lines.append("※ 号証の連番・欠番の疑いがあります。")
        lines.extend(f"　{w}" for w in plan.sequence_warnings[:5])
        if len(plan.sequence_warnings) > 5:
            lines.append(f"　...ほか{len(plan.sequence_warnings) - 5}件")
    lines.append("")
    lines.append(
        "更新する前のファイルは、同じフォルダの「元のファイル」フォルダに残り、"
        "［元のファイルに戻す］で元に戻せます。"
    )
    lines.append("")
    lines.append("更新しますか？")
    return "\n".join(lines)
