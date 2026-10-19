"""基本レポート出力機能（要件定義書 4.8）。

要件定義書4.8「レポート出力機能」：処理結果、警告一覧、複数回提出の内訳、
未対応形式の一覧等を含む。書き出し完了後、出力フォルダの直下にテキスト
レポートを生成する。

「バッチ」という用語は処理中の画面（ui/checklist_dialog.py・
ui/write_review_dialog.py等）に一度も表れず分かりにくいため、レポート上の
表記では使わない（画面の完了通知文言と揃え「提出回」で統一する。内部の
データ構造名〔batch_planner.Batch等〕はそのまま維持する）。

集計・整形のみを行う読み取り専用処理のため、CLAUDE.md「絶対に譲れない設計
原則」3（ファイル変更処理の確認UI）の対象外（自動生成してよい）。
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from mints_prep.config import EvidenceNumberConfig
from mints_prep.models import Classification, EvidenceSequenceFinding, FileRecord
from mints_prep.naming.evidence_number import check_evidence_sequence
from mints_prep.processors.batch_planner import Batch
from mints_prep.processors.output_writer import PlannedCopy
from mints_prep.ui.formatting import CLASSIFICATION_LABELS, describe_expected_processing

REPORT_FILENAME = "mints提出前レポート.txt"


def _applied_processing_labels(item: PlannedCopy) -> list[str]:
    """このPlannedCopyに実際に適用された処理（確認済みで実行されたもの）を列挙する。

    ファイル名整形の判定は名前比較ではなくfilename_sanitizedフラグで行う
    （名前比較では、同名衝突回避の連番付与や結合後ファイル名まで
    「ファイル名整形」と誤記載されるため）。連番付与は別ラベルで記載する。
    結合グループも、実際に実行したメタデータ除去・ファイル名整形を省略せず
    記録する（証拠に加えた変更の記録が欠落しないように）。
    """
    labels: list[str] = []
    if item.merge_group is not None:
        labels.append(f"枝番ファイル結合（{len(item.merge_group.members)}件を統合）")
        if item.merge_stamp:
            labels.append("証拠番号スタンプ（結合ソースごとに枝番ラベル）")
    elif item.stamp_label is not None:
        labels.append(f"証拠番号スタンプ（{item.stamp_label}）")
    if item.rotate_degrees is not None and item.merge_group is None:
        direction = "右" if item.rotate_degrees % 360 == 90 else "左"
        pages = (
            "、".join(str(p) for p in item.record.pdf_finding.orientation_mismatch_pages)
            if item.record.pdf_finding is not None
            else ""
        )
        labels.append(f"向き補正（{direction}に90°回転：{pages}ページ目）")
    if item.strip_metadata:
        labels.append("メタデータ除去")
    if item.filename_sanitized:
        labels.append("ファイル名整形")
    if item.renamed_for_collision:
        labels.append("同名衝突回避のため連番付与")
    return labels or ["処理なし（そのまま書き出し）"]


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


def build_report_text(
    *,
    input_dir: Path,
    output_dir: Path,
    all_records: list[FileRecord],
    planned: list[PlannedCopy],
    batches: list[Batch],
    evidence_number_config: EvidenceNumberConfig | None = None,
    skipped_items: list[str] | None = None,
) -> str:
    """レポート本文（テキスト形式）を組み立てる。

    skipped_items：逐次レビューのキャンセル・上書き辞退・書き出しエラーで
    実際には書き出されなかったファイルの一覧（「ファイル名（理由）」形式）。
    第3章処理フロー⑦「成功／警告／失敗の一覧」に対応する。
    """
    lines: list[str] = [
        "VA mints 処理レポート",
        f"入力フォルダ：{input_dir}",
        f"出力フォルダ：{output_dir}",
        "",
    ]

    counts = {c: 0 for c in Classification}
    for r in all_records:
        counts[r.classification] += 1
    lines.append("【サマリ】")
    lines.append(
        f"合計 {len(all_records)} 件　"
        f"A：適合済み {counts[Classification.A_COMPLIANT]} 件　"
        f"B：要修正 {counts[Classification.B_NEEDS_FIX]} 件　"
        f"C：要変換 {counts[Classification.C_NEEDS_CONVERSION]} 件　"
        f"D：対象外 {counts[Classification.D_UNSUPPORTED]} 件"
    )
    lines.append(f"書き出し：{len(planned)} 件　提出回数：{len(batches)} 回")
    lines.append("")

    # 単体200MB超過・墨消し完全性の残存検出・個人情報パターンの検出は、
    # 要件定義書4.8の指定どおり最も重要度の高い警告としてレポート冒頭に
    # 優先表示する。
    critical = collect_critical_warnings(all_records)
    if critical:
        lines.append("【重要警告（優先対応が必要）】")
        lines.extend(critical)
        lines.append("")

    # 要件定義書4.8「警告内容（用紙サイズ不一致、パスワード検出、操作制限PDF
    # 検出等）」：検出のみ（本ツールでは修正されない）の要修正事項を、書き出し
    # の有無にかかわらず全スキャン結果から列挙する。逐次レビューでチェックを
    # 外した・キャンセルしたファイルの検出結果も、ここには必ず残る。
    check_warnings = collect_check_warnings(all_records)
    if check_warnings:
        lines.append("【検出のみの要修正事項（他のアプリでの対処が必要）】")
        lines.extend(check_warnings)
        lines.append("")

    lines.append("【ファイルごとの処理結果】")
    for item in planned:
        r = item.record
        applied_text = "、".join(_applied_processing_labels(item))
        if item.merge_group is not None:
            # 結合グループのrecordは「結合後ファイルを表す合成レコード」で、
            # r.path.nameは出力名と同一になり「どの元ファイルを統合したか」が
            # レポートから読めない。証拠に加えた変更の記録として、結合元の
            # ファイル名を必ず列挙する。
            sources = "、".join(m.record.path.name for m in item.merge_group.members)
            lines.append(
                f"・{sources} → {item.output_filename}"
                f"（{CLASSIFICATION_LABELS[r.classification]}）"
            )
        else:
            lines.append(
                f"・{r.path.name} → {item.output_filename}"
                f"（{CLASSIFICATION_LABELS[r.classification]}）"
            )
            detected = describe_expected_processing(r, include_tool_note=False)
            if detected and detected != "適合済み（処理不要）":
                lines.append(f"    検出事項：{detected}")
        lines.append(f"    適用処理：{applied_text}")
    lines.append("")

    if evidence_number_config is not None:
        sequence_lines = format_evidence_sequence_warnings(
            (item.output_filename for item in planned), evidence_number_config
        )
        if sequence_lines:
            lines.append("【号証の連番・欠番チェック】")
            lines.extend(sequence_lines)
            lines.append("")

    if len(batches) >= 2:
        lines.append("【提出回ごとの内訳】")
        for batch in sorted(batches, key=lambda b: b.index):
            size_mb = batch.total_size_bytes / (1024 * 1024)
            lines.append(f"提出用_{batch.index:02d}：{len(batch.items)} 件　合計 {size_mb:.1f} MB")
            for planned_item in batch.items:
                lines.append(f"    - {planned_item.output_filename}")
        lines.append("")

    if skipped_items:
        lines.append("【書き出さなかったファイル（スキップ・失敗）】")
        lines.extend(f"・{item}" for item in skipped_items)
        lines.append("")

    excluded = [
        r
        for r in all_records
        if r.classification in (Classification.C_NEEDS_CONVERSION, Classification.D_UNSUPPORTED)
    ]
    if excluded:
        lines.append("【未対応形式として除外したファイル】")
        for r in excluded:
            reason = (
                "要変換（非対応形式）"
                if r.classification == Classification.C_NEEDS_CONVERSION
                else "処理対象外"
            )
            lines.append(f"・{r.path.name}（{reason}、検出形式：{r.detected_format}）")
        lines.append("")

    return "\n".join(lines)


def write_report(text: str, output_dir: Path) -> Path:
    path = output_dir / REPORT_FILENAME
    path.write_text(text, encoding="utf-8")
    return path
