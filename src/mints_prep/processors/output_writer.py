"""出力フォルダへの書き出し（要件定義書 第3章「処理フロー」⑥）。

書き出し対象とするのは、A（適合済み）・B（要修正）のうち記録対応形式
（RECORD_READY）のファイルのみ。C（要変換）は変換機能自体を実装しない
（4.4節の形式変換機能はチェック専念方針により廃止）ため対象外。D（処理
対象外）も書き出さない。

ファイル名整形（4.5）・メタデータ除去（4.5）の適用要否は、ユーザーが
確認ダイアログで「実行」を選んだ結果を呼び出し側（GUI）から受け取る。
本モジュール自体はConfirmationGateを経由しない（既に確認済みの計画を
実行するのみ）。

元ファイルは変更しない（コピーのみ）。CLAUDE.md「非機能要件」の「処理の
可逆性：元ファイルは変更せず、出力フォルダへの書き出しのみを行う」に対応する。
"""

from __future__ import annotations

import dataclasses
import shutil
from dataclasses import dataclass
from pathlib import Path

from mints_prep.config import EvidenceStampConfig, FilenameConfig
from mints_prep.detectors.filename_detector import check_filename
from mints_prep.models import Classification, FileRecord, FormatStatus, MetadataFinding
from mints_prep.processors.evidence_merger import BranchGroup, merge_branch_group
from mints_prep.processors.evidence_stamp import stamp_file
from mints_prep.processors.metadata_remover import strip_metadata
from mints_prep.processors.orientation_fixer import rotate_pdf_pages


@dataclass(frozen=True)
class PlannedCopy:
    record: FileRecord
    output_filename: str
    strip_metadata: bool = False
    stamp_label: str | None = None  # 4.7 右上スタンプ（単独ファイル用）
    merge_group: BranchGroup | None = None  # 4.7 枝番ファイル結合（設定時はrecordの代わりにこちらを処理）
    merge_stamp: bool = False  # 枝番ファイル結合時、各ソースを自身の枝番ラベルでスタンプするか
    # 要件定義書4.7「スタンプ位置調整」：このファイル（又は結合グループ）のみに
    # 適用する余白の上書き値。Noneならevidence_stamp_configの既定値を使う。
    # config.yamlの既定値自体は変更しない（このファイルにのみ適用する）。
    stamp_margin_top_mm: float | None = None
    stamp_margin_right_mm: float | None = None
    # ファイル名整形（4.5）を実際に適用したか。レポートの「適用処理」欄は
    # このフラグで判定する（従来の「出力名≠元の名前」比較では、同名衝突回避の
    # 連番付与や結合後ファイル名まで「ファイル名整形」と誤記載されるため）。
    filename_sanitized: bool = False
    # 同名衝突回避の連番付与（_resolve_name_collisions／画面側のdedupe）で
    # 名前が変わったか。レポートで整形とは別の適用処理として記載する。
    renamed_for_collision: bool = False
    # 向き補正（4.6の向き不一致に対する処理。processors/orientation_fixer.py）：
    # 向き不一致として検出されたページ（record.pdf_finding.
    # orientation_mismatch_pages）を、この角度（時計回り・90又は270）だけ
    # /Rotate属性で回転して書き出す。Noneなら回転しない。単独ファイルの
    # PDFのみ対象（結合グループでは提供しない）。
    rotate_degrees: int | None = None


def select_eligible_records(records: list[FileRecord]) -> list[FileRecord]:
    """書き出し対象（A・Bかつ記録対応形式）のみを抽出する。"""
    return [
        r
        for r in records
        if r.classification in (Classification.A_COMPLIANT, Classification.B_NEEDS_FIX)
        and r.format_status == FormatStatus.RECORD_READY
    ]


def _synthetic_merge_record(group: BranchGroup, filename_config: FilenameConfig) -> FileRecord:
    """枝番ファイル結合の結果としてこれから生成される（まだディスク上に存在しない）
    マージ後ファイルを表す仮のFileRecordを作る。バッチ計画（サイズ集計・
    証拠番号グルーピング）が既存のPlannedCopy.record前提の仕組みをそのまま
    使えるようにするための橋渡し。サイズはソースファイルの合計値（実際の
    マージ後サイズはページオブジェクトの共有等によりこれ以下になるのが
    通常で、200MB分割の安全マージン計算上は安全側〔過大〕の見積りとなる）。

    filename_finding は生成される結合ファイル名（複数の標目・範囲表記を
    連結したもの）自体が100文字超過・禁止文字を含む可能性があるため、
    通常のファイルと同じ検証関数（check_filename）で正しく算出する。
    metadata_finding はメンバーいずれかにメタデータがあれば「あり」とし、
    検出項目はメンバー間の和集合とする（結合後ファイルにもその処理選択肢を
    一貫して提示するため）。
    """
    rep = group.representative.record
    detected_fields = tuple(
        sorted(
            {
                f
                for m in group.members
                if m.record.metadata_finding is not None
                for f in m.record.metadata_finding.detected_fields
            }
        )
    )
    has_metadata = any(
        m.record.metadata_finding is not None and m.record.metadata_finding.has_metadata
        for m in group.members
    )
    return FileRecord(
        path=rep.path.parent / group.output_filename,
        size_bytes=group.total_size_bytes,
        format_status=rep.format_status,
        detected_format=rep.detected_format,
        classification=Classification.A_COMPLIANT,
        filename_finding=check_filename(group.output_filename, filename_config),
        metadata_finding=MetadataFinding(has_metadata=has_metadata, detected_fields=detected_fields),
    )


