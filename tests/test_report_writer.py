from pathlib import Path

from mints_prep.models import (
    Classification,
    FileRecord,
    FilenameFinding,
    FormatStatus,
    PiiFinding,
    RedactionFinding,
)
from mints_prep.processors.batch_planner import Batch
from mints_prep.processors.output_writer import PlannedCopy
from mints_prep.processors.report_writer import (
    REPORT_FILENAME,
    build_report_text,
    collect_check_warnings,
    collect_critical_warnings,
    format_evidence_sequence_warnings,
    write_report,
)


def _record(
    name: str,
    classification: Classification = Classification.A_COMPLIANT,
    format_status: FormatStatus = FormatStatus.RECORD_READY,
    detected_format: str = "PDF",
    size_bytes: int = 100,
    **kwargs,
) -> FileRecord:
    return FileRecord(
        path=Path(name),
        size_bytes=size_bytes,
        format_status=format_status,
        detected_format=detected_format,
        classification=classification,
        filename_finding=FilenameFinding(
            length=len(name), exceeds_max_length=False, exceeds_warning_length=False
        ),
        **kwargs,
    )


def test_report_includes_summary_counts():
    records = [
        _record("a.pdf", Classification.A_COMPLIANT),
        _record("b.pdf", Classification.B_NEEDS_FIX),
        _record("c.docx", Classification.C_NEEDS_CONVERSION, format_status=FormatStatus.CONVERTIBLE),
        _record("d.zip", Classification.D_UNSUPPORTED, format_status=FormatStatus.UNSUPPORTED),
    ]
    planned = [PlannedCopy(record=records[0], output_filename="a.pdf")]

    text = build_report_text(
        input_dir=Path("in"), output_dir=Path("out"), all_records=records, planned=planned, batches=[]
    )

    assert "合計 4 件" in text
    assert "A：適合済み 1 件" in text
    assert "B：要修正 1 件" in text
    assert "C：要変換 1 件" in text
    assert "D：対象外 1 件" in text


def test_report_includes_check_warnings_for_files_not_written():
    """チェックOFF・キャンセル等で書き出されなかったファイルの検出のみ警告
    （パスワード・用紙寸法等）がレポートに一切現れない不具合の回帰テスト：
    all_recordsベースの独立セクションとして必ず記載される（4.8「警告内容」・
    フロー⑦「成功／警告／失敗の一覧」）。"""
    from mints_prep.models import PdfFinding

    password_record = _record(
        "パスワード付き.pdf",
        Classification.B_NEEDS_FIX,
        pdf_finding=PdfFinding(has_password=True),
    )
    records = [_record("a.pdf"), password_record]
    # パスワード付きは書き出されなかった（plannedに含まれない）
    planned = [PlannedCopy(record=records[0], output_filename="a.pdf")]

    text = build_report_text(
        input_dir=Path("in"), output_dir=Path("out"), all_records=records, planned=planned, batches=[]
    )

    assert "【検出のみの要修正事項（他のアプリでの対処が必要）】" in text
    assert "パスワード付き.pdf" in text


def test_report_lists_skipped_items_with_reasons():
    records = [_record("a.pdf")]
    planned = [PlannedCopy(record=records[0], output_filename="a.pdf")]

    text = build_report_text(
        input_dir=Path("in"),
        output_dir=Path("out"),
        all_records=records,
        planned=planned,
        batches=[],
        skipped_items=["b.pdf（レビューでキャンセル）", "c.pdf（書き出しエラー：locked）"],
    )

    assert "【書き出さなかったファイル（スキップ・失敗）】" in text
    assert "・b.pdf（レビューでキャンセル）" in text
    assert "・c.pdf（書き出しエラー：locked）" in text


def test_report_omits_skipped_section_when_none():
    records = [_record("a.pdf")]
    planned = [PlannedCopy(record=records[0], output_filename="a.pdf")]

    text = build_report_text(
        input_dir=Path("in"), output_dir=Path("out"), all_records=records, planned=planned, batches=[]
    )

    assert "書き出さなかったファイル" not in text


