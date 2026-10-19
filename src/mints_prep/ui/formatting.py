"""スキャン結果一覧の表示文言を組み立てる。

docs/03_requirements.md 4.1「スキャン結果表示」：ファイル名・形式・サイズ・
分類（A〜D）・想定される処理内容を一覧表示する、の「検出事項」列を
FileRecord から生成する（mints提出前レポートの「検出事項」表記に合わせる。
2026-07-21改称。旧称「処理内容（予定）」）。

4.3「出力ファイル名入力機能」の証拠番号パターン判定結果（4.7の適用候補に
なるか）も、専用の列は設けずこの列の文言に含める（ツールはファイルの中身を
プレビューできずファイル名の適否を判断できないため、ファイル名編集機能は
持たない。判定結果の提示にとどめる）。
"""

from __future__ import annotations

from mints_prep.models import Classification, EvidenceNumberMatch, FileRecord, FormatStatus

CLASSIFICATION_LABELS = {
    Classification.A_COMPLIANT: "A：適合済み",
    Classification.B_NEEDS_FIX: "B：要修正",
    Classification.C_NEEDS_CONVERSION: "C：要変換",
    Classification.D_UNSUPPORTED: "D：対象外",
}


def format_size_display(size_bytes: int) -> str:
    """ファイルサイズを読みやすい単位（B/KB/MB/GB）で表示する。

    MB固定・小数点以下2桁のみだと、数百バイト〜数KBのファイルが軒並み
    「0.00」に丸められて事実上サイズが分からなくなるため、単位を可変にする。
    """
    size = float(size_bytes)
    if size_bytes < 1024:
        return f"{size_bytes} B"
    size /= 1024
    if size < 1024:
        return f"{size:.1f} KB"
    size /= 1024
    if size < 1024:
        return f"{size:.2f} MB"
    size /= 1024
    return f"{size:.2f} GB"


