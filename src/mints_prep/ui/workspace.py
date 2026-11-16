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
from mints_prep.processors.output_writer import (
    PlannedCopy,
    _synthetic_merge_record,
    select_eligible_records,
)
from mints_prep.processors.report_writer import format_evidence_sequence_warnings
from mints_prep.processors.updater import (
    ACTION_UNSTAMP,
    APPLIED_MERGE,
    APPLIED_ROTATE,
    APPLIED_SANITIZE,
    APPLIED_STAMP,
    APPLIED_STRIP_METADATA,
    STAMP_DECLINED,
    Manifest,
    applied_keys,
    build_without_stamp,
    is_batch_dir_name,
)
from mints_prep.ui.review_logic import (
    ReviewItem,
    ReviewOption,
    apply_review_choices,
    build_review_options,
)

# 証拠番号スタンプ（4.7）の対象形式。音声・動画には「ページ」の概念がなく
# 焼き込み対象にならないため対象外とする。
STAMPABLE_FORMATS = {"PDF", "JPEG", "PNG"}

MERGE_KEY = "merge"


@dataclass
class Selection:
    """1ファイル又は1結合グループの選択。"""

    item: ReviewItem
    options: list[ReviewOption]
    choices: dict[str, bool]
    # スタンプ位置調整（4.7）。None は config.yaml の既定のまま。
    stamp_margin_mm: tuple[float, float] | None = None
    # 向き補正（4.6）の回転方向（時計回り。右90°=90、左90°=270）。
    rotate_degrees: int = 270
    # 以前の更新で、証拠番号スタンプを付けたファイルか（更新記録による）。True のとき、
    # スタンプのチェックは「付いている」ことを表す：入れたままなら何もせず（もう一度は
    # 付けない）、外して更新すると消す。
    stamped: bool = False


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


@dataclass
class UpdatePlan:
    updates: list[PlannedUpdate] = field(default_factory=list)
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

    @property
    def changed_count(self) -> int:
        return sum(1 for u in self.updates if u.keys or u.unstamp)

    @property
    def has_work(self) -> bool:
        return any(u.keys or u.moves or u.unstamp for u in self.updates)