def test_report_prioritizes_oversized_and_redaction_warnings():
    oversized = _record("big.pdf", size_bytes=1, exceeds_single_file_limit=True)
    redacted = _record(
        "secret.pdf",
        redaction_finding=RedactionFinding(has_residual_text=True, pages_with_residual_text=(2,)),
    )
    records = [oversized, redacted]
    planned = [
        PlannedCopy(record=oversized, output_filename="big.pdf"),
        PlannedCopy(record=redacted, output_filename="secret.pdf"),
    ]

    text = build_report_text(
        input_dir=Path("in"), output_dir=Path("out"), all_records=records, planned=planned, batches=[]
    )

    assert "【重要警告（優先対応が必要）】" in text
    assert "big.pdf" in text.split("【重要警告")[1].split("【ファイルごと")[0]
    assert "2ページ目" in text


def test_report_prioritizes_pii_detection_warning():
    """要件定義書4.8：個人情報パターンの検出は墨消し検出と並ぶ最重要警告とする。"""
    flagged = _record(
        "secret.pdf",
        pii_finding=PiiFinding(has_pii=True, detected_categories=("メールアドレス",)),
    )
    planned = [PlannedCopy(record=flagged, output_filename="secret.pdf")]

    text = build_report_text(
        input_dir=Path("in"), output_dir=Path("out"), all_records=[flagged], planned=planned, batches=[]
    )

    assert "【重要警告（優先対応が必要）】" in text
    critical_section = text.split("【重要警告")[1].split("【ファイルごと")[0]
    assert "secret.pdf" in critical_section
    assert "個人情報の可能性がある記載を検出しました" in critical_section


def test_report_lists_applied_processing_per_file():
    record = _record("甲001 契約書.pdf")
    planned = [
        PlannedCopy(
            record=record,
            output_filename="甲001 契約書.pdf",
            strip_metadata=True,
            stamp_label="甲001",
        )
    ]

    text = build_report_text(
        input_dir=Path("in"), output_dir=Path("out"), all_records=[record], planned=planned, batches=[]
    )

    assert "証拠番号スタンプ（甲001）" in text
    assert "メタデータ除去" in text


def test_report_shows_detected_before_applied_in_chronological_order():
    from mints_prep.models import MetadataFinding

    # describe_expected_processing はA（適合済み）の場合「適合済み（処理不要）」を
    # 返し、これはreport_writer側でフィルタされ「検出事項」行が出ない。検出事項
    # が実際に出るB（要修正）分類で検証する。
    record = _record(
        "甲001 契約書.pdf",
        classification=Classification.B_NEEDS_FIX,
        metadata_finding=MetadataFinding(has_metadata=True, detected_fields=("Author",)),
    )
    planned = [
        PlannedCopy(
            record=record,
            output_filename="甲001 契約書.pdf",
            strip_metadata=True,
            stamp_label="甲001",
        )
    ]

    text = build_report_text(
        input_dir=Path("in"), output_dir=Path("out"), all_records=[record], planned=planned, batches=[]
    )

    detected_index = text.index("検出事項：")
    applied_index = text.index("適用処理：")
    assert detected_index < applied_index


def test_report_shows_batch_composition_when_split(tmp_path: Path):
    r1 = _record("a.pdf")
    r2 = _record("b.pdf")
    planned = [
        PlannedCopy(record=r1, output_filename="a.pdf"),
        PlannedCopy(record=r2, output_filename="b.pdf"),
    ]
    batches = [
        Batch(index=1, items=(planned[0],)),
        Batch(index=2, items=(planned[1],)),
    ]

    text = build_report_text(
        input_dir=Path("in"), output_dir=Path("out"), all_records=[r1, r2], planned=planned, batches=batches
    )

    assert "【提出回ごとの内訳】" in text
    assert "提出用_01：1 件" in text
    assert "提出用_02：1 件" in text


def test_report_omits_batch_section_when_single_batch():
    r1 = _record("a.pdf")
    planned = [PlannedCopy(record=r1, output_filename="a.pdf")]
    batches = [Batch(index=1, items=(planned[0],))]

    text = build_report_text(
        input_dir=Path("in"), output_dir=Path("out"), all_records=[r1], planned=planned, batches=batches
    )

    assert "【提出回ごとの内訳】" not in text


def test_report_lists_unsupported_and_convertible_files():
    records = [
        _record(
            "legacy.docx",
            Classification.C_NEEDS_CONVERSION,
            format_status=FormatStatus.CONVERTIBLE,
            detected_format="DOCX",
        ),
        _record(
            "archive.zip",
            Classification.D_UNSUPPORTED,
            format_status=FormatStatus.UNSUPPORTED,
            detected_format="unknown",
        ),
    ]

    text = build_report_text(
        input_dir=Path("in"), output_dir=Path("out"), all_records=records, planned=[], batches=[]
    )

    assert "【未対応形式として除外したファイル】" in text
    assert "legacy.docx（要変換（非対応形式）、検出形式：DOCX）" in text
    assert "archive.zip（処理対象外、検出形式：unknown）" in text


