"""200MB分割（複数回提出の仕分け）（要件定義書 4.5）。

1回のアップロード合計上限（200MB。Q&A 9頁・一次確定）を超える場合に備え、
書き出し対象ファイルを提出回ごとのバッチへ仕分ける。ファイル自体の分割・
変更は一切行わない（貪欲法によるグルーピングのみ）。

グルーピング方法：処理済みファイルを**入力順（スキャン時の列挙順＝画面の
一覧に表示される順序）のまま**貪欲法で積み上げ、累積サイズが安全マージン
（config.yaml upload.safety_margin_ratio、既定90%＝180MB）に達する直前で
区切り、次のバッチへ移る。ファイル名順・証拠番号順への並べ替えは行わない
（利用者の処理イメージ〔画面表示順＝処理順〕に近いことを優先する）。

ただし、同一証拠番号（枝番違い含む）のファイル群は分割せず同一バッチに
まとめる。この場合も入力順の原則は維持し、そのグループの挿入位置は
グループ内で最初に現れたファイルの入力順を基準とする（グループ全体を
ファイル名順に並べ替えたりはしない）。

入力順を優先する結果、後方の入力順にある小さいファイルが、実際には
まだ空きのある手前のバッチに収まる場合でも、そちらへ詰め替える最適化は
行わない（バッチ間に多少の空きが生じることを許容する。要件定義書4.5節）。

ファイル内容を変更しない処理のため、CLAUDE.md「絶対に譲れない設計原則」3の
確認UI原則の対象外（実行可否の確認は不要）。ただし、分割が発生する場合
（バッチが2つ以上になる場合）は、処理完了時に画面上で明示的に通知する
ことが必須（要件定義書4.5節「通知の必須化」）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mints_prep.config import EvidenceNumberConfig, UploadConfig
from mints_prep.naming.evidence_number import match_evidence_number
from mints_prep.processors.output_writer import PlannedCopy, find_existing_conflicts


@dataclass(frozen=True)
class Batch:
    index: int  # 1始まり
    items: tuple[PlannedCopy, ...]

    @property
    def folder_name(self) -> str:
        return f"提出用_{self.index:02d}"

    @property
    def total_size_bytes(self) -> int:
        return sum(item.record.size_bytes for item in self.items)


def _group_key(item: PlannedCopy, evidence_config: EvidenceNumberConfig) -> str:
    """同一証拠番号（符号＋添字＋番号。枝番は問わない）でグルーピングするための
    キーを求める。証拠番号パターンに合致しないファイルは、そのファイル単独を
    1グループとする。"""
    match = match_evidence_number(item.output_filename, evidence_config)
    if match is None:
        return f"__standalone__:{item.output_filename}"
    return f"{match.symbol}{match.subletter}{match.number}"


def plan_batches(
    planned: list[PlannedCopy],
    upload_config: UploadConfig,
    evidence_config: EvidenceNumberConfig,
) -> list[Batch]:
    """書き出し対象ファイルを提出回ごとのバッチへ仕分ける（貪欲法）。

    要件定義書4.5節：ファイル名順・証拠番号順への並べ替えは行わず、入力順
    （＝呼び出し側から渡された順序。通常はスキャン結果の一覧表示順）を
    そのまま用いる。ただし同一証拠番号のファイル群はグルーピングし、その
    挿入位置はグループ内で最初に現れたファイルの入力順を基準とする。
    """
    groups: dict[str, list[PlannedCopy]] = {}
    order: list[str] = []
    for item in planned:
        key = _group_key(item, evidence_config)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(item)

    safe_limit = upload_config.safe_batch_bytes
    batches: list[list[PlannedCopy]] = [[]]
    current_size = 0
    for key in order:
        group_items = groups[key]
        group_size = sum(i.record.size_bytes for i in group_items)
        # 現在のバッチが空でない状態でこのグループを足すと安全マージンを
        # 超える場合のみ次のバッチへ移る（グループ自体が単独で上限を
        # 超える場合は、証拠番号のまとまりを優先し分割しない＝そのまま
        # 現在のバッチに入れる。ファイル分割は別途4.6節の対応事項）。
        if current_size > 0 and current_size + group_size > safe_limit:
            batches.append([])
            current_size = 0
        batches[-1].extend(group_items)
        current_size += group_size

    return [Batch(index=i + 1, items=tuple(b)) for i, b in enumerate(batches) if b]


def resolve_batch_dir(batches: list[Batch], batch: Batch, output_dir: Path) -> Path:
    """バッチの実際の書き出し先を決める。

    要件定義書 第3章処理フロー⑥：「合計サイズが200MBを超える場合は
    『提出用_01』『提出用_02』等のサブフォルダに事前仕分けし」とあるとおり、
    サブフォルダ化は分割が発生する場合（バッチが2つ以上）に限る。1バッチで
    収まる場合（＝分割不要）は出力フォルダ直下にそのまま書き出す。
    """
    if len(batches) <= 1:
        return output_dir
    return output_dir / batch.folder_name


def find_batch_conflicts(batches: list[Batch], output_dir: Path) -> list[str]:
    """各バッチの出力先に既に同名ファイルがないかを列挙する（上書き警告用）。

    分割が発生する場合は「提出用_NN/ファイル名」、1バッチで収まる場合は
    「ファイル名」のみの形式で返す（resolve_batch_dir参照）。
    """
    conflicts: list[str] = []
    for batch in batches:
        batch_dir = resolve_batch_dir(batches, batch, output_dir)
        batch_conflicts = find_existing_conflicts(list(batch.items), batch_dir)
        prefix = f"{batch.folder_name}/" if len(batches) > 1 else ""
        conflicts.extend(f"{prefix}{name}" for name in batch_conflicts)
    return conflicts
