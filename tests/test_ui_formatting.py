from pathlib import Path

from mints_prep.models import (
    Classification,
    EvidenceNumberMatch,
    FileRecord,
    FilenameFinding,
    FormatStatus,
    PdfFinding,
    PiiFinding,
)
from mints_prep.ui.formatting import describe_expected_processing, format_size_display


def test_bytes_shown_as_is():
    assert format_size_display(500) == "500 B"


def test_small_file_shown_in_kb_not_rounded_to_zero():
    # 数百バイト〜数KBのファイルがMB表示で一律「0.00」に潰れないことを確認する
    assert format_size_display(1493) == "1.5 KB"


def test_mb_range():
    assert format_size_display(2 * 1024 * 1024) == "2.00 MB"


def test_gb_range():
    assert format_size_display(3 * 1024 * 1024 * 1024) == "3.00 GB"


def _compliant_record(name: str) -> FileRecord:
    return FileRecord(
        path=Path(name),
        size_bytes=1000,
        format_status=FormatStatus.RECORD_READY,
        detected_format="PDF",
        classification=Classification.A_COMPLIANT,
        filename_finding=FilenameFinding(
            length=len(name), exceeds_max_length=False, exceeds_warning_length=False
        ),
    )


def test_evidence_note_folded_into_a_classification_processing_text():
    record = _compliant_record("甲001 適合済みPDF.pdf")
    match = EvidenceNumberMatch(symbol="甲", subletter="", number="001", title="適合済みPDF")

    text = describe_expected_processing(record, match)

    assert "適合済み（処理不要）" in text
    assert "証拠番号処理" in text
    assert "甲001" in text


def test_no_evidence_note_when_match_is_none():
    record = _compliant_record("準備書面.pdf")

    text = describe_expected_processing(record, None)

    assert text == "適合済み（処理不要）"


def test_single_file_over_200mb_warning_shown_first():
    record = FileRecord(
        path=Path("甲001 巨大ファイル.pdf"),
        size_bytes=250 * 1024 * 1024,
        format_status=FormatStatus.RECORD_READY,
        detected_format="PDF",
        classification=Classification.B_NEEDS_FIX,
        filename_finding=FilenameFinding(
            length=10, exceeds_max_length=False, exceeds_warning_length=False
        ),
        exceeds_single_file_limit=True,
        single_file_limit_mb=200,  # walkerがconfig.upload.max_total_mbから設定する値
    )

    text = describe_expected_processing(record)

    assert "200MBを超えています" in text
    assert "250.0MB" in text
    # 最も重要度の高い警告として先頭（他の警告より前）に来ること
    assert text.startswith("単体で200MBを超えています")


def test_single_file_limit_warning_without_limit_value_falls_back_to_generic():
    """limit値が未設定（旧データ等）でも文言が破綻しないこと。"""
    record = FileRecord(
        path=Path("甲001 巨大ファイル.pdf"),
        size_bytes=250 * 1024 * 1024,
        format_status=FormatStatus.RECORD_READY,
        detected_format="PDF",
        classification=Classification.B_NEEDS_FIX,
        filename_finding=FilenameFinding(
            length=10, exceeds_max_length=False, exceeds_warning_length=False
        ),
        exceeds_single_file_limit=True,
    )

    text = describe_expected_processing(record)

    assert "単体でアップロード上限を超えています" in text


def test_no_size_warning_when_within_limit():
    record = _compliant_record("甲001 通常サイズ.pdf")

    text = describe_expected_processing(record)

    assert "200MB" not in text


def test_permission_restricted_pdf_warning_shown_distinct_from_password():
    """要件定義書4.6：操作制限PDFの警告は、パスワード検出とは別文言で表示される。"""
    record = FileRecord(
        path=Path("登記情報.pdf"),
        size_bytes=1000,
        format_status=FormatStatus.RECORD_READY,
        detected_format="PDF",
        classification=Classification.B_NEEDS_FIX,
        filename_finding=FilenameFinding(
            length=10, exceeds_max_length=False, exceeds_warning_length=False
        ),
        pdf_finding=PdfFinding(has_password=False, has_permission_restriction=True),
    )

    text = describe_expected_processing(record)

    assert "印刷・コピー" in text
    assert "パスワード付き" not in text


def test_tool_processable_findings_annotated():
    """ファイル名整形・メタデータ除去は本ツールで処理できる旨を注記し、
    他アプリでの対処が必要な検出事項と区別できるようにする（分類はBのまま）。"""
    from mints_prep.models import MetadataFinding

    record = FileRecord(
        path=Path("bad<name>.pdf"),
        size_bytes=1000,
        format_status=FormatStatus.RECORD_READY,
        detected_format="PDF",
        classification=Classification.B_NEEDS_FIX,
        filename_finding=FilenameFinding(
            length=120,
            exceeds_max_length=True,
            exceeds_warning_length=True,
            forbidden_chars_found=("<", ">"),
        ),
        metadata_finding=MetadataFinding(has_metadata=True, detected_fields=("Author",)),
    )

    text = describe_expected_processing(record)

    assert text.count("書き出し時に本ツールで処理できます") == 3


def test_externally_fixed_findings_not_annotated():
    """パスワード等、他アプリでの対処が必要な検出事項には注記を付けない。"""
    record = FileRecord(
        path=Path("locked.pdf"),
        size_bytes=1000,
        format_status=FormatStatus.RECORD_READY,
        detected_format="PDF",
        classification=Classification.B_NEEDS_FIX,
        filename_finding=FilenameFinding(
            length=10, exceeds_max_length=False, exceeds_warning_length=False
        ),
        pdf_finding=PdfFinding(has_password=True),
    )

    text = describe_expected_processing(record)

    assert "書き出し時に本ツールで処理できます" not in text


def test_pii_warning_uses_non_committal_wording():
    """要件定義書4.9：誤検出があり得るため断定を避けた文言とする。"""
    record = FileRecord(
        path=Path("報告書.pdf"),
        size_bytes=1000,
        format_status=FormatStatus.RECORD_READY,
        detected_format="PDF",
        classification=Classification.B_NEEDS_FIX,
        filename_finding=FilenameFinding(
            length=10, exceeds_max_length=False, exceeds_warning_length=False
        ),
        pii_finding=PiiFinding(has_pii=True, detected_categories=("メールアドレス",)),
    )

    text = describe_expected_processing(record)

    assert "個人情報の可能性がある記載を検出しました" in text
    assert "意図した記載か確認してください" in text
    assert "メールアドレス" in text


def test_evidence_note_not_shown_for_unsupported_format():
    record = FileRecord(
        path=Path("非対応形式ファイル.zip"),
        size_bytes=10,
        format_status=FormatStatus.UNSUPPORTED,
        detected_format="ZIP_CONTAINER",
        classification=Classification.D_UNSUPPORTED,
        filename_finding=FilenameFinding(
            length=10, exceeds_max_length=False, exceeds_warning_length=False
        ),
    )
    match = EvidenceNumberMatch(symbol="甲", subletter="", number="001", title="")

    text = describe_expected_processing(record, match)

    assert "証拠番号処理" not in text
