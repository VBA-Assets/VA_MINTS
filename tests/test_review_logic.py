from pathlib import Path

from fixtures.generate_fixtures import make_a4_pdf
from mints_prep.config import EvidenceNumberConfig, FilenameConfig
from mints_prep.models import Classification, FileRecord, FilenameFinding, FormatStatus, MetadataFinding
from mints_prep.processors.evidence_merger import find_branch_merge_groups
from mints_prep.processors.output_writer import PlannedCopy, plan_output_filenames
from mints_prep.ui.review_logic import ReviewItem, apply_review_choices, build_review_options

_FILENAME_CONFIG = FilenameConfig(
    max_length=100,
    warning_length=50,
    forbidden_chars=("\\", "/", ":", "*", "?", '"', "<", ">", "|"),
    jis_x0213_codec="shift_jis_2004",
)
_EVIDENCE_CONFIG = EvidenceNumberConfig(symbols=("甲", "乙", "丙"), digits=3)


def _record(
    name: str,
    exceeds_max_length: bool = False,
    forbidden_chars_found: tuple[str, ...] = (),
    has_metadata: bool = False,
    detected_format: str = "PDF",
) -> FileRecord:
    return FileRecord(
        path=Path(name),
        size_bytes=100,
        format_status=FormatStatus.RECORD_READY,
        detected_format=detected_format,
        classification=Classification.A_COMPLIANT,
        filename_finding=FilenameFinding(
            length=len(name),
            exceeds_max_length=exceeds_max_length,
            exceeds_warning_length=False,
            forbidden_chars_found=forbidden_chars_found,
        ),
        metadata_finding=MetadataFinding(has_metadata=has_metadata, detected_fields=("Author",) if has_metadata else ()),
    )


def _item(prelim: PlannedCopy) -> ReviewItem:
    return ReviewItem(preliminary=prelim, preview_record=prelim.record, batch_dir=Path("out"))


def test_build_review_options_includes_disabled_info_item_for_unfixable_detection():
    """本ツールでは対処できない検出事項（パスワード等）は、グレーアウト
    （enabled=False）の情報項目としてチェックボックス一覧に含める。"""
    from mints_prep.models import PdfFinding

    record = FileRecord(
        path=Path("甲001 登記情報.pdf"),
        size_bytes=100,
        format_status=FormatStatus.RECORD_READY,
        detected_format="PDF",
        classification=Classification.B_NEEDS_FIX,
        filename_finding=FilenameFinding(
            length=len("甲001 登記情報.pdf"), exceeds_max_length=False, exceeds_warning_length=False
        ),
        pdf_finding=PdfFinding(has_password=False, has_permission_restriction=True),
    )
    prelim = PlannedCopy(record=record, output_filename="甲001 登記情報.pdf")

    options = build_review_options(_item(prelim), _FILENAME_CONFIG)

    info_opts = [o for o in options if not o.enabled]
    assert len(info_opts) == 1
    # 対処不可であることが伝わるよう、OFF状態でグレーアウト表示する。
    assert info_opts[0].default_checked is False
    assert "印刷・コピー" in info_opts[0].label


def test_info_item_for_single_file_omits_filename_prefix():
    """逐次レビュー画面は1ファイルずつの表示でファイル名は画面上部に出ている
    ため、単独ファイルの検出項目にはファイル名を重複表示しない（レポート用の
    「・ファイル名：内容」形式から接頭辞を除去する回帰テスト）。"""
    from mints_prep.models import PdfFinding

    name = "甲001 登記情報.pdf"
    record = FileRecord(
        path=Path(name),
        size_bytes=100,
        format_status=FormatStatus.RECORD_READY,
        detected_format="PDF",
        classification=Classification.B_NEEDS_FIX,
        filename_finding=FilenameFinding(
            length=len(name), exceeds_max_length=False, exceeds_warning_length=False
        ),
        pdf_finding=PdfFinding(has_password=False, has_permission_restriction=True),
    )
    prelim = PlannedCopy(record=record, output_filename=name)

    options = build_review_options(_item(prelim), _FILENAME_CONFIG)

    info_opts = [o for o in options if not o.enabled]
    assert len(info_opts) == 1
    assert name not in info_opts[0].label
    assert not info_opts[0].label.startswith("・")
    assert "印刷・コピー" in info_opts[0].label


def test_build_review_options_actionable_items_stay_enabled():
    record = _record("bad<name>.pdf", forbidden_chars_found=("<", ">"))
    prelim = PlannedCopy(record=record, output_filename="bad_name_.pdf")

    options = build_review_options(_item(prelim), _FILENAME_CONFIG)

    sanitize_opt = next(o for o in options if o.key == "sanitize")
    assert sanitize_opt.enabled is True


