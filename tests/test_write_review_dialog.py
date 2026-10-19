from pathlib import Path

import pytest

pytest.importorskip("tkinter")

import tkinter as tk  # noqa: E402

from fixtures.generate_fixtures import (  # noqa: E402
    make_a4_pdf,
    make_multipage_pdf,
    make_password_pdf,
)
from mints_prep.config import EvidenceStampConfig, FilenameConfig  # noqa: E402
from mints_prep.models import (  # noqa: E402
    Classification,
    FileRecord,
    FilenameFinding,
    FormatStatus,
    MetadataFinding,
    PdfFinding,
)
from mints_prep.processors.output_writer import PlannedCopy  # noqa: E402
from mints_prep.ui.review_logic import ReviewItem  # noqa: E402
from mints_prep.ui.write_review_dialog import WriteReviewDialog  # noqa: E402

_FILENAME_CONFIG = FilenameConfig(
    max_length=100,
    warning_length=50,
    forbidden_chars=("\\", "/", ":", "*", "?", '"', "<", ">", "|"),
    jis_x0213_codec="shift_jis_2004",
)
_STAMP_CONFIG = EvidenceStampConfig(font_size_pt=14, margin_top_mm=8, margin_right_mm=8)


@pytest.fixture(scope="module")
def root():
    r = tk.Tk()
    r.withdraw()
    yield r
    r.destroy()


def _record(path: Path, has_metadata: bool = False, pdf_finding=None) -> FileRecord:
    return FileRecord(
        path=path,
        size_bytes=path.stat().st_size if path.exists() else 100,
        format_status=FormatStatus.RECORD_READY,
        detected_format="PDF",
        classification=Classification.A_COMPLIANT,
        filename_finding=FilenameFinding(
            length=len(path.name), exceeds_max_length=False, exceeds_warning_length=False
        ),
        metadata_finding=MetadataFinding(has_metadata=has_metadata, detected_fields=("Author",) if has_metadata else ()),
        pdf_finding=pdf_finding,
    )


def test_confirm_applies_default_recommended_choices(root: tk.Tk, tmp_path: Path):
    path = make_a4_pdf(tmp_path / "甲001 契約書.pdf")
    record = _record(path, has_metadata=True)
    prelim = PlannedCopy(record=record, output_filename=path.name, strip_metadata=True, stamp_label="甲001")
    item = ReviewItem(preliminary=prelim, preview_record=record, batch_dir=tmp_path)

    dialog = WriteReviewDialog(root, item, 1, 1, _FILENAME_CONFIG, _STAMP_CONFIG)
    root.update()

    assert set(dialog._choice_vars) == {"strip_metadata", "stamp"}
    assert all(v.get() for v in dialog._choice_vars.values())

    dialog._on_confirm()

    assert dialog.result.strip_metadata is True
    assert dialog.result.stamp_label == "甲001"
    assert not dialog.winfo_exists()


def test_unchecking_options_reverts_them(root: tk.Tk, tmp_path: Path):
    path = make_a4_pdf(tmp_path / "甲002 契約書.pdf")
    record = _record(path, has_metadata=True)
    prelim = PlannedCopy(record=record, output_filename=path.name, strip_metadata=True, stamp_label="甲002")
    item = ReviewItem(preliminary=prelim, preview_record=record, batch_dir=tmp_path)

    dialog = WriteReviewDialog(root, item, 1, 1, _FILENAME_CONFIG, _STAMP_CONFIG)
    root.update()
    dialog._choice_vars["stamp"].set(False)

    dialog._on_confirm()

    assert dialog.result.strip_metadata is True
    assert dialog.result.stamp_label is None


def test_cancel_returns_none_result_without_aborting_all(root: tk.Tk, tmp_path: Path):
    """「キャンセル」はこの1件だけをスキップする。残り全件の中断
    （abort_all）は伴わない（ウィンドウを閉じる操作専用）。"""
    path = make_a4_pdf(tmp_path / "甲003.pdf")
    record = _record(path)
    prelim = PlannedCopy(record=record, output_filename=path.name)
    item = ReviewItem(preliminary=prelim, preview_record=record, batch_dir=tmp_path)

    dialog = WriteReviewDialog(root, item, 1, 1, _FILENAME_CONFIG, _STAMP_CONFIG)
    root.update()

    dialog._on_cancel()

    assert dialog.result is None
    assert dialog.abort_all is False
    assert not dialog.winfo_exists()


