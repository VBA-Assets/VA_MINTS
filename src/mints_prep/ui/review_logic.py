"""逐次書き出しレビュー画面（ui/write_review_dialog.py）の判定ロジック。

画面の見た目（Tkinter）から切り離した純粋関数として実装し、単体テストで
検証できるようにする（プロジェクトの既存方針：例えば app.py の
RESULT_TABLE_COLUMNS がTk非依存の定数として切り出されているのと同様）。

チェックボックスの既定値は、既存の自動検出ロジック（needs_sanitization・
metadata_finding.has_metadata・証拠番号マッチの有無）をすべて適用したと仮定して
事前に組み立てられた「推奨計画」（ReviewItem.preliminary）から導出する。
ユーザーがチェックを外した項目は、apply_review_choices が最終的な
PlannedCopy へ反映する（未適用の状態に戻す）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mints_prep.config import FilenameConfig
from mints_prep.models import FileRecord
from mints_prep.processors.filename_sanitizer import needs_sanitization, sanitize_filename
from mints_prep.processors.output_writer import PlannedCopy
from mints_prep.processors.report_writer import collect_check_warnings, collect_critical_warnings


@dataclass(frozen=True)
class ReviewItem:
    """逐次レビュー画面1回分に対応する1件（単独ファイル又は枝番ファイル結合グループ）。"""

    preliminary: PlannedCopy  # 既定値（自動推奨）をすべて適用した状態の計画
    preview_record: FileRecord  # プレビュー対象（結合グループは代表メンバーの実ファイル）
    batch_dir: Path  # このアイテムの書き出し先（事前計算済みバッチによる）


@dataclass(frozen=True)
class ReviewOption:
    key: str  # "sanitize" / "strip_metadata" / "stamp" / "rotate" / "info_N"（検出のみ）
    label: str
    default_checked: bool
    # Falseの場合、本ツールでは対処できない（検出のみ）項目としてチェック
    # ボックスをグレーアウト表示する（default_checkedはFalse＝実行を前提と
    # しない、対処不可であることが伝わるOFF状態で固定される）。
    enabled: bool = True


def _unfixable_detection_labels(record: FileRecord) -> list[str]:
    """本ツールでは対処できない（検出のみ）項目の説明文を返す。

    レポート出力（4.8）と同じ collect_critical_warnings・collect_check_warnings
    を再利用し、文言を統一する（単体200MB超過・墨消し完全性の残存検出・
    個人情報パターンの検出・パスワード・操作制限・用紙寸法・向き不一致）。
    """
    return collect_critical_warnings([record]) + collect_check_warnings([record])


def build_review_options(item: ReviewItem, filename_config: FilenameConfig) -> list[ReviewOption]:
    """このアイテムに提示すべきチェックボックスと、その既定値を決定する。

    該当する処理がなければ空リストを返す（画面側は「検出された問題は
    ありません」を表示する）。

    本ツールでは対処できない（検出のみの）項目をenabled=Falseで一覧の先頭に
    並べ、続けて実行可否を選べる項目（ファイル名整形・メタデータ除去・
    証拠番号スタンプ）を並べる。書き出し完了時に別途まとめて警告する方式
    （旧設計）は、実行前個別確認の原則（CLAUDE.md）に照らすと「確認せず
    出力した場合に何も知らされない」抜け道になり得るため、その場で必ず
    目に入るこの画面に一本化した。結合グループは各ソースファイルの検出結果を、
    どのファイル由来か分かる形でそれぞれ列挙する。
    """
    prelim = item.preliminary
    info_options: list[ReviewOption] = []
    actionable_options: list[ReviewOption] = []

    # 向き補正（4.6。orientation_fixer参照）を提供できるか：単独ファイルの
    # PDFで向き不一致ページが検出されている場合のみ。提供する場合、向き
    # 不一致は「検出のみ」の情報項目ではなく実行可否を選べる項目になるため、
    # 情報項目一覧からは除外する。
    offers_rotate = (
        prelim.merge_group is None
        and prelim.record.pdf_finding is not None
        and bool(prelim.record.pdf_finding.orientation_mismatch_pages)
    )

    detected_records = (
        [m.record for m in prelim.merge_group.members] if prelim.merge_group is not None else [prelim.record]
    )
    for record in detected_records:
        for label in _unfixable_detection_labels(record):
            if offers_rotate and "向きが基本形と不一致" in label:
                continue
            # collect_*の文言はレポート用の「・ファイル名：内容」形式。この画面は
            # 1ファイルずつの表示でファイル名は画面上部に出ているため、単独
            # ファイルではファイル名接頭辞を除去する（重複表示の回避）。結合
            # グループのみ、どのメンバー由来の検出かを区別するため名前を残す。
            label = label.removeprefix("・")
            if prelim.merge_group is None:
                label = label.removeprefix(f"{record.path.name}：")
            info_options.append(
                ReviewOption(
                    key=f"info_{len(info_options)}",
                    label=label,
                    # 検出のみで本ツールでは対処できない項目のため、実行を
                    # 前提とするON状態ではなく、対処不可であることが伝わる
                    # OFF状態でグレーアウト表示する。
                    default_checked=False,
                    enabled=False,
                )
            )

    if needs_sanitization(prelim.record.filename_finding):
        # 単独ファイルの「現在の名前」は元のファイル名。結合グループには
        # 個々のソースファイル名に対応する「元の名前」という概念がないため、
        # 結合により新規生成された名前そのものを基準に整形候補を示す。
        current_name = (
            prelim.record.path.name if prelim.merge_group is None else prelim.output_filename
        )
        sanitized = sanitize_filename(current_name, filename_config)
        actionable_options.append(
            ReviewOption(
                key="sanitize",
                label=f"ファイル名整形：「{current_name}」→「{sanitized}」",
                default_checked=True,
            )
        )

    if prelim.record.metadata_finding is not None and prelim.record.metadata_finding.has_metadata:
        fields = "、".join(prelim.record.metadata_finding.detected_fields)
        actionable_options.append(
            ReviewOption(
                key="strip_metadata",
                label=f"メタデータ除去：検出項目：{fields}",
                default_checked=True,
            )
        )

    if offers_rotate:
        pages = "、".join(str(p) for p in prelim.record.pdf_finding.orientation_mismatch_pages)
        actionable_options.append(
            ReviewOption(
                key="rotate",
                label=(
                    f"向き補正：基本形（A4=縦・A3=横）に合わせて90°回転（{pages}ページ目。"
                    "回転属性のみの変更で、ページの内容・画質・テキストは変わりません）"
                ),
                # 内容の向き（文字がどちらを向いているか）はツールには解析
                # できず、回転方向を誤ると読めない向きになるため、既定はOFF。
                # ONにするとプレビューが回転後の見た目に切り替わるので、
                # 目視確認のうえで適用を判断してもらう。
                default_checked=False,
            )
        )

    if prelim.merge_group is not None:
        low = prelim.merge_group.members[0].match.evidence_number_label
        high = prelim.merge_group.members[-1].match.evidence_number_label
        actionable_options.append(
            ReviewOption(
                key="stamp",
                label=(
                    "証拠番号スタンプ：結合後の各ページに、元ファイルごとの"
                    f"枝番ラベルを焼き込み（{low}〜{high}）"
                ),
                default_checked=True,
            )
        )
    elif prelim.stamp_label is not None:
        actionable_options.append(
            ReviewOption(
                key="stamp",
                label=f"証拠番号スタンプ：右上に「{prelim.stamp_label}」を焼き込み",
                # PDF以外（JPEG/PNG）は既定でOFFとする。写真等の画像は、
                # PDFの証拠（契約書等）と異なり原本の見た目そのものが証拠内容と
                # 直結する場合があり、スタンプの要否をより慎重に個別判断させる
                # ため、自動推奨（既定チェック）の対象からは外す。
                default_checked=prelim.record.detected_format == "PDF",
            )
        )

    return info_options + actionable_options


def apply_review_choices(
    item: ReviewItem,
    choices: dict[str, bool],
    filename_config: FilenameConfig,
    stamp_offset_mm: tuple[float, float] | None = None,
    rotate_degrees: int = 270,
) -> PlannedCopy:
    """チェックボックスの選択結果（choices）から、最終的なPlannedCopyを組み立てる。

    choicesに含まれないキー（画面に表示されなかった＝該当しない処理）は
    Falseとして扱う。

    stamp_offset_mm: 要件定義書4.7「スタンプ位置調整」。スタンプを実行する
    場合の (margin_top_mm, margin_right_mm) の上書き値。Noneならconfig.yamlの
    既定値をそのまま使う。スタンプを実行しない場合は無視される。このファイル
    （又は結合グループ）にのみ適用し、config.yamlの既定値自体は変更しない。

    rotate_degrees: 向き補正（choices["rotate"]がTrue）の場合の回転角
    （時計回り。右90°=90、左90°=270。既定は左90°＝画面側の既定と一致）。
    回転しない場合は無視される。
    """
    prelim = item.preliminary
    sanitize_on = choices.get("sanitize", False)
    strip_metadata_on = choices.get("strip_metadata", False)
    stamp_on = choices.get("stamp", False)
    rotate_on = choices.get("rotate", False)

    margin_top_mm, margin_right_mm = (
        stamp_offset_mm if stamp_on and stamp_offset_mm is not None else (None, None)
    )

    if prelim.merge_group is not None:
        base_name = prelim.output_filename
        output_filename = sanitize_filename(base_name, filename_config) if sanitize_on else base_name
        return PlannedCopy(
            record=prelim.record,
            output_filename=output_filename,
            strip_metadata=strip_metadata_on,
            merge_group=prelim.merge_group,
            merge_stamp=stamp_on,
            stamp_margin_top_mm=margin_top_mm,
            stamp_margin_right_mm=margin_right_mm,
            filename_sanitized=sanitize_on,
        )

    original_name = prelim.record.path.name
    output_filename = sanitize_filename(original_name, filename_config) if sanitize_on else original_name
    return PlannedCopy(
        record=prelim.record,
        output_filename=output_filename,
        strip_metadata=strip_metadata_on,
        stamp_label=prelim.stamp_label if stamp_on else None,
        stamp_margin_top_mm=margin_top_mm,
        stamp_margin_right_mm=margin_right_mm,
        filename_sanitized=sanitize_on,
        rotate_degrees=rotate_degrees if rotate_on else None,
    )
