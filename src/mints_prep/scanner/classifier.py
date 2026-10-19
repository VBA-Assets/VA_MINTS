"""ファイル分類（A：適合済み／B：要修正／C：要変換／D：処理対象外）。

docs/03_requirements.md 第3章「処理フロー（全体像）」の分類定義に基づく。
"""

from __future__ import annotations

from mints_prep.models import (
    Classification,
    FilenameFinding,
    FormatStatus,
    PdfFinding,
)


def _filename_needs_fix(finding: FilenameFinding) -> bool:
    # JIS X 0213外の文字（の可能性）も、mints側でファイル名が拒否され得る
    # 検出済みの要対処事項のためBに反映する（「A＝検出できる要対処事項が
    # ない」の定義〔要件定義書改訂履歴2026-07-21〕との整合）。コーデック
    # 近似による誤検出があり得るため、表示文言は「可能性」の非断定とする
    # （ui/formatting.py参照）。
    return (
        finding.exceeds_max_length
        or bool(finding.forbidden_chars_found)
        or bool(finding.non_jis_x0213_chars)
    )


def _pdf_needs_fix(finding: PdfFinding | None) -> bool:
    if finding is None:
        return False
    if finding.has_password:
        return True
    if finding.has_permission_restriction:
        return True
    if any(j == "非対応" for j in finding.page_size_judgments):
        return True
    if finding.orientation_mismatch_pages:
        return True
    return False


def classify(
    format_status: FormatStatus,
    filename_finding: FilenameFinding,
    pdf_finding: PdfFinding | None,
    has_metadata: bool,
    has_redaction_residue: bool = False,
    exceeds_single_file_limit: bool = False,
    has_pii: bool = False,
) -> Classification:
    """分類（A〜D）を判定する。

    メタデータ除去（4.5節）・用紙寸法補正やパスワード対処（4.6節）はいずれも
    「適合化処理」の対象であるため、これらの検出結果は B（要修正）に反映する。
    墨消し下の文字情報残存（4.6節）も、提出前に必ず対処すべき重大な問題のため
    同様にBとする（検出・警告のみで、除去〔防止処理〕の実行はチェック専念
    方針により廃止した）。個人情報パターンの検出（4.9節）も同様の性質
    （検出・警告のみ、実行はしない）のためBとする。
    単体ファイルが200MBを超える場合（4.5節「単体ファイル200MB超過の検出・警告」）
    も、合計サイズの分割では解決できない重大な問題のためBとする（報告書第8章
    確定事項5：追加時チェックが累積サイズを対象とし、単体で200MBを超える
    ファイルは最初の追加操作自体が拒否されると強く推認される）。
    """
    if format_status == FormatStatus.CONVERTIBLE:
        return Classification.C_NEEDS_CONVERSION

    if format_status in (FormatStatus.UNSUPPORTED, FormatStatus.EXTENSION_MISMATCH):
        return Classification.D_UNSUPPORTED

    # ここまで来れば FormatStatus.RECORD_READY
    if (
        _filename_needs_fix(filename_finding)
        or _pdf_needs_fix(pdf_finding)
        or has_metadata
        or has_redaction_residue
        or exceeds_single_file_limit
        or has_pii
    ):
        return Classification.B_NEEDS_FIX

    return Classification.A_COMPLIANT