def plan_output_filenames(
    records: list[FileRecord],
    sanitized_names: dict[Path, str],
    strip_metadata_paths: set[Path] | None = None,
    stamp_labels: dict[Path, str] | None = None,
    merge_groups: list[BranchGroup] | None = None,
    stamp_merge_sources: bool = False,
    filename_config: FilenameConfig | None = None,
) -> list[PlannedCopy]:
    """書き出し対象ファイルと、それぞれの出力ファイル名・処理内容を決定する。

    sanitized_names: ファイル名整形の確認で「実行」が選択された場合の
    {元のパス: 整形後ファイル名} 辞書。整形を実行しない（未確認・スキップされた）
    ファイルは、元のファイル名のまま書き出す対象とする。
    strip_metadata_paths: メタデータ除去の確認で「実行」が選択されたファイルの
    パス集合。
    stamp_labels: 証拠番号スタンプ（4.7）の確認で「実行」が選択された、
    単独ファイル用の {元のパス: スタンプラベル} 辞書。
    merge_groups: 枝番ファイル結合（4.7）の確認で「実行」が選択されたグループ一覧。
    このrecordsには、結合対象となった枝番ファイル自体は含めないこと
    （呼び出し側で除外してから渡す）。merge_groupsを渡す場合、
    filename_configも必須（結合後ファイル名の検証に使用）。
    stamp_merge_sources: 結合後のページに、各ソースの枝番ラベルをスタンプするか。
    """
    strip_metadata_paths = strip_metadata_paths or set()
    stamp_labels = stamp_labels or {}
    planned = [
        PlannedCopy(
            record=r,
            output_filename=sanitized_names.get(r.path, r.path.name),
            strip_metadata=r.path in strip_metadata_paths,
            stamp_label=stamp_labels.get(r.path),
            filename_sanitized=r.path in sanitized_names,
        )
        for r in records
    ]
    for group in merge_groups or []:
        assert filename_config is not None, "merge_groups指定時はfilename_configが必須です"
        planned.append(
            PlannedCopy(
                record=_synthetic_merge_record(group, filename_config),
                output_filename=group.output_filename,
                merge_group=group,
                merge_stamp=stamp_merge_sources,
            )
        )
    return _resolve_name_collisions(planned, filename_config)


def make_deduped_name(
    name: str, seen_count: int, filename_config: FilenameConfig | None = None
) -> str:
    """同名衝突回避のため「幹_連番.拡張子」形式の代替名を作る。

    連番を付け足すと、100文字ちょうどまで切り詰め済みの整形結果が
    上限を超えてしまう（整形機能自身が違反名を作る）ため、filename_configが
    与えられた場合は付与後の全長が上限に収まるよう幹を再切り詰めする。
    """
    stem = Path(name).stem
    suffix = Path(name).suffix
    counter = f"_{seen_count}"
    if filename_config is not None:
        max_stem = filename_config.max_length - len(suffix) - len(counter)
        if max_stem > 0:
            stem = stem[:max_stem]
    return f"{stem}{counter}{suffix}"


def _resolve_name_collisions(
    planned: list[PlannedCopy], filename_config: FilenameConfig | None = None
) -> list[PlannedCopy]:
    """出力ファイル名が重複する場合、連番を付けて区別する。

    mints側も同名ファイルの同時アップロードは許容しない（別紙2エラー#52）ため、
    出力フォルダ内で同名衝突が起きないようにする。判定キーはcasefoldで正規化
    する（Windows〔NTFS〕のファイル名は大文字小文字を区別しないため、拡張子の
    大小違い〔.PDF/.pdf〕だけが異なる名前は実際には衝突・上書きになる）。
    """
    seen: dict[str, int] = {}
    resolved: list[PlannedCopy] = []
    for item in planned:
        name = item.output_filename
        key = name.casefold()
        if key not in seen:
            seen[key] = 1
            resolved.append(item)
            continue
        seen[key] += 1
        new_name = make_deduped_name(name, seen[key], filename_config)
        while new_name.casefold() in seen:
            seen[key] += 1
            new_name = make_deduped_name(name, seen[key], filename_config)
        seen[new_name.casefold()] = 1
        resolved.append(
            dataclasses.replace(item, output_filename=new_name, renamed_for_collision=True)
        )
    return resolved


