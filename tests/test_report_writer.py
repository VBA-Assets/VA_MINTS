from pathlib import Path

from va_mints.models import (
    Classification,
    FileRecord,
    FilenameFinding,
    FormatStatus,
    PiiFinding,
    RedactionFinding,
)
from va_mints.processors.report_writer import (
    collect_check_warnings,
    collect_critical_warnings,
    format_evidence_sequence_warnings,
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
    from va_mints.models import PdfFinding

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
    from va_mints.models import PdfFinding

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
