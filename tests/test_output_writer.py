from pathlib import Path

import pdfplumber
import pikepdf
from pypdf import PdfReader

from fixtures.generate_fixtures import make_a4_pdf
from mints_prep.config import EvidenceNumberConfig, EvidenceStampConfig, FilenameConfig
from mints_prep.models import Classification, FileRecord, FilenameFinding, FormatStatus, MetadataFinding
from mints_prep.processors.evidence_merger import find_branch_merge_groups
from mints_prep.processors.output_writer import (
    PlannedCopy,
    copy_to_output,
    find_existing_conflicts,
    plan_output_filenames,
    select_eligible_records,
)

_STAMP_CONFIG = EvidenceStampConfig(font_size_pt=10, margin_top_mm=8, margin_right_mm=8)
_EVIDENCE_CONFIG = EvidenceNumberConfig(symbols=("甲", "乙", "丙"), digits=3)
_FILENAME_CONFIG = FilenameConfig(
    max_length=100,
    warning_length=50,
    forbidden_chars=("\\", "/", ":", "*", "?", '"', "<", ">", "|"),
    jis_x0213_codec="shift_jis_2004",
)


def _record(name: str, classification: Classification, format_status=FormatStatus.RECORD_READY) -> FileRecord:
    return FileRecord(
        path=Path(name),
        size_bytes=100,
        format_status=format_status,
        detected_format="PDF",
        classification=classification,
        filename_finding=FilenameFinding(
            length=len(name), exceeds_max_length=False, exceeds_warning_length=False
        ),
    )


def test_eligible_records_include_only_a_and_b_record_ready(tmp_path: Path):
    records = [
        _record("a.pdf", Classification.A_COMPLIANT),
        _record("b.pdf", Classification.B_NEEDS_FIX),
        _record("c.docx", Classification.C_NEEDS_CONVERSION, format_status=FormatStatus.CONVERTIBLE),
        _record("d.zip", Classification.D_UNSUPPORTED, format_status=FormatStatus.UNSUPPORTED),
    ]
    eligible = select_eligible_records(records)
    names = {r.path.name for r in eligible}
    assert names == {"a.pdf", "b.pdf"}


def test_plan_uses_sanitized_name_when_provided():
    records = [_record("bad<name>.pdf", Classification.B_NEEDS_FIX)]
    plan = plan_output_filenames(records, {Path("bad<name>.pdf"): "bad_name_.pdf"})
    assert plan[0].output_filename == "bad_name_.pdf"


def test_plan_keeps_original_name_when_not_sanitized():
    records = [_record("甲001 適合済み.pdf", Classification.A_COMPLIANT)]
    plan = plan_output_filenames(records, {})
    assert plan[0].output_filename == "甲001 適合済み.pdf"


def test_plan_resolves_name_collisions():
    records = [
        _record("dup.pdf", Classification.A_COMPLIANT),
        _record("dup.pdf", Classification.A_COMPLIANT),
        _record("dup.pdf", Classification.A_COMPLIANT),
    ]
    plan = plan_output_filenames(records, {})
    names = [p.output_filename for p in plan]
    assert names == ["dup.pdf", "dup_2.pdf", "dup_3.pdf"]
    assert len(set(names)) == 3


def test_plan_avoids_collision_with_preexisting_name():
    records = [
        _record("dup.pdf", Classification.A_COMPLIANT),
        _record("dup_2.pdf", Classification.A_COMPLIANT),
        _record("dup.pdf", Classification.A_COMPLIANT),
    ]
    plan = plan_output_filenames(records, {})
    names = [p.output_filename for p in plan]
    assert names == ["dup.pdf", "dup_2.pdf", "dup_3.pdf"]
    assert len(set(names)) == 3


def test_plan_preserves_strip_metadata_flag_through_collision_resolution():
    records = [
        _record("dup.pdf", Classification.A_COMPLIANT),
        _record("dup.pdf", Classification.B_NEEDS_FIX),
    ]
    plan = plan_output_filenames(
        records, {}, strip_metadata_paths={Path("dup.pdf")}
    )
    # 2件ともstrip_metadata_paths指定のPath("dup.pdf")と一致するため、
    # 衝突解決でファイル名が変わってもフラグは維持されること。
    assert plan[0].output_filename == "dup.pdf"
    assert plan[0].strip_metadata is True
    assert plan[1].output_filename == "dup_2.pdf"
    assert plan[1].strip_metadata is True


