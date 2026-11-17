"""右ペインの処理の項目（ui/app.py・ui/workspace.py）の判定ロジック。

Ver170 までの逐次書き出しレビュー画面（ui/write_review_dialog.py。Ver200 で廃止）の
ために作ったもので、名前（ReviewItem・ReviewOption）はそのままにしている。

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

from mints_prep.config import CompressConfig, FilenameConfig, PdfConfig
from mints_prep.models import FileRecord
from mints_prep.processors.filename_sanitizer import needs_sanitization, sanitize_filename
from mints_prep.processors.metadata_remover import can_strip_metadata
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
    key: str  # "sanitize" / "strip_metadata" / "stamp" / "fit_a4" など / "info_N"（検出のみ）
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


def build_review_options(
    item: ReviewItem,
    filename_config: FilenameConfig,
    applied: frozenset[str] = frozenset(),
    compress_config: CompressConfig | None = None,
) -> list[ReviewOption]:
    """このアイテムに提示すべきチェックボックスと、その既定値を決定する。

    applied：これまでの更新で、このファイルに済ませた処理（更新記録。
    processors/updater.py）。証拠番号スタンプを付けたファイルは、読み込み直した
    ときも、スタンプにチェックの入った状態にする（2026-11-16 班長指示。チェックは
    「スタンプが付いている」ことを表し、外して更新するとスタンプを消す。付いて
    いるファイルにもう一度付けることはしない。ui/workspace.py）。

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

    # 向きの不一致（4.6）は、知らせるだけにする。回すのは、ページの回転（4.10）で利用者が
    # ページを選んで行う（2026-11-17 班長決定。右のペインの「向き補正」は廃止した）。
    detected_records = (
        [m.record for m in prelim.merge_group.members] if prelim.merge_group is not None else [prelim.record]
    )
    # 用紙を A4 にそろえる（4.15）・付箋等の除去（4.16）：PDF で、パスワードなしに開けるもの。
    # 結合グループは、結合する各ファイルの検出結果を合わせる。
    readable = [
        r.pdf_finding for r in detected_records
        if r.detected_format == "PDF" and r.pdf_finding is not None and not r.pdf_finding.has_password
    ]
    offers_fit = len(readable) == len(detected_records) and any(pf.non_a4a3_pages for pf in readable)
    hidden = [pf for pf in readable if pf.has_hidden_content] if len(readable) == len(detected_records) else []
    for record in detected_records:
        for label in _unfixable_detection_labels(record):
            if "向きが基本形と不一致" in label:
                continue  # 検出のみの事項ではない（ページを回して対処する。orientation_notice）
            if offers_fit and "用紙寸法がA4/A3以外" in label:
                continue  # そろえる項目に、ページを書いて出す
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

    if hidden:
        comments = sum(pf.hidden_comments for pf in hidden)
        attachments = sum(pf.hidden_attachments for pf in hidden)
        found = []
        if comments:
            found.append(f"付箋・コメント（作成者名を含む）{comments}件")
        if attachments:
            found.append(f"添付ファイル{attachments}件")
        if any(pf.has_javascript for pf in hidden):
            found.append("JavaScript（スクリプト）")
        actionable_options.append(
            ReviewOption(
                key="strip_hidden",
                label=(
                    f"付箋・コメント・添付ファイルを取り除く：{'、'.join(found)}。"
                    "ハイライトや図形などの見た目は残し、その中のコメントの文と作成者名だけを"
                    "外します（付箋と添付ファイルのアイコンは消えます）"
                ),
                # 原本の作成者が付けたコメントが、証拠の内容であることもあるので、自動では
                # チェックを付けない（更新の確認で、残っているファイルの数を示す）。
                default_checked=False,
            )
        )

    if (
        prelim.record.metadata_finding is not None and prelim.record.metadata_finding.has_metadata
        and not can_strip_metadata(prelim.record.detected_format)
    ):
        # MP4 のタグは、見つけて知らせるだけ（2026-11-17。detectors/av_tags.py）
        fields = "、".join(prelim.record.metadata_finding.detected_fields)
        info_options.append(
            ReviewOption(
                key=f"info_{len(info_options)}",
                label=(
                    f"メタデータ：検出項目：{fields}。動画のタグは、このツールでは取り除けません"
                    "（位置などを外す必要があれば、ほかのアプリで取り除いてから読み込み直してください）"
                ),
                default_checked=False,
                enabled=False,
            )
        )
    elif prelim.record.metadata_finding is not None and prelim.record.metadata_finding.has_metadata:
        fields = "、".join(prelim.record.metadata_finding.detected_fields)
        actionable_options.append(
            ReviewOption(
                key="strip_metadata",
                label=f"メタデータ除去：検出項目：{fields}",
                default_checked=True,
            )
        )

    if offers_fit:
        if prelim.merge_group is None:
            pages = "、".join(str(p) for p in readable[0].non_a4a3_pages)
            where = f"{pages}ページ目"
        else:
            where = "、".join(
                r.path.name for r in detected_records if r.pdf_finding.non_a4a3_pages
            )
        actionable_options.append(
            ReviewOption(
                key="fit_a4",
                label=(
                    f"用紙をA4にそろえる：A4・A3以外の大きさのページ（{where}）を、同じ向きのA4の"
                    "用紙に載せます。大きいページは縮め、小さいページは原寸のまま真ん中に置きます"
                    "（ページは画像にしません）"
                ),
                # A4・A3 以外のページがあると、mints が受け付けない（別紙2 エラー#46）ので、
                # 自動でチェックを付ける（2026-11-17）。縮めた見た目はプレビューで確かめる。
                default_checked=True,
            )
        )

    if prelim.merge_group is not None:
        low = prelim.merge_group.members[0].match.evidence_number_label
        high = prelim.merge_group.members[-1].match.evidence_number_label
        actionable_options.append(
            ReviewOption(
                key="stamp",
                label=(
                    "証拠番号スタンプ：結合する各ファイルの1ページ目の右上に、"
                    f"それぞれの枝番を焼き込み（{low}〜{high}）"
                ),
                default_checked=True,
            )
        )
    elif prelim.stamp_label is not None:
        actionable_options.append(
            ReviewOption(
                key="stamp",
                label=f"証拠番号スタンプ：1ページ目の右上に「{prelim.stamp_label}」を焼き込み",
                # PDF以外（JPEG/PNG）は既定でOFFとする。写真等の画像は、
                # PDFの証拠（契約書等）と異なり原本の見た目そのものが証拠内容と
                # 直結する場合があり、スタンプの要否をより慎重に個別判断させる
                # ため、自動推奨（既定チェック）の対象からは外す。
                # 付けたファイルは、読み込み直してもON。利用者が付けないと決めたファイル
                # （stamp_declined。更新記録）は、PDFでもONに戻さない。
                default_checked="stamp" in applied
                or (prelim.record.detected_format == "PDF" and "stamp_declined" not in applied),
            )
        )

    actionable_options.extend(_compress_options(prelim, applied, compress_config))
    return info_options + actionable_options