def test_window_close_aborts_all(root: tk.Tk, tmp_path: Path):
    path = make_a4_pdf(tmp_path / "甲003.pdf")
    record = _record(path)
    prelim = PlannedCopy(record=record, output_filename=path.name)
    item = ReviewItem(preliminary=prelim, preview_record=record, batch_dir=tmp_path)

    dialog = WriteReviewDialog(root, item, 1, 1, _FILENAME_CONFIG, _STAMP_CONFIG)
    root.update()

    dialog._on_abort()

    assert dialog.result is None
    assert dialog.abort_all is True
    assert not dialog.winfo_exists()


def test_password_protected_file_shows_no_stamp_option(root: tk.Tk, tmp_path: Path):
    path = make_password_pdf(tmp_path / "甲004 パスワード付き.pdf")
    record = _record(path, pdf_finding=PdfFinding(has_password=True))
    prelim = PlannedCopy(record=record, output_filename=path.name)
    item = ReviewItem(preliminary=prelim, preview_record=record, batch_dir=tmp_path)

    dialog = WriteReviewDialog(root, item, 1, 1, _FILENAME_CONFIG, _STAMP_CONFIG)
    root.update()

    # 実行可否を選べる項目（sanitize/strip_metadata/stamp）はない。パスワード
    # 検出自体は、本ツールでは対処できない項目としてグレーアウト表示される。
    assert "sanitize" not in dialog._choice_vars
    assert "strip_metadata" not in dialog._choice_vars
    assert "stamp" not in dialog._choice_vars
    assert len(dialog._choice_vars) == 1
    info_key = next(iter(dialog._choice_vars))
    assert info_key.startswith("info_")
    assert dialog._preview_photo is None

    dialog.destroy()


def _find_checkbuttons(widget: tk.Misc) -> list[tk.Checkbutton]:
    found = []
    for child in widget.winfo_children():
        if isinstance(child, tk.Checkbutton):
            found.append(child)
        found.extend(_find_checkbuttons(child))
    return found


def _find_labels(widget: tk.Misc) -> list["ttk.Label"]:
    from tkinter import ttk

    found = []
    for child in widget.winfo_children():
        if isinstance(child, ttk.Label):
            found.append(child)
        found.extend(_find_labels(child))
    return found


def test_unfixable_detection_checkbox_is_grayed_out_while_actionable_stays_enabled(
    root: tk.Tk, tmp_path: Path
):
    """要件：本ツールで対処できない検出事項（例：操作制限PDF）はグレーアウト
    （state=DISABLED）表示、実行可否を選べる項目（メタデータ除去）は通常どおり
    操作可能（state=NORMAL）であること。"""
    from mints_prep.models import MetadataFinding, PdfFinding

    path = make_a4_pdf(tmp_path / "甲010 登記情報.pdf")
    record = _record(
        path,
        has_metadata=True,
        pdf_finding=PdfFinding(has_password=False, has_permission_restriction=True),
    )
    prelim = PlannedCopy(record=record, output_filename=path.name, strip_metadata=True)
    item = ReviewItem(preliminary=prelim, preview_record=record, batch_dir=tmp_path)

    dialog = WriteReviewDialog(root, item, 1, 1, _FILENAME_CONFIG, _STAMP_CONFIG)
    root.update()

    assert "strip_metadata" in dialog._choice_vars
    info_keys = [k for k in dialog._choice_vars if k.startswith("info_")]
    assert len(info_keys) == 1
    # 検出のみ・対処不可の項目は、実行を前提とするON状態ではなく、対処不可で
    # あることが伝わるOFF状態でグレーアウト表示する。
    assert dialog._choice_vars[info_keys[0]].get() is False

    checkbuttons = {str(cb.cget("text")): cb for cb in _find_checkbuttons(dialog)}
    metadata_cb = next(cb for text, cb in checkbuttons.items() if "メタデータ除去" in text)
    info_cb = next(cb for text, cb in checkbuttons.items() if "操作制限" in text or "印刷・コピー" in text)

    assert str(metadata_cb.cget("state")) == "normal"
    assert str(info_cb.cget("state")) == "disabled"
    assert str(info_cb.cget("disabledforeground")) == "#c00000"

    # 検出のみ・対処不可の項目は一覧の先頭（実行可否を選べる項目より上）に
    # 表示される（ウィジェットの配置順で確認する）。
    all_cbs = _find_checkbuttons(dialog)
    assert all_cbs.index(info_cb) < all_cbs.index(metadata_cb)

    # 「以下は本ツールでは対処できません」の見出し文言自体も赤字にする。
    note_label = next(
        lbl for lbl in _find_labels(dialog) if "以下は本ツールでは対処できません" in str(lbl.cget("text"))
    )
    assert str(note_label.cget("foreground")) == "#c00000"

    dialog.destroy()


