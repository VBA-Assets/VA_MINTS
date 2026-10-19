from mints_prep.models import Classification, FilenameFinding, FormatStatus, PdfFinding
from mints_prep.scanner.classifier import classify

_OK_FILENAME = FilenameFinding(length=10, exceeds_max_length=False, exceeds_warning_length=False)


def test_compliant_file_classified_a():
    result = classify(
        format_status=FormatStatus.RECORD_READY,
        filename_finding=_OK_FILENAME,
        pdf_finding=None,
        has_metadata=False,
    )
    assert result == Classification.A_COMPLIANT


def test_convertible_format_classified_c_even_if_otherwise_compliant():
    result = classify(
        format_status=FormatStatus.CONVERTIBLE,
        filename_finding=_OK_FILENAME,
        pdf_finding=None,
        has_metadata=False,
    )
    assert result == Classification.C_NEEDS_CONVERSION


def test_unsupported_format_classified_d():
    result = classify(
        format_status=FormatStatus.UNSUPPORTED,
        filename_finding=_OK_FILENAME,
        pdf_finding=None,
        has_metadata=False,
    )
    assert result == Classification.D_UNSUPPORTED


def test_exceeds_single_file_limit_forces_b():
    result = classify(
        format_status=FormatStatus.RECORD_READY,
        filename_finding=_OK_FILENAME,
        pdf_finding=None,
        has_metadata=False,
        exceeds_single_file_limit=True,
    )
    assert result == Classification.B_NEEDS_FIX


def test_permission_restricted_pdf_forces_b():
    """要件定義書4.6：操作制限（印刷・コピー制限）PDFはB（要修正）に反映する。"""
    result = classify(
        format_status=FormatStatus.RECORD_READY,
        filename_finding=_OK_FILENAME,
        pdf_finding=PdfFinding(has_password=False, has_permission_restriction=True),
        has_metadata=False,
    )
    assert result == Classification.B_NEEDS_FIX


def test_pii_detection_forces_b():
    """要件定義書4.9：個人情報パターンの検出はB（要修正）に反映する。"""
    result = classify(
        format_status=FormatStatus.RECORD_READY,
        filename_finding=_OK_FILENAME,
        pdf_finding=None,
        has_metadata=False,
        has_pii=True,
    )
    assert result == Classification.B_NEEDS_FIX


def test_non_jis_x0213_chars_force_b():
    """JIS X 0213外の文字（の可能性）は備考表示のみでAのままだった不具合の
    回帰テスト：「A＝検出できる要対処事項がない」の定義（改訂履歴2026-07-21）
    に合わせてBに反映する。"""
    finding = FilenameFinding(
        length=10,
        exceeds_max_length=False,
        exceeds_warning_length=False,
        non_jis_x0213_chars=("𩸽",),
    )
    result = classify(
        format_status=FormatStatus.RECORD_READY,
        filename_finding=finding,
        pdf_finding=None,
        has_metadata=False,
    )
    assert result == Classification.B_NEEDS_FIX


def test_exceeds_single_file_limit_false_by_default():
    # 明示的に指定しなければ、他の問題がない限りAのまま
    result = classify(
        format_status=FormatStatus.RECORD_READY,
        filename_finding=_OK_FILENAME,
        pdf_finding=None,
        has_metadata=False,
    )
    assert result == Classification.A_COMPLIANT