def describe_expected_processing(
    record: FileRecord,
    evidence_match: EvidenceNumberMatch | None = None,
    include_tool_note: bool = True,
) -> str:
    """分類・検出結果から「検出事項」列の説明文を組み立てる。

    evidence_match は4.3の証拠番号パターン判定結果。C（要変換）・D（対象外）の
    ファイルはそのままでは証拠として提出できないため、判定結果があっても表示しない。

    include_tool_note：ファイル名整形・メタデータ除去の「書き出し時に本ツールで
    処理できます」注記を含めるか。スキャン結果一覧（書き出し前）ではTrue、
    書き出し完了後のレポートでは時制が合わない（既に処理済み又は辞退済み）ため
    Falseを渡す。
    """
    if record.classification == Classification.D_UNSUPPORTED:
        if record.format_status == FormatStatus.EXTENSION_MISMATCH:
            return "拡張子と実体（中身）が一致しないため要確認"
        return "未対応形式のため処理対象外"

    if record.classification == Classification.C_NEEDS_CONVERSION:
        return (
            f"{record.detected_format}形式のため要変換"
            "（本ツールは自動変換を行いません。対応形式〔PDF/MP4/MP3/JPEG/PNG〕に"
            "変換したうえで再度読み込んでください）"
        )

    parts: list[str] = []
    # 単体ファイル200MB超過・墨消し完全性の検証結果は、いずれも提出前に必ず
    # 対処すべき最も重要度の高い警告として先頭に表示する（要件定義書4.5節・
    # 4.8のレポート出力仕様に準じる）。単体200MB超過は合計サイズの分割
    # （200MB分割）では解決できず、圧縮又は分割（実行機能はチェック専念方針
    # により廃止）なしには原理的に
    # 提出不可能なため、最優先で示す。
    if record.exceeds_single_file_limit:
        limit_text = (
            f"{record.single_file_limit_mb:g}MB"
            if record.single_file_limit_mb is not None
            else "アップロード上限"
        )
        parts.append(
            f"単体で{limit_text}を超えています（{record.size_mb:.1f}MB）。"
            "圧縮又は分割なしには提出できません"
        )

    rf = record.redaction_finding
    if rf is not None and rf.has_residual_text:
        pages = "、".join(str(p) for p in rf.pages_with_residual_text)
        parts.append(f"墨消し・マーカーの下に文字情報が残存している疑いあり（{pages}ページ目、要確認）")

    # 個人情報パターンの検出（要件定義書4.9）も、墨消し完全性の検証結果と
    # 並ぶ最重要警告として先頭付近に表示する。誤検出（事件番号・書証中の
    # 日付等を誤って拾う場合）があり得るため、断定を避けた文言とする。
    pii = record.pii_finding
    if pii is not None and pii.has_pii:
        categories = "、".join(pii.detected_categories)
        parts.append(
            f"個人情報の可能性がある記載を検出しました（{categories}）。"
            "意図した記載か確認してください"
        )

    # ファイル名整形・メタデータ除去は本ツールの処理系機能（書き出し時の
    # 逐次レビューで実行可能）のため、他アプリでの対処が必要な検出事項と
    # 区別できるよう「書き出し時に本ツールで処理できます」と注記する
    # （分類は現状の状態把握としてBのまま変えない。処理して出力された
    # ファイルを再読み込みすればAになる）。
    tool_note = "。書き出し時に本ツールで処理できます" if include_tool_note else ""
    ff = record.filename_finding
    if ff.exceeds_max_length:
        limit_text = f"上限{ff.max_length}文字" if ff.max_length is not None else "上限文字数"
        parts.append(f"ファイル名が{ff.length}文字（{limit_text}を超過{tool_note}）")
    if ff.forbidden_chars_found:
        parts.append(
            f"ファイル名に禁止文字あり（{' '.join(ff.forbidden_chars_found)}{tool_note}）"
        )
    if ff.non_jis_x0213_chars:
        # 外字はファイル名整形（禁止文字置換）の対象外＝本ツールでは対処
        # できないため、tool_noteは付けない（他アプリでの改名が必要）。
        # コーデック近似による誤検出があり得るため非断定の文言とする。
        chars = "".join(ff.non_jis_x0213_chars)
        parts.append(
            f"ファイル名にJIS X 0213外の文字の可能性（{chars}）。"
            "mintsで使用できない場合があるため、ファイル名の変更を検討してください"
        )

    pf = record.pdf_finding
    if pf is not None:
        if pf.has_password:
            parts.append("パスワード付き（アップロード不可。提出前にファイルのパスワードを解除し、再度お試しください）")
        if pf.has_permission_restriction:
            parts.append(
                "印刷・コピー等が制限されたPDFの疑いあり（オーナーパスワードによる権限制限。"
                "mintsに拒否される可能性があります。制限のないPDFとして再取得するか、"
                "他のツールで制限を解除してから再度読み込んで再検証してください）"
            )
        non_a4a3_pages = [
            i for i, j in enumerate(pf.page_size_judgments, start=1) if j == "非対応"
        ]
        if non_a4a3_pages:
            pages = "、".join(str(p) for p in non_a4a3_pages)
            parts.append(f"用紙寸法がA4/A3以外（{pages}ページ目）")
        if pf.orientation_mismatch_pages:
            pages = "、".join(str(p) for p in pf.orientation_mismatch_pages)
            parts.append(f"向きが基本形と不一致の疑い（{pages}ページ目{tool_note}）")

    if record.metadata_finding is not None and record.metadata_finding.has_metadata:
        parts.append(f"メタデータあり（除去を推奨{tool_note}）")

    evidence_note = None
    if evidence_match is not None:
        evidence_note = f"証拠番号処理（右上スタンプ・枝番ファイル結合等）の適用候補：{evidence_match.evidence_number_label}"

    if record.classification == Classification.A_COMPLIANT:
        segments = ["適合済み（処理不要）"]
        if ff.exceeds_warning_length:
            segments.append(f"参考：ファイル名{ff.length}文字。実務推奨の50文字は超過")
        if evidence_note:
            segments.append(evidence_note)
        return " / ".join(segments)

    if evidence_note:
        parts.append(evidence_note)
    return " / ".join(parts) if parts else "要確認"
