"""フォルダ選択・スキャン機能（要件定義書 4.1〜4.2、4.6の墨消し検証、
4.9の個人情報パターン検出）。

フォルダ内の全ファイルを走査し、各ファイルについて形式・サイズ・ファイル名・
パスワード有無・操作制限・用紙寸法・向き・メタデータ有無・墨消し完全性・
個人情報パターンを検出する。本モジュールはファイルの内容を一切変更しない
読み取り専用処理であり、CLAUDE.md「絶対に譲れない設計原則」3の確認UI原則
の対象外である。
"""

from __future__ import annotations

from pathlib import Path

from mints_prep.config import Config
from mints_prep.detectors.filename_detector import check_filename
from mints_prep.detectors.format_detector import detect_format
from mints_prep.detectors.metadata_detector import detect_metadata
from mints_prep.detectors.pdf_detector import detect_pdf
from mints_prep.detectors.pii_detector import (
    detect_pii_in_filename,
    detect_pii_in_pdf_text,
    merge_pii_findings,
)
from mints_prep.detectors.redaction_detector import detect_redaction_residue
from mints_prep.detectors.size_detector import get_size_bytes
from mints_prep.models import (
    Classification,
    FileRecord,
    FilenameFinding,
    FormatStatus,
    ScanResult,
)
from mints_prep.scanner.classifier import classify


# OS・アプリケーションが自動生成する一時ファイル・システムファイル。提出対象に
# なり得ず、スキャン結果一覧のノイズになるだけでなく、Officeのロックファイル
# （~$文書.docx。中身はZIPでないため拡張子フォールバックでC「要変換」と誤案内
# される）のような誤分類の原因にもなるため、走査対象から除外する。
_SYSTEM_FILE_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}
_SYSTEM_FILE_PREFIXES = ("~$",)
_SYSTEM_FILE_SUFFIXES = (".tmp",)


def _is_system_file(path: Path) -> bool:
    name = path.name.lower()
    return (
        name in _SYSTEM_FILE_NAMES
        or name.startswith(_SYSTEM_FILE_PREFIXES)
        or name.endswith(_SYSTEM_FILE_SUFFIXES)
    )


def _iter_files(root: Path, include_subfolders: bool):
    if include_subfolders:
        yield from (p for p in root.rglob("*") if p.is_file() and not _is_system_file(p))
    else:
        yield from (p for p in root.iterdir() if p.is_file() and not _is_system_file(p))