def test_report_includes_evidence_sequence_section_when_gap_detected(config):
    r1 = _record("甲001 契約書.pdf")
    r2 = _record("甲003 領収書.pdf")
    planned = [
        PlannedCopy(record=r1, output_filename="甲001 契約書.pdf"),
        PlannedCopy(record=r2, output_filename="甲003 領収書.pdf"),
    ]

    text = build_report_text(
        input_dir=Path("in"),
        output_dir=Path("out"),
        all_records=[r1, r2],
        planned=planned,
        batches=[],
        evidence_number_config=config.evidence_number,
    )

    assert "【号証の連番・欠番チェック】" in text
    assert "甲：欠番の疑いあり（甲002）" in text


def test_report_omits_evidence_sequence_section_when_no_issue(config):
    r1 = _record("甲001 契約書.pdf")
    planned = [PlannedCopy(record=r1, output_filename="甲001 契約書.pdf")]

    text = build_report_text(
        input_dir=Path("in"),
        output_dir=Path("out"),
        all_records=[r1],
        planned=planned,
        batches=[],
        evidence_number_config=config.evidence_number,
    )

    assert "【号証の連番・欠番チェック】" not in text


def test_report_omits_evidence_sequence_section_when_config_not_supplied():
    r1 = _record("甲001 契約書.pdf")
    r2 = _record("甲003 領収書.pdf")
    planned = [
        PlannedCopy(record=r1, output_filename="甲001 契約書.pdf"),
        PlannedCopy(record=r2, output_filename="甲003 領収書.pdf"),
    ]

    text = build_report_text(
        input_dir=Path("in"), output_dir=Path("out"), all_records=[r1, r2], planned=planned, batches=[]
    )

    assert "【号証の連番・欠番チェック】" not in text


def test_collect_critical_warnings_covers_oversized_redaction_and_pii():
    """要件定義書4.8：collect_critical_warnings()は書き出し完了時の画面表示
    （ui/app.py）とレポート出力の双方から共有される。3種の重要警告を
    それぞれ拾えることを確認する。"""
    oversized = _record("big.pdf", size_bytes=1, exceeds_single_file_limit=True)
    redacted = _record(
        "secret.pdf",
        redaction_finding=RedactionFinding(has_residual_text=True, pages_with_residual_text=(2,)),
    )
    pii = _record(
        "陳述書.pdf", pii_finding=PiiFinding(has_pii=True, detected_categories=("電話番号",))
    )
    compliant = _record("ok.pdf")

    lines = collect_critical_warnings([oversized, redacted, pii, compliant])

    assert any("big.pdf" in line for line in lines)
    assert any("secret.pdf" in line and "2ページ目" in line for line in lines)
    assert any("陳述書.pdf" in line and "電話番号" in line for line in lines)
    assert len(lines) == 3


def test_collect_critical_warnings_empty_when_nothing_critical():
    assert collect_critical_warnings([_record("ok.pdf")]) == []


def test_collect_check_warnings_covers_password_permission_size_and_orientation():
    """書き出し完了時の警告画面（ui/app.py）に、検出のみの要修正事項
    （パスワード・操作制限・用紙寸法・向き不一致）がすべて表示されること。"""
    from mints_prep.models import PdfFinding

    password = _record("locked.pdf", pdf_finding=PdfFinding(has_password=True))
    restricted = _record(
        "登記情報.pdf", pdf_finding=PdfFinding(has_password=False, has_permission_restriction=True)
    )
    letter = _record(
        "letter.pdf",
        pdf_finding=PdfFinding(has_password=False, page_size_judgments=("A4", "非対応")),
    )
    rotated = _record(
        "rotated.pdf",
        pdf_finding=PdfFinding(has_password=False, orientation_mismatch_pages=(1, 3)),
    )
    compliant = _record("ok.pdf", pdf_finding=PdfFinding(has_password=False))
    non_pdf = _record("photo.jpg", detected_format="JPEG")

    lines = collect_check_warnings([password, restricted, letter, rotated, compliant, non_pdf])

    assert any("locked.pdf" in line and "パスワード付き" in line for line in lines)
    assert any("登記情報.pdf" in line and "印刷・コピー" in line for line in lines)
    assert any("letter.pdf" in line and "A4/A3以外" in line and "2ページ目" in line for line in lines)
    assert any("rotated.pdf" in line and "向き" in line and "1、3ページ目" in line for line in lines)
    assert len(lines) == 4