def test_build_review_options_lists_info_items_before_actionable_items():
    """検出のみ・対処不可の項目は一覧の先頭（実行可否を選べる項目より上）に
    並べる（画面側はこの順序をもとに区切り線の位置を決める）。"""
    from mints_prep.models import PdfFinding

    record = FileRecord(
        path=Path("bad<name>.pdf"),
        size_bytes=100,
        format_status=FormatStatus.RECORD_READY,
        detected_format="PDF",
        classification=Classification.B_NEEDS_FIX,
        filename_finding=FilenameFinding(
            length=len("bad<name>.pdf"),
            exceeds_max_length=False,
            exceeds_warning_length=False,
            forbidden_chars_found=("<", ">"),
        ),
        pdf_finding=PdfFinding(has_password=True),
    )
    prelim = PlannedCopy(record=record, output_filename="bad_name_.pdf")

    options = build_review_options(_item(prelim), _FILENAME_CONFIG)

    enabled_flags = [o.enabled for o in options]
    assert enabled_flags == [False, True], "検出のみの項目が先頭、実行可否を選べる項目が後続であるべき"


def test_build_review_options_omits_info_items_when_nothing_unfixable_detected():
    record = _record("ok.pdf")
    prelim = PlannedCopy(record=record, output_filename="ok.pdf")

    options = build_review_options(_item(prelim), _FILENAME_CONFIG)

    assert all(o.enabled for o in options)


def test_build_review_options_for_merge_group_attributes_info_items_per_member(tmp_path: Path):
    """結合グループの各メンバーで検出内容が異なる場合、どのファイル由来かが
    分かる形でそれぞれ情報項目として列挙されること。"""
    from mints_prep.models import PdfFinding

    member1 = _record_with_real_file(tmp_path, "甲001-1 領収書.pdf")
    member2_path = make_a4_pdf(tmp_path / "甲001-2 領収書.pdf")
    member2 = FileRecord(
        path=member2_path,
        size_bytes=member2_path.stat().st_size,
        format_status=FormatStatus.RECORD_READY,
        detected_format="PDF",
        classification=Classification.B_NEEDS_FIX,
        filename_finding=FilenameFinding(
            length=len("甲001-2 領収書.pdf"), exceeds_max_length=False, exceeds_warning_length=False
        ),
        pdf_finding=PdfFinding(has_password=False, has_permission_restriction=True),
    )
    prelim = _merge_preliminary([member1, member2])

    options = build_review_options(_item(prelim), _FILENAME_CONFIG)

    info_opts = [o for o in options if not o.enabled]
    assert len(info_opts) == 1
    assert "甲001-2 領収書.pdf" in info_opts[0].label


def test_build_review_options_empty_when_nothing_applicable():
    record = _record("甲001 契約書.pdf")
    prelim = PlannedCopy(record=record, output_filename="甲001 契約書.pdf")

    options = build_review_options(_item(prelim), _FILENAME_CONFIG)

    assert options == []


def test_build_review_options_includes_sanitize_when_needed():
    record = _record("bad<name>.pdf", forbidden_chars_found=("<", ">"))
    prelim = PlannedCopy(record=record, output_filename="bad_name_.pdf")

    options = build_review_options(_item(prelim), _FILENAME_CONFIG)

    keys = [o.key for o in options]
    assert "sanitize" in keys
    sanitize_opt = next(o for o in options if o.key == "sanitize")
    assert sanitize_opt.default_checked is True
    assert "bad<name>.pdf" in sanitize_opt.label
    assert "bad_name_.pdf" in sanitize_opt.label


def test_build_review_options_includes_strip_metadata_when_needed():
    record = _record("甲001.pdf", has_metadata=True)
    prelim = PlannedCopy(record=record, output_filename="甲001.pdf", strip_metadata=True)

    options = build_review_options(_item(prelim), _FILENAME_CONFIG)

    opt = next(o for o in options if o.key == "strip_metadata")
    assert opt.default_checked is True
    assert "Author" in opt.label


def test_build_review_options_includes_stamp_for_standalone_evidence_match():
    record = _record("甲001 契約書.pdf")
    prelim = PlannedCopy(record=record, output_filename="甲001 契約書.pdf", stamp_label="甲001")

    options = build_review_options(_item(prelim), _FILENAME_CONFIG)

    opt = next(o for o in options if o.key == "stamp")
    assert opt.default_checked is True
    assert "甲001" in opt.label


def test_build_review_options_omits_stamp_when_no_evidence_match():
    record = _record("準備書面1.pdf")
    prelim = PlannedCopy(record=record, output_filename="準備書面1.pdf")

    options = build_review_options(_item(prelim), _FILENAME_CONFIG)

    assert all(o.key != "stamp" for o in options)