def test_preview_shows_stamp_by_default_when_stamp_checkbox_defaults_on(root: tk.Tk, tmp_path: Path):
    path = make_a4_pdf(tmp_path / "甲005 契約書.pdf")
    record = _record(path)
    prelim = PlannedCopy(record=record, output_filename=path.name, stamp_label="甲005")
    item = ReviewItem(preliminary=prelim, preview_record=record, batch_dir=tmp_path)

    dialog = WriteReviewDialog(root, item, 1, 1, _FILENAME_CONFIG, _STAMP_CONFIG)
    root.update()

    # デフォルトでチェック済み（証拠番号スタンプ適用候補）のため、初期表示から
    # スタンプ適用後のプレビューが表示されているはず。
    assert dialog._preview_photo is not None
    initial_photo = dialog._preview_photo

    dialog._choice_vars["stamp"].set(False)
    root.update()

    # チェックを外すと、証拠番号を消去した（未適用の）プレビューに切り替わる。
    assert dialog._preview_photo is not None
    assert dialog._preview_photo is not initial_photo

    dialog._choice_vars["stamp"].set(True)
    root.update()

    assert dialog._preview_photo is not None

    dialog.destroy()


def test_position_controls_shown_when_stamp_option_present(root: tk.Tk, tmp_path: Path):
    path = make_a4_pdf(tmp_path / "甲006 契約書.pdf")
    record = _record(path)
    prelim = PlannedCopy(record=record, output_filename=path.name, stamp_label="甲006")
    item = ReviewItem(preliminary=prelim, preview_record=record, batch_dir=tmp_path)

    dialog = WriteReviewDialog(root, item, 1, 1, _FILENAME_CONFIG, _STAMP_CONFIG)
    root.update()

    assert dialog._position_label is not None
    assert dialog._stamp_margin_top_mm == _STAMP_CONFIG.margin_top_mm
    assert dialog._stamp_margin_right_mm == _STAMP_CONFIG.margin_right_mm

    dialog.destroy()


def test_position_controls_not_shown_when_no_stamp_option(root: tk.Tk, tmp_path: Path):
    path = make_a4_pdf(tmp_path / "準備書面2.pdf")
    record = _record(path)
    prelim = PlannedCopy(record=record, output_filename=path.name)
    item = ReviewItem(preliminary=prelim, preview_record=record, batch_dir=tmp_path)

    dialog = WriteReviewDialog(root, item, 1, 1, _FILENAME_CONFIG, _STAMP_CONFIG)
    root.update()

    assert dialog._position_label is None

    dialog.destroy()


def test_adjust_position_moves_stamp_and_updates_result(root: tk.Tk, tmp_path: Path):
    path = make_a4_pdf(tmp_path / "甲007 契約書.pdf")
    record = _record(path)
    prelim = PlannedCopy(record=record, output_filename=path.name, stamp_label="甲007")
    item = ReviewItem(preliminary=prelim, preview_record=record, batch_dir=tmp_path)

    dialog = WriteReviewDialog(root, item, 1, 1, _FILENAME_CONFIG, _STAMP_CONFIG)
    root.update()

    initial_top = dialog._stamp_margin_top_mm
    initial_right = dialog._stamp_margin_right_mm
    initial_photo = dialog._preview_photo

    dialog._adjust_stamp_position(dy=1)  # 下方向へ移動（margin_top_mmが増える）
    root.update()

    assert dialog._stamp_margin_top_mm == initial_top + 2.0
    assert dialog._preview_photo is not None
    assert dialog._preview_photo is not initial_photo

    dialog._adjust_stamp_position(dx=1)  # 右方向へ移動（margin_right_mmが減る）
    root.update()

    assert dialog._stamp_margin_right_mm == initial_right - 2.0

    dialog._on_confirm()

    assert dialog.result.stamp_margin_top_mm == initial_top + 2.0
    assert dialog.result.stamp_margin_right_mm == initial_right - 2.0