def find_existing_conflicts(planned: list[PlannedCopy], output_dir: Path) -> list[str]:
    """出力先フォルダに既に同名ファイルが存在するものを列挙する（上書き警告用）。

    書き出し実行時に、出力フォルダに既存のファイルがある場合はそのまま
    上書きされてしまうため、実行前に一覧化してユーザーに警告・キャンセルの
    機会を与える（ファイルの内容を変更する処理ではないためCLAUDE.mdの
    確認UI原則〔設計原則3〕そのものの対象ではないが、既存ファイルの意図しない
    上書きを防ぐための別種の安全確認として設ける）。
    """
    return [
        item.output_filename for item in planned if (output_dir / item.output_filename).exists()
    ]


def _effective_stamp_config(
    config: EvidenceStampConfig, item: PlannedCopy
) -> EvidenceStampConfig:
    """このitemにスタンプ位置調整（4.7節）の上書きがあれば反映した設定を返す。

    config.yamlの既定値自体（引数のconfig）は変更しない。上書きがなければ
    そのまま返す。
    """
    if item.stamp_margin_top_mm is None and item.stamp_margin_right_mm is None:
        return config
    return dataclasses.replace(
        config,
        margin_top_mm=(
            item.stamp_margin_top_mm if item.stamp_margin_top_mm is not None else config.margin_top_mm
        ),
        margin_right_mm=(
            item.stamp_margin_right_mm
            if item.stamp_margin_right_mm is not None
            else config.margin_right_mm
        ),
    )


def copy_to_output(
    planned: list[PlannedCopy],
    output_dir: Path,
    evidence_stamp_config: EvidenceStampConfig | None = None,
) -> list[Path]:
    """計画に基づき、実際にファイルを出力フォルダへコピーする（元ファイルには触れない）。

    evidence_stamp_config: スタンプ（stamp_label）又は枝番ファイル結合時のスタンプ
    （merge_stamp）を伴う項目が1件でもある場合は必須。

    戻り値は書き出したファイルのパス一覧。
    """
    written: list[Path] = []
    for item in planned:
        dest = output_dir / item.output_filename
        effective_stamp_config = (
            _effective_stamp_config(evidence_stamp_config, item)
            if evidence_stamp_config is not None
            else None
        )

        # 向き補正（4.6）：他の処理（スタンプ→メタデータ除去）より先に、
        # 検出済みの向き不一致ページの/Rotate属性を補正した一時ファイルを
        # 作り、以降の処理はそれを入力とする（スタンプは回転補正後の
        # 「閲覧時の右上」に載る）。結合グループでは提供しない。
        source_path = item.record.path
        rotate_tmp: Path | None = None
        if item.rotate_degrees is not None and item.merge_group is None:
            mismatch_pages = (
                item.record.pdf_finding.orientation_mismatch_pages
                if item.record.pdf_finding is not None
                else ()
            )
            if mismatch_pages:
                rotate_tmp = dest.with_name(dest.name + ".rotate_tmp")
                rotate_pdf_pages(
                    item.record.path, rotate_tmp, mismatch_pages, item.rotate_degrees
                )
                source_path = rotate_tmp

        try:
            if item.merge_group is not None:
                assert effective_stamp_config is not None or not item.merge_stamp
                if item.strip_metadata:
                    # 結合後PDF（pypdfで生成）にもメタデータが残り得るため、
                    # 単独ファイルのスタンプ→メタデータ除去と同様に一時ファイルを
                    # 経由する。途中で例外が出ても一時ファイルを出力フォルダに
                    # 残さないようfinallyで削除する。
                    tmp = dest.with_name(dest.name + ".merge_tmp")
                    try:
                        merge_branch_group(item.merge_group, tmp, item.merge_stamp, effective_stamp_config)
                        strip_metadata(tmp, dest, item.record.detected_format)
                    finally:
                        tmp.unlink(missing_ok=True)
                else:
                    merge_branch_group(item.merge_group, dest, item.merge_stamp, effective_stamp_config)
            elif item.stamp_label is not None:
                assert effective_stamp_config is not None
                if item.strip_metadata:
                    # スタンプ処理（pypdf/reportlab）が付与し得るメタデータを
                    # 最終的に除去するため、スタンプ→メタデータ除去の順で
                    # 一時ファイルを経由する。途中で例外が出ても一時ファイルを
                    # 出力フォルダに残さないようfinallyで削除する。
                    tmp = dest.with_name(dest.name + ".stamp_tmp")
                    try:
                        stamp_file(
                            source_path, tmp, item.stamp_label, effective_stamp_config,
                            item.record.detected_format,
                        )
                        strip_metadata(tmp, dest, item.record.detected_format)
                    finally:
                        tmp.unlink(missing_ok=True)
                else:
                    stamp_file(
                        source_path, dest, item.stamp_label, effective_stamp_config,
                        item.record.detected_format,
                    )
            elif item.strip_metadata:
                strip_metadata(source_path, dest, item.record.detected_format)
            else:
                shutil.copy2(source_path, dest)
        finally:
            if rotate_tmp is not None:
                rotate_tmp.unlink(missing_ok=True)
        written.append(dest)
    return written