def test_build_review_options_stamp_defaults_off_for_image_formats():
    # PDF以外（JPEG/PNG）は、証拠番号スタンプの既定値をOFFとする
    # （写真等は原本の見た目そのものが証拠内容と直結し得るため）。
    record = _record("甲001 現場写真.jpg", detected_format="JPEG")
    prelim = PlannedCopy(record=record, output_filename="甲001 現場写真.jpg", stamp_label="甲001")

    options = build_review_options(_item(prelim), _FILENAME_CONFIG)

    opt = next(o for o in options if o.key == "stamp")
    assert opt.default_checked is False


def test_build_review_options_stamp_defaults_on_for_pdf():
    record = _record("甲001 契約書.pdf", detected_format="PDF")
    prelim = PlannedCopy(record=record, output_filename="甲001 契約書.pdf", stamp_label="甲001")

    options = build_review_options(_item(prelim), _FILENAME_CONFIG)

    opt = next(o for o in options if o.key == "stamp")
    assert opt.default_checked is True


def _merge_preliminary(records: list[FileRecord]) -> PlannedCopy:
    groups = find_branch_merge_groups(records, _EVIDENCE_CONFIG)
    planned = plan_output_filenames(
        [], {}, merge_groups=groups, stamp_merge_sources=True, filename_config=_FILENAME_CONFIG
    )
    return planned[0]


def test_build_review_options_for_merge_group_shows_branch_range(tmp_path: Path):
    records = [
        _record_with_real_file(tmp_path, "甲001-1 領収書.pdf"),
        _record_with_real_file(tmp_path, "甲001-2 領収書.pdf"),
    ]
    prelim = _merge_preliminary(records)

    options = build_review_options(_item(prelim), _FILENAME_CONFIG)

    opt = next(o for o in options if o.key == "stamp")
    assert "甲001-1" in opt.label and "甲001-2" in opt.label


def _record_with_real_file(tmp_path: Path, name: str) -> FileRecord:
    path = make_a4_pdf(tmp_path / name)
    return FileRecord(
        path=path,
        size_bytes=path.stat().st_size,
        format_status=FormatStatus.RECORD_READY,
        detected_format="PDF",
        classification=Classification.A_COMPLIANT,
        filename_finding=FilenameFinding(
            length=len(name), exceeds_max_length=False, exceeds_warning_length=False
        ),
    )


def test_apply_review_choices_all_checked_matches_preliminary():
    record = _record("bad<name>.pdf", forbidden_chars_found=("<", ">"), has_metadata=True)
    prelim = PlannedCopy(
        record=record, output_filename="bad_name_.pdf", strip_metadata=True, stamp_label="甲001"
    )

    final = apply_review_choices(
        _item(prelim), {"sanitize": True, "strip_metadata": True, "stamp": True}, _FILENAME_CONFIG
    )

    assert final.output_filename == "bad_name_.pdf"
    assert final.strip_metadata is True
    assert final.stamp_label == "甲001"


def test_apply_review_choices_all_unchecked_reverts_to_original():
    record = _record("bad<name>.pdf", forbidden_chars_found=("<", ">"), has_metadata=True)
    prelim = PlannedCopy(
        record=record, output_filename="bad_name_.pdf", strip_metadata=True, stamp_label="甲001"
    )

    final = apply_review_choices(_item(prelim), {}, _FILENAME_CONFIG)

    assert final.output_filename == "bad<name>.pdf"
    assert final.strip_metadata is False
    assert final.stamp_label is None


def test_apply_review_choices_stamp_offset_applied_when_stamp_on():
    record = _record("甲001 契約書.pdf")
    prelim = PlannedCopy(record=record, output_filename="甲001 契約書.pdf", stamp_label="甲001")

    final = apply_review_choices(
        _item(prelim), {"stamp": True}, _FILENAME_CONFIG, stamp_offset_mm=(20.0, 30.0)
    )

    assert final.stamp_margin_top_mm == 20.0
    assert final.stamp_margin_right_mm == 30.0


def test_apply_review_choices_stamp_offset_ignored_when_stamp_off():
    record = _record("甲001 契約書.pdf")
    prelim = PlannedCopy(record=record, output_filename="甲001 契約書.pdf", stamp_label="甲001")

    final = apply_review_choices(
        _item(prelim), {"stamp": False}, _FILENAME_CONFIG, stamp_offset_mm=(20.0, 30.0)
    )

    assert final.stamp_margin_top_mm is None
    assert final.stamp_margin_right_mm is None


def test_apply_review_choices_stamp_offset_defaults_to_none_when_not_supplied():
    record = _record("甲001 契約書.pdf")
    prelim = PlannedCopy(record=record, output_filename="甲001 契約書.pdf", stamp_label="甲001")

    final = apply_review_choices(_item(prelim), {"stamp": True}, _FILENAME_CONFIG)

    assert final.stamp_margin_top_mm is None
    assert final.stamp_margin_right_mm is None