def test_reset_position_restores_config_default(root: tk.Tk, tmp_path: Path):
    path = make_a4_pdf(tmp_path / "甲008 契約書.pdf")
    record = _record(path)
    prelim = PlannedCopy(record=record, output_filename=path.name, stamp_label="甲008")
    item = ReviewItem(preliminary=prelim, preview_record=record, batch_dir=tmp_path)

    dialog = WriteReviewDialog(root, item, 1, 1, _FILENAME_CONFIG, _STAMP_CONFIG)
    root.update()

    dialog._adjust_stamp_position(dy=1, dx=1)
    root.update()
    assert dialog._stamp_margin_top_mm != _STAMP_CONFIG.margin_top_mm

    dialog._reset_stamp_position()
    root.update()

    assert dialog._stamp_margin_top_mm == _STAMP_CONFIG.margin_top_mm
    assert dialog._stamp_margin_right_mm == _STAMP_CONFIG.margin_right_mm

    dialog.destroy()


def test_position_does_not_go_negative(root: tk.Tk, tmp_path: Path):
    path = make_a4_pdf(tmp_path / "甲009 契約書.pdf")
    record = _record(path)
    prelim = PlannedCopy(record=record, output_filename=path.name, stamp_label="甲009")
    item = ReviewItem(preliminary=prelim, preview_record=record, batch_dir=tmp_path)

    small_config = EvidenceStampConfig(font_size_pt=14, margin_top_mm=1, margin_right_mm=1)
    dialog = WriteReviewDialog(root, item, 1, 1, _FILENAME_CONFIG, small_config)
    root.update()

    dialog._adjust_stamp_position(dy=-5)  # 上方向へ大きく移動しようとしても0未満にはならない
    root.update()

    assert dialog._stamp_margin_top_mm == 0.0

    dialog.destroy()


def test_preview_toggle_not_wired_when_no_stamp_option(root: tk.Tk, tmp_path: Path):
    # 証拠番号処理の対象外ファイル（stamp_labelなし）では、チェックボックス
    # 自体が存在しないため、プレビューは常にスタンプなしの状態のまま。
    path = make_a4_pdf(tmp_path / "準備書面1.pdf")
    record = _record(path)
    prelim = PlannedCopy(record=record, output_filename=path.name)
    item = ReviewItem(preliminary=prelim, preview_record=record, batch_dir=tmp_path)

    dialog = WriteReviewDialog(root, item, 1, 1, _FILENAME_CONFIG, _STAMP_CONFIG)
    root.update()

    assert "stamp" not in dialog._choice_vars
    assert dialog._preview_photo is not None

    dialog.destroy()


def test_page_nav_hidden_for_single_page_file(root: tk.Tk, tmp_path: Path):
    path = make_a4_pdf(tmp_path / "甲010 契約書.pdf")
    record = _record(path)
    prelim = PlannedCopy(record=record, output_filename=path.name)
    item = ReviewItem(preliminary=prelim, preview_record=record, batch_dir=tmp_path)

    dialog = WriteReviewDialog(root, item, 1, 1, _FILENAME_CONFIG, _STAMP_CONFIG)
    root.update()

    assert dialog._page_count == 1
    assert dialog._page_label is None
    assert dialog._prev_page_button is None
    assert dialog._next_page_button is None

    dialog.destroy()


def test_page_nav_shown_and_navigable_for_multipage_file(root: tk.Tk, tmp_path: Path):
    path = make_multipage_pdf(tmp_path / "甲011 契約書.pdf", page_count=3)
    record = _record(path)
    prelim = PlannedCopy(record=record, output_filename=path.name)
    item = ReviewItem(preliminary=prelim, preview_record=record, batch_dir=tmp_path)

    dialog = WriteReviewDialog(root, item, 1, 1, _FILENAME_CONFIG, _STAMP_CONFIG)
    root.update()

    assert dialog._page_count == 3
    assert dialog._page_index == 0
    assert str(dialog._prev_page_button["state"]) == "disabled"
    assert str(dialog._next_page_button["state"]) == "normal"
    initial_photo = dialog._preview_photo

    dialog._change_page(1)
    root.update()

    assert dialog._page_index == 1
    assert str(dialog._prev_page_button["state"]) == "normal"
    assert dialog._preview_photo is not None
    assert dialog._preview_photo is not initial_photo

    dialog._change_page(1)
    root.update()

    assert dialog._page_index == 2
    assert str(dialog._next_page_button["state"]) == "disabled"

    # 最終ページを超えて進もうとしても範囲外には出ない。
    dialog._change_page(1)
    root.update()
    assert dialog._page_index == 2

    dialog.destroy()