def test_plan_strip_metadata_defaults_to_false_when_not_specified():
    records = [_record("a.pdf", Classification.A_COMPLIANT)]
    plan = plan_output_filenames(records, {})
    assert plan[0].strip_metadata is False


def test_find_existing_conflicts_detects_preexisting_files(tmp_path: Path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "a.pdf").write_bytes(b"already here")

    records = [
        _record("a.pdf", Classification.A_COMPLIANT),
        _record("b.pdf", Classification.A_COMPLIANT),
    ]
    plan = plan_output_filenames(records, {})

    conflicts = find_existing_conflicts(plan, out_dir)

    assert conflicts == ["a.pdf"]


def test_find_existing_conflicts_empty_when_no_overlap(tmp_path: Path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "unrelated.pdf").write_bytes(b"something else")

    records = [_record("a.pdf", Classification.A_COMPLIANT)]
    plan = plan_output_filenames(records, {})

    conflicts = find_existing_conflicts(plan, out_dir)

    assert conflicts == []


def test_find_existing_conflicts_empty_when_output_dir_empty(tmp_path: Path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    records = [_record("a.pdf", Classification.A_COMPLIANT)]
    plan = plan_output_filenames(records, {})

    assert find_existing_conflicts(plan, out_dir) == []


def test_copy_to_output_strips_metadata_when_flagged(tmp_path: Path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    src_file = make_a4_pdf(src_dir / "with_meta.pdf", with_metadata=True)
    with pikepdf.open(src_file) as pdf:
        assert "/Author" in pdf.docinfo.keys()

    record = FileRecord(
        path=src_file,
        size_bytes=src_file.stat().st_size,
        format_status=FormatStatus.RECORD_READY,
        detected_format="PDF",
        classification=Classification.A_COMPLIANT,
        filename_finding=FilenameFinding(
            length=len(src_file.name), exceeds_max_length=False, exceeds_warning_length=False
        ),
    )
    plan = plan_output_filenames([record], {}, strip_metadata_paths={src_file})

    copy_to_output(plan, out_dir)

    dest = out_dir / src_file.name
    with pikepdf.open(dest) as pdf:
        keys = [str(k) for k in pdf.docinfo.keys()] if pdf.docinfo else []
        assert "/Author" not in keys
    # 元ファイルは変更されない
    with pikepdf.open(src_file) as pdf:
        assert "/Author" in pdf.docinfo.keys()


def test_copy_to_output_writes_files_with_planned_names(tmp_path: Path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    src_file = src_dir / "original.pdf"
    src_file.write_bytes(b"pdf content")

    record = FileRecord(
        path=src_file,
        size_bytes=len(b"pdf content"),
        format_status=FormatStatus.RECORD_READY,
        detected_format="PDF",
        classification=Classification.A_COMPLIANT,
        filename_finding=FilenameFinding(
            length=len(src_file.name), exceeds_max_length=False, exceeds_warning_length=False
        ),
    )
    plan = plan_output_filenames([record], {src_file: "甲001 契約書.pdf"})

    written = copy_to_output(plan, out_dir)

    assert written == [out_dir / "甲001 契約書.pdf"]
    assert (out_dir / "甲001 契約書.pdf").read_bytes() == b"pdf content"
    # 元ファイルは変更されない
    assert src_file.read_bytes() == b"pdf content"
    assert src_file.exists()


def test_copy_to_output_stamps_pdf_when_label_set(tmp_path: Path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    src_file = make_a4_pdf(src_dir / "甲001 契約書.pdf")

    record = FileRecord(
        path=src_file,
        size_bytes=src_file.stat().st_size,
        format_status=FormatStatus.RECORD_READY,
        detected_format="PDF",
        classification=Classification.A_COMPLIANT,
        filename_finding=FilenameFinding(
            length=len(src_file.name), exceeds_max_length=False, exceeds_warning_length=False
        ),
    )
    plan = plan_output_filenames([record], {}, stamp_labels={src_file: "甲001"})

    copy_to_output(plan, out_dir, _STAMP_CONFIG)

    dest = out_dir / "甲001 契約書.pdf"
    assert "甲001" in PdfReader(str(dest)).pages[0].extract_text()
    # 元ファイルは変更されない
    assert "甲001" not in PdfReader(str(src_file)).pages[0].extract_text()


def test_copy_to_output_stamp_position_override_moves_stamp(tmp_path: Path):
    """要件定義書4.7「スタンプ位置調整」：PlannedCopyの上書き値がこのファイル
    のみに適用され、config.yamlの既定値（_STAMP_CONFIG）自体は変更されない。
    """
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    src_file = make_a4_pdf(src_dir / "甲001 契約書.pdf")

    record = FileRecord(
        path=src_file,
        size_bytes=src_file.stat().st_size,
        format_status=FormatStatus.RECORD_READY,
        detected_format="PDF",
        classification=Classification.A_COMPLIANT,
        filename_finding=FilenameFinding(
            length=len(src_file.name), exceeds_max_length=False, exceeds_warning_length=False
        ),
    )

    default_item = PlannedCopy(record=record, output_filename="default.pdf", stamp_label="甲001")
    overridden_item = PlannedCopy(
        record=record,
        output_filename="overridden.pdf",
        stamp_label="甲001",
        stamp_margin_top_mm=40,
        stamp_margin_right_mm=40,
    )

    copy_to_output([default_item], out_dir, _STAMP_CONFIG)
    copy_to_output([overridden_item], out_dir, _STAMP_CONFIG)

    def _stamp_top(path: Path) -> float:
        with pdfplumber.open(path) as pdf:
            word = next(w for w in pdf.pages[0].extract_words() if w["text"] == "甲001")
            return word["top"]

    default_top = _stamp_top(out_dir / "default.pdf")
    overridden_top = _stamp_top(out_dir / "overridden.pdf")

    # margin_top_mmが大きいほど上端からの余白が増える（＝ページ上端からの
    # 距離＝topの値が大きくなる、下寄りの位置になる）。
    assert overridden_top > default_top
    # config.yamlの既定値（呼び出し側が渡したEvidenceStampConfig自体）は
    # 変更されないままであることを確認する。
    assert _STAMP_CONFIG.margin_top_mm == 8
    assert _STAMP_CONFIG.margin_right_mm == 8


def test_copy_to_output_stamp_then_strips_metadata(tmp_path: Path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    src_file = make_a4_pdf(src_dir / "甲001 契約書.pdf", with_metadata=True)

    record = FileRecord(
        path=src_file,
        size_bytes=src_file.stat().st_size,
        format_status=FormatStatus.RECORD_READY,
        detected_format="PDF",
        classification=Classification.A_COMPLIANT,
        filename_finding=FilenameFinding(
            length=len(src_file.name), exceeds_max_length=False, exceeds_warning_length=False
        ),
    )
    plan = plan_output_filenames(
        [record], {}, strip_metadata_paths={src_file}, stamp_labels={src_file: "甲001"}
    )

    copy_to_output(plan, out_dir, _STAMP_CONFIG)

    dest = out_dir / "甲001 契約書.pdf"
    assert "甲001" in PdfReader(str(dest)).pages[0].extract_text()
    with pikepdf.open(dest) as pdf:
        keys = [str(k) for k in pdf.docinfo.keys()] if pdf.docinfo else []
        assert "/Author" not in keys
    # 一時ファイルが残らないこと
    assert not (out_dir / (dest.name + ".stamp_tmp")).exists()


def test_copy_to_output_writes_merged_branch_group(tmp_path: Path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    def _record_for(path: Path) -> FileRecord:
        return FileRecord(
            path=path,
            size_bytes=path.stat().st_size,
            format_status=FormatStatus.RECORD_READY,
            detected_format="PDF",
            classification=Classification.A_COMPLIANT,
            filename_finding=FilenameFinding(
                length=len(path.name), exceeds_max_length=False, exceeds_warning_length=False
            ),
        )

    records = [
        _record_for(make_a4_pdf(src_dir / "甲001-1 領収書.pdf")),
        _record_for(make_a4_pdf(src_dir / "甲001-2 領収書.pdf")),
    ]
    groups = find_branch_merge_groups(records, _EVIDENCE_CONFIG)
    assert len(groups) == 1

    plan = plan_output_filenames(
        [], {}, merge_groups=groups, stamp_merge_sources=True, filename_config=_FILENAME_CONFIG
    )

    written = copy_to_output(plan, out_dir, _STAMP_CONFIG)

    assert written == [out_dir / "甲001-1~2 領収書.pdf"]
    merged = PdfReader(str(written[0]))
    assert len(merged.pages) == 2
    assert "甲001-1" in merged.pages[0].extract_text()
    assert "甲001-2" in merged.pages[1].extract_text()


def test_copy_to_output_strips_metadata_from_merged_branch_group(tmp_path: Path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    def _record_with_metadata(path: Path) -> FileRecord:
        make_a4_pdf(path, with_metadata=True)
        return FileRecord(
            path=path,
            size_bytes=path.stat().st_size,
            format_status=FormatStatus.RECORD_READY,
            detected_format="PDF",
            classification=Classification.A_COMPLIANT,
            filename_finding=FilenameFinding(
                length=len(path.name), exceeds_max_length=False, exceeds_warning_length=False
            ),
            metadata_finding=MetadataFinding(has_metadata=True, detected_fields=("Author", "Title")),
        )

    records = [
        _record_with_metadata(src_dir / "甲001-1 領収書.pdf"),
        _record_with_metadata(src_dir / "甲001-2 領収書.pdf"),
    ]
    groups = find_branch_merge_groups(records, _EVIDENCE_CONFIG)
    assert len(groups) == 1

    # 結合グループへのメタデータ除去はPlannedCopy.strip_metadataで直接指定する
    # （逐次レビュー画面がチェックボックスON/OFFから最終的なPlannedCopyを
    # 組み立てる際の形を模す）。
    plan = plan_output_filenames([], {}, merge_groups=groups, filename_config=_FILENAME_CONFIG)
    merged_item = plan[0]
    plan[0] = PlannedCopy(
        record=merged_item.record,
        output_filename=merged_item.output_filename,
        strip_metadata=True,
        merge_group=merged_item.merge_group,
        merge_stamp=merged_item.merge_stamp,
    )

    written = copy_to_output(plan, out_dir, _STAMP_CONFIG)

    dest = written[0]
    with pikepdf.open(dest) as pdf:
        keys = [str(k) for k in pdf.docinfo.keys()] if pdf.docinfo else []
        assert "/Author" not in keys
    assert not (out_dir / (dest.name + ".merge_tmp")).exists()


def test_synthetic_merge_record_aggregates_metadata_from_members(tmp_path: Path):
    def _record_with_metadata(path: Path, has_metadata: bool, fields: tuple[str, ...]) -> FileRecord:
        make_a4_pdf(path)
        return FileRecord(
            path=path,
            size_bytes=path.stat().st_size,
            format_status=FormatStatus.RECORD_READY,
            detected_format="PDF",
            classification=Classification.A_COMPLIANT,
            filename_finding=FilenameFinding(
                length=len(path.name), exceeds_max_length=False, exceeds_warning_length=False
            ),
            metadata_finding=MetadataFinding(has_metadata=has_metadata, detected_fields=fields),
        )

    records = [
        _record_with_metadata(tmp_path / "甲001-1 領収書.pdf", False, ()),
        _record_with_metadata(tmp_path / "甲001-2 領収書.pdf", True, ("Author",)),
    ]
    groups = find_branch_merge_groups(records, _EVIDENCE_CONFIG)
    assert len(groups) == 1

    plan = plan_output_filenames(
        [], {}, merge_groups=groups, filename_config=_FILENAME_CONFIG
    )

    merged_record = plan[0].record
    assert merged_record.metadata_finding.has_metadata is True
    assert merged_record.metadata_finding.detected_fields == ("Author",)




def test_collision_rename_stays_within_max_length():
    """整形で100文字ちょうどへ切り詰めた名前同士が衝突した場合、連番付与後も
    100文字上限に収まる（整形機能自身が違反名を作る不具合の回帰テスト）。"""
    long_stem = "あ" * 96  # 96 + ".pdf" = 100文字
    name = long_stem + ".pdf"
    records = [
        _record(name, Classification.A_COMPLIANT),
        _record(name, Classification.A_COMPLIANT),
    ]
    plan = plan_output_filenames(records, {}, filename_config=_FILENAME_CONFIG)

    assert len(plan[0].output_filename) <= _FILENAME_CONFIG.max_length
    assert len(plan[1].output_filename) <= _FILENAME_CONFIG.max_length
    assert plan[0].output_filename != plan[1].output_filename
    assert plan[1].renamed_for_collision is True


def test_collision_detected_case_insensitively():
    """拡張子の大小違い（.PDF/.pdf）はNTFSでは同名＝上書きになるため、
    衝突として検出し連番を付ける（casefoldキーの回帰テスト）。"""
    records = [
        _record("dup.PDF", Classification.A_COMPLIANT),
        _record("dup.pdf", Classification.A_COMPLIANT),
    ]
    plan = plan_output_filenames(records, {})

    assert plan[0].output_filename.casefold() != plan[1].output_filename.casefold()


def test_sanitized_flag_set_when_sanitize_applied():
    records = [_record("bad<name>.pdf", Classification.B_NEEDS_FIX)]
    plan = plan_output_filenames(records, {Path("bad<name>.pdf"): "bad_name_.pdf"})
    assert plan[0].filename_sanitized is True

    plan2 = plan_output_filenames([_record("ok.pdf", Classification.A_COMPLIANT)], {})
    assert plan2[0].filename_sanitized is False


def test_copy_to_output_rotates_mismatched_pages(tmp_path: Path, config):
    """向き補正（rotate_degrees）：向き不一致ページを回転して書き出し、
    再検出で不一致が解消する。元ファイルは変更しない。"""
    from fixtures.generate_fixtures import make_a4_landscape_pdf
    from mints_prep.detectors.pdf_detector import detect_pdf

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    src_file = make_a4_landscape_pdf(src_dir / "甲001 横置き.pdf")
    finding = detect_pdf(src_file, config.pdf)
    assert finding.orientation_mismatch_pages == (1,)

    record = FileRecord(
        path=src_file,
        size_bytes=src_file.stat().st_size,
        format_status=FormatStatus.RECORD_READY,
        detected_format="PDF",
        classification=Classification.B_NEEDS_FIX,
        filename_finding=FilenameFinding(
            length=len(src_file.name), exceeds_max_length=False, exceeds_warning_length=False
        ),
        pdf_finding=finding,
    )
    item = PlannedCopy(record=record, output_filename=src_file.name, rotate_degrees=90)

    written = copy_to_output([item], out_dir)

    after = detect_pdf(written[0], config.pdf)
    assert after.orientation_mismatch_pages == ()
    assert after.page_orientations == ("縦",)
    # 元ファイルは変更されない
    assert detect_pdf(src_file, config.pdf).orientation_mismatch_pages == (1,)
    # 一時ファイルが残らない
    assert not (out_dir / (src_file.name + ".rotate_tmp")).exists()


def test_copy_to_output_rotate_then_stamp_then_strip(tmp_path: Path, config):
    """向き補正→スタンプ→メタデータ除去の複合：スタンプは回転後の右上に載り、
    メタデータも除去される。"""
    from fixtures.generate_fixtures import make_a4_landscape_pdf
    from mints_prep.detectors.pdf_detector import detect_pdf

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    src_file = make_a4_landscape_pdf(src_dir / "甲001 横置き.pdf")
    finding = detect_pdf(src_file, config.pdf)

    record = FileRecord(
        path=src_file,
        size_bytes=src_file.stat().st_size,
        format_status=FormatStatus.RECORD_READY,
        detected_format="PDF",
        classification=Classification.B_NEEDS_FIX,
        filename_finding=FilenameFinding(
            length=len(src_file.name), exceeds_max_length=False, exceeds_warning_length=False
        ),
        pdf_finding=finding,
    )
    item = PlannedCopy(
        record=record,
        output_filename=src_file.name,
        rotate_degrees=90,
        stamp_label="甲001",
        strip_metadata=True,
    )

    written = copy_to_output([item], out_dir, _STAMP_CONFIG)

    dest = written[0]
    after = detect_pdf(dest, config.pdf)
    assert after.orientation_mismatch_pages == ()
    assert "甲001" in PdfReader(str(dest)).pages[0].extract_text()
    with pikepdf.open(dest) as pdf:
        keys = [str(k) for k in pdf.docinfo.keys()] if pdf.docinfo else []
        assert "/Author" not in keys