def test_apply_review_choices_stamp_offset_applied_to_merge_group(tmp_path: Path):
    records = [
        _record_with_real_file(tmp_path, "甲001-1 領収書.pdf"),
        _record_with_real_file(tmp_path, "甲001-2 領収書.pdf"),
    ]
    prelim = _merge_preliminary(records)

    final = apply_review_choices(
        _item(prelim), {"stamp": True}, _FILENAME_CONFIG, stamp_offset_mm=(15.0, 25.0)
    )

    assert final.stamp_margin_top_mm == 15.0
    assert final.stamp_margin_right_mm == 25.0


def test_apply_review_choices_for_merge_group_toggles_merge_stamp(tmp_path: Path):
    records = [
        _record_with_real_file(tmp_path, "甲001-1 領収書.pdf"),
        _record_with_real_file(tmp_path, "甲001-2 領収書.pdf"),
    ]
    prelim = _merge_preliminary(records)

    final_on = apply_review_choices(_item(prelim), {"stamp": True}, _FILENAME_CONFIG)
    final_off = apply_review_choices(_item(prelim), {"stamp": False}, _FILENAME_CONFIG)

    assert final_on.merge_stamp is True
    assert final_off.merge_stamp is False
    assert final_on.merge_group is prelim.merge_group
    assert final_off.output_filename == prelim.output_filename


def _mismatch_record(name: str = "甲001 横置き.pdf"):
    from mints_prep.models import PdfFinding

    return FileRecord(
        path=Path(name),
        size_bytes=100,
        format_status=FormatStatus.RECORD_READY,
        detected_format="PDF",
        classification=Classification.B_NEEDS_FIX,
        filename_finding=FilenameFinding(
            length=len(name), exceeds_max_length=False, exceeds_warning_length=False
        ),
        pdf_finding=PdfFinding(
            has_password=False,
            page_size_judgments=("A4",),
            page_orientations=("横",),
            orientation_mismatch_pages=(1,),
        ),
    )


def test_rotate_option_offered_for_orientation_mismatch():
    """向き不一致の単独PDFには「向き補正」の実行可否チェックボックスを提示し、
    赤字の情報項目（検出のみ）としては表示しない。既定はOFF（回転方向は
    プレビューを見たユーザーが判断するため）。"""
    prelim = PlannedCopy(record=_mismatch_record(), output_filename="甲001 横置き.pdf")

    options = build_review_options(_item(prelim), _FILENAME_CONFIG)

    rotate_opt = next(o for o in options if o.key == "rotate")
    assert rotate_opt.enabled is True
    assert rotate_opt.default_checked is False
    assert "90°回転" in rotate_opt.label
    assert not any("向きが基本形と不一致" in o.label for o in options if not o.enabled)


def test_apply_review_choices_rotate_on_sets_degrees():
    prelim = PlannedCopy(record=_mismatch_record(), output_filename="甲001 横置き.pdf")

    final_right = apply_review_choices(
        _item(prelim), {"rotate": True}, _FILENAME_CONFIG, rotate_degrees=90
    )
    final_left = apply_review_choices(
        _item(prelim), {"rotate": True}, _FILENAME_CONFIG, rotate_degrees=270
    )
    final_off = apply_review_choices(_item(prelim), {"rotate": False}, _FILENAME_CONFIG)

    assert final_right.rotate_degrees == 90
    assert final_left.rotate_degrees == 270
    assert final_off.rotate_degrees is None


def test_rotate_option_not_offered_for_merge_groups(tmp_path: Path):
    """結合グループには向き補正を提供しない（メンバーの向き不一致は従来どおり
    検出のみの情報項目として表示される）。"""
    from mints_prep.models import PdfFinding

    member1 = _record_with_real_file(tmp_path, "甲001-1 領収書.pdf")
    p2 = make_a4_pdf(tmp_path / "甲001-2 領収書.pdf")
    member2 = FileRecord(
        path=p2,
        size_bytes=p2.stat().st_size,
        format_status=FormatStatus.RECORD_READY,
        detected_format="PDF",
        classification=Classification.B_NEEDS_FIX,
        filename_finding=FilenameFinding(
            length=len(p2.name), exceeds_max_length=False, exceeds_warning_length=False
        ),
        pdf_finding=PdfFinding(
            has_password=False,
            page_size_judgments=("A4",),
            page_orientations=("横",),
            orientation_mismatch_pages=(1,),
        ),
    )
    prelim = _merge_preliminary([member1, member2])

    options = build_review_options(_item(prelim), _FILENAME_CONFIG)

    assert all(o.key != "rotate" for o in options)
    assert any(
        not o.enabled and "向きが基本形と不一致" in o.label and "甲001-2" in o.label
        for o in options
    )