def test_rotate_direction_defaults_to_left(root: tk.Tk, tmp_path: Path):
    """向き補正の回転方向の既定は「左に90°」（=270。時計回り表現）。"""
    from fixtures.generate_fixtures import make_a4_landscape_pdf
    from mints_prep.models import PdfFinding

    path = make_a4_landscape_pdf(tmp_path / "甲011 横置き.pdf")
    record = _record(
        path,
        pdf_finding=PdfFinding(
            has_password=False,
            page_size_judgments=("A4",),
            page_orientations=("横",),
            orientation_mismatch_pages=(1,),
        ),
    )
    prelim = PlannedCopy(record=record, output_filename=path.name)
    item = ReviewItem(preliminary=prelim, preview_record=record, batch_dir=tmp_path)

    dialog = WriteReviewDialog(root, item, 1, 1, _FILENAME_CONFIG, _STAMP_CONFIG)
    root.update()

    assert "rotate" in dialog._choice_vars
    assert dialog._rotate_degrees_var.get() == 270

    dialog._choice_vars["rotate"].set(True)
    root.update()
    dialog._on_confirm()

    assert dialog.result.rotate_degrees == 270


def _merge_item(tmp_path: Path, names: list[str]) -> ReviewItem:
    from mints_prep.processors.evidence_merger import find_branch_merge_groups
    from mints_prep.processors.output_writer import plan_output_filenames
    from mints_prep.config import EvidenceNumberConfig

    records = [_record(make_a4_pdf(tmp_path / n)) for n in names]
    groups = find_branch_merge_groups(
        records, EvidenceNumberConfig(symbols=("甲", "乙", "丙"), digits=3)
    )
    planned = plan_output_filenames(
        [], {}, merge_groups=groups, stamp_merge_sources=True, filename_config=_FILENAME_CONFIG
    )
    prelim = planned[0]
    return ReviewItem(
        preliminary=prelim,
        preview_record=prelim.merge_group.representative.record,
        batch_dir=tmp_path,
    )


def test_merge_group_preview_navigates_all_members(root: tk.Tk, tmp_path: Path):
    """結合グループのプレビューが代表メンバー1件に固定され、2件目以降の
    重複スタンプ目視確認ができなかった不具合の回帰テスト：「前のファイル／
    次のファイル」でメンバーを切り替えられ、各メンバー自身の枝番ラベルが
    プレビュー対象になる。"""
    item = _merge_item(tmp_path, ["甲001-1 領収書.pdf", "甲001-2 領収書.pdf", "甲001-3 領収書.pdf"])

    dialog = WriteReviewDialog(root, item, 1, 1, _FILENAME_CONFIG, _STAMP_CONFIG)
    root.update()

    assert len(dialog._members) == 3
    assert dialog._member_index == 0
    assert dialog._current_record().path.name == "甲001-1 領収書.pdf"
    assert dialog._current_stamp_label() == "甲001-1"

    dialog._change_member(1)
    root.update()
    assert dialog._member_index == 1
    assert dialog._current_record().path.name == "甲001-2 領収書.pdf"
    assert dialog._current_stamp_label() == "甲001-2"

    dialog._change_member(1)
    root.update()
    assert dialog._current_stamp_label() == "甲001-3"
    # 末尾を超えては進まない
    dialog._change_member(1)
    assert dialog._member_index == 2

    dialog._on_cancel()


def test_single_file_has_no_member_navigation(root: tk.Tk, tmp_path: Path):
    path = make_a4_pdf(tmp_path / "甲005 契約書.pdf")
    record = _record(path)
    prelim = PlannedCopy(record=record, output_filename=path.name)
    item = ReviewItem(preliminary=prelim, preview_record=record, batch_dir=tmp_path)

    dialog = WriteReviewDialog(root, item, 1, 1, _FILENAME_CONFIG, _STAMP_CONFIG)
    root.update()

    assert len(dialog._members) == 1
    assert dialog._member_label is None  # ファイル送りUIは表示されない

    dialog._on_cancel()