def test_collect_check_warnings_empty_when_all_compliant():
    from mints_prep.models import PdfFinding

    records = [
        _record("ok.pdf", pdf_finding=PdfFinding(has_password=False, page_size_judgments=("A4",))),
        _record("photo.jpg", detected_format="JPEG"),
    ]
    assert collect_check_warnings(records) == []


def test_format_evidence_sequence_warnings_matches_report_output(config):
    filenames = ["甲001 契約書.pdf", "甲003 領収書.pdf"]

    lines = format_evidence_sequence_warnings(filenames, config.evidence_number)

    assert lines == ["・甲：欠番の疑いあり（甲002）"]


def test_format_evidence_sequence_warnings_empty_when_no_issue(config):
    assert format_evidence_sequence_warnings(["甲001 契約書.pdf"], config.evidence_number) == []


def test_write_report_creates_file_in_output_dir(tmp_path: Path):
    dest = write_report("テスト本文", tmp_path)

    assert dest == tmp_path / REPORT_FILENAME
    assert dest.read_text(encoding="utf-8") == "テスト本文"


def test_report_does_not_mislabel_collision_rename_as_sanitize():
    """同名衝突回避の連番付与（dedupe）を「ファイル名整形」と誤記載しない
    （名前比較でなくfilename_sanitizedフラグで判定する回帰テスト）。"""
    record = _record("dup.pdf")
    item = PlannedCopy(
        record=record, output_filename="dup_2.pdf", renamed_for_collision=True
    )

    text = build_report_text(
        input_dir=Path("in"), output_dir=Path("out"), all_records=[record], planned=[item], batches=[]
    )

    assert "ファイル名整形" not in text
    assert "同名衝突回避のため連番付与" in text


def test_report_records_strip_metadata_for_merge_group_and_lists_sources(tmp_path: Path, config):
    """結合グループでも、実行したメタデータ除去を「適用処理」に記録し、
    どの元ファイルを統合したかを列挙する（記録欠落の回帰テスト）。"""
    from fixtures.generate_fixtures import make_a4_pdf
    from mints_prep.config import EvidenceNumberConfig, FilenameConfig
    from mints_prep.processors.evidence_merger import find_branch_merge_groups
    from mints_prep.processors.output_writer import plan_output_filenames

    def _real_record(name: str) -> FileRecord:
        p = make_a4_pdf(tmp_path / name)
        return FileRecord(
            path=p,
            size_bytes=p.stat().st_size,
            format_status=FormatStatus.RECORD_READY,
            detected_format="PDF",
            classification=Classification.A_COMPLIANT,
            filename_finding=FilenameFinding(
                length=len(name), exceeds_max_length=False, exceeds_warning_length=False
            ),
        )

    records = [_real_record("甲001-1 領収書.pdf"), _real_record("甲001-2 領収書.pdf")]
    groups = find_branch_merge_groups(records, config.evidence_number)
    plan = plan_output_filenames([], {}, merge_groups=groups, filename_config=config.filename)
    merged = PlannedCopy(
        record=plan[0].record,
        output_filename=plan[0].output_filename,
        strip_metadata=True,
        merge_group=plan[0].merge_group,
    )

    text = build_report_text(
        input_dir=Path("in"),
        output_dir=Path("out"),
        all_records=records,
        planned=[merged],
        batches=[],
    )

    assert "メタデータ除去" in text
    assert "甲001-1 領収書.pdf" in text
    assert "甲001-2 領収書.pdf" in text


def test_report_detected_column_omits_tool_note(tmp_path: Path):
    """書き出し後のレポートでは「書き出し時に本ツールで処理できます」注記を
    省く（既に処理済み・辞退済みで時制が合わないため）。"""
    from mints_prep.models import MetadataFinding

    record = _record(
        "甲001 契約書.pdf",
        classification=Classification.B_NEEDS_FIX,
        metadata_finding=MetadataFinding(has_metadata=True, detected_fields=("Author",)),
    )
    item = PlannedCopy(record=record, output_filename="甲001 契約書.pdf", strip_metadata=True)

    text = build_report_text(
        input_dir=Path("in"), output_dir=Path("out"), all_records=[record], planned=[item], batches=[]
    )

    assert "書き出し時に本ツールで処理できます" not in text
    assert "メタデータあり" in text