def _compress_options(
    prelim: PlannedCopy, applied: frozenset[str], compress_config: CompressConfig | None
) -> list[ReviewOption]:
    """圧縮（要件定義書 4.13）の項目。①は PDF だけ、②は PDF・JPEG・PNG。"""
    record = prelim.record
    fmt = record.detected_format
    if fmt not in ("PDF", "JPEG", "PNG"):
        return []
    if record.pdf_finding is not None and record.pdf_finding.has_password:
        return []  # 復号キーがないと中身を読めない
    cfg = compress_config or CompressConfig()
    options: list[ReviewOption] = []
    # 以前の更新で済ませた圧縮は、チェックの入った状態で出す（証拠番号スタンプと同じく、
    # チェックは「済んでいる」ことを表す。入れたままなら何もせず、外して更新すると、元の
    # ファイルから作り直す。2026-11-16 班長指示。ui/workspace.py）。
    if fmt == "PDF":
        large = record.size_bytes >= cfg.auto_lossless_mb * 1024 * 1024
        options.append(
            ReviewOption(
                key="compress",
                label=(
                    "画質を損なわない圧縮：PDFの中の使っていないデータを除き、データを詰め直して"
                    "小さくします（見た目・文字・画像は変わりません）"
                ),
                # 大きなファイルにだけ、自動でチェックを付ける。
                default_checked="compress" in applied or large,
            )
        )
    options.append(
        ReviewOption(
            key="reduce",
            label=(
                "画像を軽くする：画像の解像度を下げて小さくします（細かい字や写真が粗くなることが"
                "あります。プレビューで確かめてください）"
            ),
            # 画質が落ちるので、自動ではチェックを付けない（要件定義書 4.13）。済んだものだけ入れる。
            default_checked="reduce_images" in applied,
        )
    )
    return options