class Workspace:
    def __init__(self, scan_result: ScanResult, config: Config, manifest: Manifest | None = None) -> None:
        self.root = scan_result.root
        self.config = config
        self.records = list(scan_result.records)
        self.manifest = manifest if manifest is not None else Manifest()
        self.eligible = select_eligible_records(self.records)
        self._eligible_paths = {r.path for r in self.eligible}
        self.viewed: set[Path] = set()
        # スタンプのチェックを外したときのプレビュー用に作った、スタンプのない姿
        # （一時フォルダ。このオブジェクトがなくなると消える）。
        self._unstamped_previews: dict[Path, FileRecord | None] = {}
        self._temp_dirs: list[tempfile.TemporaryDirectory] = []

        self._files: dict[Path, Selection] = {}
        for record in self.eligible:
            item = ReviewItem(
                preliminary=self._individual_preliminary(record),
                preview_record=record,
                batch_dir=record.path.parent,
            )
            applied = self.manifest.applied_for(self.root, record.path)
            options = build_review_options(item, config.filename, applied)
            self._files[record.path] = Selection(
                item=item,
                options=options,
                choices={o.key: o.default_checked for o in options if o.enabled},
                stamped=APPLIED_STAMP in applied and any(o.key == "stamp" for o in options),
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
            options = build_review_options(item, config.filename)
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
    def _stamp_label(self, record: FileRecord) -> str | None:
        """証拠番号スタンプの候補（ラベル）。候補にならないファイルは None。"""
        if record.detected_format not in STAMPABLE_FORMATS:
            return None
        if record.pdf_finding is not None and record.pdf_finding.has_password:
            # パスワード付きPDFは復号キーがないと内容を読めず、スタンプできない。
            return None
        match = match_evidence_number(record.path.name, self.config.evidence_number)
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
            and record.metadata_finding.has_metadata,
            stamp_label=self._stamp_label(record),
            filename_sanitized=sanitize,
        )

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
        gs = self.group_for(record)
        if gs is not None:
            gs.merge = checked

    def mark_viewed(self, record: FileRecord) -> None:
        self.viewed.add(record.path)

    # ------------------------------------------------------------------ プレビュー
    def preview_state(self, record: FileRecord) -> tuple[str | None, tuple[float, float] | None, int, bool]:
        """プレビューの描き方：（スタンプのラベル又はNone、スタンプ位置、回転方向、向き補正がONか）。

        結合ONのグループのファイルは、そのファイル自身の枝番ラベルで、グループの
        スタンプの選択に従う（evidence_merger.merge_branch_group が実際に焼き込むものと同じ）。
        """
        view = self.pane_view(record)
        sel = view.selection
        if sel is None:
            return None, None, 270, False
        stamp_on = sel.choices.get("stamp", False)
        if view.is_group:
            gs = self.group_for(record)
            member = next(m for m in gs.group.members if m.record.path == record.path)
            label = member.match.evidence_number_label if stamp_on else None
            return label, sel.stamp_margin_mm, 270, False
        # スタンプ済みのファイルは、ファイルそのものにスタンプが入っている。プレビューで
        # 重ねて描かない（外したときの姿は preview_record が用意する）。
        label = sel.item.preliminary.stamp_label if stamp_on and not sel.stamped else None
        return label, sel.stamp_margin_mm, sel.rotate_degrees, sel.choices.get("rotate", False)

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
        if record.path not in self._unstamped_previews:
            preview: FileRecord | None = None
            try:
                entry = self.manifest.get(record.path.relative_to(self.root).as_posix())
                tmp = tempfile.TemporaryDirectory(prefix="va_mints_preview_")
                self._temp_dirs.append(tmp)
                path = build_without_stamp(self.root, entry, self.config, Path(tmp.name))
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
    def _final(self, sel: Selection) -> PlannedCopy:
        choices = sel.choices
        if sel.stamped:
            # スタンプ済み：チェックが入っていても、もう一度は付けない。
            choices = {**choices, "stamp": False}
        return apply_review_choices(
            sel.item,
            choices,
            self.config.filename,
            stamp_offset_mm=sel.stamp_margin_mm,
            rotate_degrees=sel.rotate_degrees,
        )

    def _planned(self) -> list[tuple[PlannedCopy, Selection]]:
        planned: list[tuple[PlannedCopy, Selection]] = []
        done_groups: set[int] = set()
        for record in self.eligible:
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

        分割が要る（提出回が2つ以上）ときは、読み込んだフォルダの直下の「提出用_NN」。
        要らないときは、いまの位置のまま。ただし、以前の更新で「提出用_NN」に入って
        いたファイルは、分割が要らなくなったら直下に戻す。
        """
        if len(batches) > 1:
            return self.root / batch.folder_name
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
            APPLIED_ROTATE: 0,
            APPLIED_MERGE: 0,
            ACTION_UNSTAMP: 0,
        }
        for batch in batches:
            for item in batch.items:
                sources = tuple(
                    m.record.path for m in item.merge_group.members
                ) if item.merge_group is not None else (item.record.path,)
                dest_dir = self._dest_dir(item, batches, batch)
                keys = tuple(applied_keys(item))
                moves = dest_dir != sources[0].parent
                sel = selection_of[id(item)]
                unstamp = sel.stamped and not sel.choices.get("stamp", False)
                declines = "stamp" in sel.choices and not sel.choices["stamp"]
                plan.updates.append(
                    PlannedUpdate(item, dest_dir, keys, moves, sources, unstamp, declines)
                )
                for key in keys:
                    counts[key] += 1
                if unstamp:
                    counts[ACTION_UNSTAMP] += 1
                if moves:
                    plan.move_count += len(sources)
                if keys or unstamp:
                    plan.unviewed_count += sum(1 for p in sources if p not in self.viewed)
        plan.counts = counts
        plan.sequence_warnings = format_evidence_sequence_warnings(
            (item.output_filename for item in planned), self.config.evidence_number
        )
        plan.oversized = [r for r in self.eligible if r.exceeds_single_file_limit]
        plan.unstamped_image_count = sum(
            1
            for record in self.eligible
            if record.detected_format in ("JPEG", "PNG")
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
    (APPLIED_ROTATE, "向き補正"),
    (APPLIED_MERGE, "枝番ファイル結合"),
)


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
            "圧縮又は分割を行わない限り、mintsへ提出できません（本ツールでは対処できません）。"
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
