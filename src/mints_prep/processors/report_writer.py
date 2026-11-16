"""検出結果の警告文（最重要警告・検出のみの要修正事項・号証の連番欠番）。

Ver170 までは、ここでテキストのレポート（mints提出前レポート.txt。要件定義書 4.8）を
組み立てていた。Ver200 でレポートは廃止した（2026-11-16 班長決定。検出結果は右ペイン
と画面下部に、行った処理は「元のファイル」フォルダの更新記録に、更新できなかった
ファイルは完了のお知らせに出るため）。残っているのは、画面（ui/review_logic.py・
ui/app.py・ui/workspace.py）が使う警告文の組み立てだけで、モジュールの名前は
そのままにしている。

集計・整形のみを行う読み取り専用処理のため、CLAUDE.md「絶対に譲れない設計
原則」3（ファイル変更処理の確認UI）の対象外。
"""

from __future__ import annotations

from collections.abc import Iterable

from mints_prep.config import EvidenceNumberConfig
from mints_prep.models import EvidenceSequenceFinding, FileRecord
from mints_prep.naming.evidence_number import check_evidence_sequence


def _format_evidence_sequence_finding(finding: EvidenceSequenceFinding, digits: int) -> list[str]:
    def _label(n: int) -> str:
        return f"{finding.symbol_key}{n:0{digits}d}"

    lines: list[str] = []
    if finding.missing_numbers:
        numbers = "、".join(_label(n) for n in finding.missing_numbers)
        lines.append(f"・{finding.symbol_key}：欠番の疑いあり（{numbers}）")
    if finding.duplicate_numbers:
        numbers = "、".join(_label(n) for n in finding.duplicate_numbers)
        lines.append(f"・{finding.symbol_key}：重複の疑いあり（{numbers}）")
    return lines


def collect_critical_warnings(records: Iterable[FileRecord]) -> list[str]:
    """要件定義書4.8の指定どおり、最も重要度の高い警告（単体200MB超過・墨消し
    完全性の残存検出・個人情報パターンの検出）を一覧化する。

    レポート出力（build_report_text）と、逐次レビュー画面（ui/review_logic.py。
    検出のみ・対処不可の項目のグレーアウト表示）の双方で共有し、レポートを
    開かないと重要な検出結果に気づけない、という事態を避ける。
    """
    critical: list[str] = []
    for r in records:
        if r.exceeds_single_file_limit:
            critical.append(
                f"・{r.path.name}：単体で{r.size_mb:.1f}MB。圧縮又は分割なしには提出不可能"
            )
        if r.redaction_finding is not None and r.redaction_finding.has_residual_text:
            pages = "、".join(str(p) for p in r.redaction_finding.pages_with_residual_text)
            critical.append(
                f"・{r.path.name}：墨消し・マーカーの下に文字情報が残存している疑いあり"
                f"（{pages}ページ目、要確認）"
            )
        if r.pii_finding is not None and r.pii_finding.has_pii:
            categories = "、".join(r.pii_finding.detected_categories)
            critical.append(
                f"・{r.path.name}：個人情報の可能性がある記載を検出しました"
                f"（{categories}）。意図した記載か確認してください"
            )
    return critical


def collect_check_warnings(records: Iterable[FileRecord]) -> list[str]:
    """チェック専念方針の「検出のみ」項目のうち、本ツールの書き出し処理では
    修正されない（＝他のアプリでの対処が必要な）ものを一覧化する。

    対象：パスワード付きPDF・操作制限（印刷・コピー制限）PDF・用紙寸法
    （A4/A3以外）・向き不一致。最重要警告（collect_critical_warnings）に
    含まれる項目との重複はない。ファイル名整形・メタデータ除去は本ツールの
    処理系機能（実行可否をチェックボックスで選べる）のため含めない。
    ui/review_logic.pyの逐次レビュー画面（検出のみ・対処不可の項目の
    グレーアウト表示）と、レポート出力（build_report_text。要件定義書4.8
    「警告内容」）の双方で使用する。レビューでチェックを外した・キャンセル
    したファイルの検出結果も、レポートには全件（all_recordsベースで）残る。
    """
    lines: list[str] = []
    for r in records:
        pf = r.pdf_finding
        if pf is None:
            continue
        if pf.has_password:
            lines.append(f"・{r.path.name}：パスワード付き（提出前に解除が必要）")
        if pf.has_permission_restriction:
            lines.append(
                f"・{r.path.name}：印刷・コピー等が制限されたPDFの疑い"
                "（制限のない形での再取得又は他ツールでの解除が必要）"
            )
        non_a4a3_pages = [
            i for i, judgment in enumerate(pf.page_size_judgments, start=1) if judgment == "非対応"
        ]
        if non_a4a3_pages:
            pages = "、".join(str(p) for p in non_a4a3_pages)
            lines.append(f"・{r.path.name}：用紙寸法がA4/A3以外（{pages}ページ目）")
        if pf.orientation_mismatch_pages:
            pages = "、".join(str(p) for p in pf.orientation_mismatch_pages)
            lines.append(f"・{r.path.name}：向きが基本形と不一致の疑い（{pages}ページ目）")
    return lines


def format_evidence_sequence_warnings(
    filenames: Iterable[str], config: EvidenceNumberConfig
) -> list[str]:
    """号証の連番・欠番チェック（要件定義書4.7）の結果を警告行として整形する。

    レポート出力と、書き出し開始前の画面表示（ui/app.py。対象ファイル確定
    時点のポップアップ）の双方で共有する。
    """
    findings = check_evidence_sequence(filenames, config)
    lines: list[str] = []
    for finding in findings:
        lines.extend(_format_evidence_sequence_finding(finding, config.digits))
    return lines