def scan_file(path: Path, config: Config) -> FileRecord:
    """1ファイル分の状態把握を行う（読み取り専用）。"""
    notes: list[str] = []

    size_bytes = get_size_bytes(path)
    format_status, detected_format = detect_format(path)

    filename_finding = check_filename(path.name, config.filename)
    if filename_finding.non_jis_x0213_chars:
        notes.append(
            f"JIS X 0213外の文字の可能性: {''.join(filename_finding.non_jis_x0213_chars)}"
        )

    pdf_finding = None
    if format_status == FormatStatus.RECORD_READY and detected_format == "PDF":
        pdf_finding = detect_pdf(path, config.pdf)
        if pdf_finding.has_password:
            notes.append("パスワード付きPDFのため寸法・向きの検出をスキップしました")

    metadata_finding = None
    if not (pdf_finding and pdf_finding.has_password):
        metadata_finding = detect_metadata(path)
        if metadata_finding is None:
            notes.append("この形式のメタデータ検出には対応していません")

    redaction_finding = None
    if format_status == FormatStatus.RECORD_READY and detected_format == "PDF":
        if pdf_finding and pdf_finding.has_password:
            notes.append("パスワード付きPDFのため墨消し完全性の検証をスキップしました")
        else:
            redaction_finding = detect_redaction_residue(path)
            if redaction_finding is None:
                notes.append("墨消し完全性の検証を実行できませんでした")
            elif redaction_finding.has_residual_text:
                notes.extend(redaction_finding.details)

    # 要件定義書4.9「個人情報パターンの検出」：ファイル名は全形式共通で検出し、
    # PDFのみ本文のテキストレイヤーも合わせて検出する。パスワード付きPDFは
    # 本文を読めないためファイル名のみとする。
    pii_finding = detect_pii_in_filename(path.name, config.pii_detection)
    if format_status == FormatStatus.RECORD_READY and detected_format == "PDF":
        if pdf_finding and pdf_finding.has_password:
            notes.append("パスワード付きPDFのため個人情報パターンの検出（本文）をスキップしました")
        else:
            pdf_text_pii = detect_pii_in_pdf_text(path, config.pii_detection)
            if pdf_text_pii is None:
                # 解析失敗を無警告の「個人情報なし」に化けさせない（墨消し
                # 検証の実行不可注記と同じ扱い）。
                notes.append("個人情報パターンの検出（本文）を実行できませんでした")
            else:
                pii_finding = merge_pii_findings(pii_finding, pdf_text_pii)
    if pii_finding.has_pii:
        notes.extend(pii_finding.details)

    if format_status == FormatStatus.EXTENSION_MISMATCH:
        notes.append(f"拡張子と実体（{detected_format}）が一致しません")
    if format_status == FormatStatus.UNSUPPORTED:
        notes.append("未対応形式のため処理対象外です")

    # 要件定義書4.5「単体ファイル200MB超過の検出・警告」（報告書第8章確定事項5）：
    # 追加時チェックが累積サイズを対象とするため、単体で200MBを超えるファイルは
    # 他のファイルとの合算を問わず最初の追加操作自体が拒否されると強く推認される。
    # 合計サイズの分割（200MB分割）では解決できない別種の重大な問題のため、
    # ここで検出する（対処〔圧縮・分割の実行〕はチェック専念方針により廃止）。
    exceeds_single_file_limit = size_bytes > config.upload.max_total_bytes
    if exceeds_single_file_limit:
        notes.append(
            f"単体ファイルが{config.upload.max_total_mb:g}MBを超えています"
            f"（{size_bytes / (1024 * 1024):.1f}MB）。"
            "圧縮又は分割なしにはmintsへ提出できません。"
        )

    classification = classify(
        format_status=format_status,
        filename_finding=filename_finding,
        pdf_finding=pdf_finding,
        has_metadata=bool(metadata_finding and metadata_finding.has_metadata),
        has_redaction_residue=bool(redaction_finding and redaction_finding.has_residual_text),
        exceeds_single_file_limit=exceeds_single_file_limit,
        has_pii=pii_finding.has_pii,
    )

    return FileRecord(
        path=path,
        size_bytes=size_bytes,
        format_status=format_status,
        detected_format=detected_format,
        classification=classification,
        filename_finding=filename_finding,
        metadata_finding=metadata_finding,
        pdf_finding=pdf_finding,
        redaction_finding=redaction_finding,
        pii_finding=pii_finding,
        exceeds_single_file_limit=exceeds_single_file_limit,
        single_file_limit_mb=config.upload.max_total_mb,
        notes=notes,
    )


def _error_record(path: Path, exc: Exception, config: Config) -> FileRecord:
    """個別ファイルの読み取りで想定外の例外が発生した場合の代替レコード。

    実運用のフォルダには破損ファイル等が混在し得るため、1ファイルの読み取り
    失敗でスキャン全体を中断させない（読み取り専用処理として安全側に倒す）。
    ファイル名の検証（check_filename）は純粋な文字列処理でファイル読み取りと
    無関係のため、読み取りエラー時も通常どおり実行する（100文字超過等の
    ファイル名起因の問題まで見逃さない）。
    """
    try:
        size_bytes = get_size_bytes(path)
    except OSError:
        size_bytes = 0
    try:
        filename_finding = check_filename(path.name, config.filename)
    except Exception:
        filename_finding = FilenameFinding(
            length=len(path.name), exceeds_max_length=False, exceeds_warning_length=False
        )
    return FileRecord(
        path=path,
        size_bytes=size_bytes,
        format_status=FormatStatus.UNSUPPORTED,
        detected_format="unknown",
        classification=Classification.D_UNSUPPORTED,
        filename_finding=filename_finding,
        notes=[f"読み取りエラーのため検出できませんでした: {exc}"],
    )


def scan_folder(root: Path, config: Config, include_subfolders: bool = False) -> ScanResult:
    """フォルダ内全ファイルをスキャンし、ScanResult を返す。"""
    records: list[FileRecord] = []
    for p in _iter_files(root, include_subfolders):
        try:
            records.append(scan_file(p, config))
        except Exception as exc:  # noqa: BLE001 - 個別ファイルの想定外エラーを隔離する
            records.append(_error_record(p, exc, config))
    return ScanResult(root=root, include_subfolders=include_subfolders, records=records)