def apply_review_choices(
    item: ReviewItem,
    choices: dict[str, bool],
    filename_config: FilenameConfig,
    stamp_offset_mm: tuple[float, float] | None = None,
    reduce_dpi: int | None = None,
    jpeg_quality: int = 80,
    pdf_config: PdfConfig | None = None,
) -> PlannedCopy:
    """チェックボックスの選択結果（choices）から、最終的なPlannedCopyを組み立てる。

    choicesに含まれないキー（画面に表示されなかった＝該当しない処理）は
    Falseとして扱う。

    stamp_offset_mm: 要件定義書4.7「スタンプ位置調整」。スタンプを実行する
    場合の (margin_top_mm, margin_right_mm) の上書き値。Noneならconfig.yamlの
    既定値をそのまま使う。スタンプを実行しない場合は無視される。このファイル
    （又は結合グループ）にのみ適用し、config.yamlの既定値自体は変更しない。
    """
    prelim = item.preliminary
    sanitize_on = choices.get("sanitize", False)
    strip_metadata_on = choices.get("strip_metadata", False)
    stamp_on = choices.get("stamp", False)

    margin_top_mm, margin_right_mm = (
        stamp_offset_mm if stamp_on and stamp_offset_mm is not None else (None, None)
    )
    # 圧縮（4.13）。②の解像度は、画面で選んだもの（なければ設定ファイルの先頭）。
    compress = dict(
        compress_lossless=choices.get("compress", False),
        reduce_dpi=(reduce_dpi or CompressConfig().reduce_dpi[0]) if choices.get("reduce", False) else None,
        jpeg_quality=jpeg_quality,
        # 用紙を A4 にそろえる（4.15。寸法は設定ファイルの pdf）・付箋等の除去（4.16）。
        fit_a4=pdf_config if choices.get("fit_a4", False) and pdf_config is not None else None,
        strip_hidden=choices.get("strip_hidden", False),
    )

    if prelim.merge_group is not None:
        base_name = prelim.output_filename
        output_filename = sanitize_filename(base_name, filename_config) if sanitize_on else base_name
        return PlannedCopy(
            **compress,
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
        **compress,
        record=prelim.record,
        output_filename=output_filename,
        strip_metadata=strip_metadata_on,
        stamp_label=prelim.stamp_label if stamp_on else None,
        stamp_margin_top_mm=margin_top_mm,
        stamp_margin_right_mm=margin_right_mm,
        filename_sanitized=sanitize_on,
    )


def orientation_notice(record: FileRecord, positions: list[int]) -> str | None:
    """右のペインに出す向きの注意（4.6）。positions は、注意を出すページ（下書きの何ページ目か。
    0始まり。Workspace.orientation_pages）。なければ None。

    mints は縦向き・横向きのどちらの PDF も受け付ける（操作マニュアル別紙4）。求められるのは、
    内容の向きと用紙の向きを合わせること（mints が上の辺に付けるタイムスタンプが、文字に
    重ならないように）。ツールには内容の向きが分からないので、回すかどうかは利用者が決める
    （2026-11-17 班長指摘「縦使用でなければならないのか？」で、言い方を改めた）。
    """
    pf = record.pdf_finding
    if not positions or pf is None:
        return None
    kinds = []
    for page in pf.orientation_mismatch_pages:
        if not 0 < page <= len(pf.page_size_judgments):
            continue
        kind = "A4の横向き" if pf.page_size_judgments[page - 1] == "A4" else "A3の縦向き"
        if kind not in kinds:
            kinds.append(kind)
    pages = "、".join(str(p + 1) for p in positions)
    return (
        # 文ごとに行を改める（Tk は行頭の「、」を避けないので、長い1段落より読みやすい）。
        f"向き：{'・'.join(kinds) or '向きの違う'}のページがあります（{pages}ページ目）。\n"
        "mintsは縦向き・横向きのどちらも受け付けますが、内容の向きと用紙の向きが違うと、"
        "mintsが上の辺に付けるタイムスタンプが文字に重なります。\n"
        "内容の向きが違うページは、選んで［回転］の［左に90°回転］か［右に90°回転］で回してください。\n"
        "表など、用紙の向きどおりの内容なら、そのままで構いません。"
    )


@dataclass(frozen=True)
class PaneWarning:
    """右ペインの赤字の注意1つ分（見出しの文と、その下に並べる細目）。"""

    text: str
    details: tuple[str, ...] = ()


def build_pane_warnings(
    record: FileRecord, info_labels: list[str], is_group: bool = False, is_target: bool = True
) -> tuple[list[PaneWarning], list[str]]:
    """右ペインに出す、赤字の注意と備考を、同じ内容が二度出ないように組み立てる。

    赤字の注意（検出のみの事項。info_labels）と備考（record.notes）は、別々の所で
    作っているので、そのまま並べると同じことを二度書く（2026-11-16 班長指摘「右ペイン
    の赤字の注意と備考欄の内容が重複」）。次のように整理する。

    - 個人情報・墨消しの残存：備考にあるのは、赤字の注意の細目（どの種類が何ページ目か、
      残っている文字の例）。赤字の見出しは種類・ページを書かない短い文にし、細目を
      その下に並べる。備考からは外す。
    - 単体の上限超過・未対応形式・拡張子の不一致：備考は赤字の言い換えなので、外す。
    - 備考に残すのは、赤字にないことだけ（検出できなかった・とばした検査、ファイル名の
      JIS X 0213 外の文字など）。

    is_group：枝番ファイル結合のグループの項目を出しているとき。赤字の注意は結合する
    各ファイルの分（ファイル名つき）なので、細目への組み替えはせず、言い換えの備考を
    外すだけにする。

    is_target：更新の対象（分類A・B）か。対象でないファイル（要変換・対象外）は、
    「メタデータ検出に対応していない」のような検査の備考に意味がないので外す。

    戻り値は（赤字の注意、備考に残す文）。
    """
    pii_details = tuple(record.pii_finding.details) if record.pii_finding is not None else ()
    redaction_details = (
        tuple(record.redaction_finding.details) if record.redaction_finding is not None else ()
    )
    warnings: list[PaneWarning] = []
    moved: set[str] = set()
    for label in info_labels:
        if not is_group and "個人情報の可能性がある記載を検出しました" in label and pii_details:
            warnings.append(
                PaneWarning(
                    "個人情報の可能性がある記載を検出しました。意図した記載か確認してください",
                    pii_details,
                )
            )
            moved.update(pii_details)
        elif not is_group and "墨消し・マーカーの下に文字情報が残存している疑い" in label and redaction_details:
            warnings.append(
                PaneWarning("墨消し・マーカーの下に文字情報が残存している疑いがあります（要確認）", redaction_details)
            )
            moved.update(redaction_details)
        else:
            warnings.append(PaneWarning(label))

    restated = ("単体ファイルが", "未対応形式のため処理対象外です", "拡張子と実体（")
    notes = [n for n in record.notes if n not in moved and not n.startswith(restated)]
    if not is_target:
        notes = [n for n in notes if n != "この形式のメタデータ検出には対応していません"]
    return warnings, notes
