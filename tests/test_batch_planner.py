from pathlib import Path

from mints_prep.config import EvidenceNumberConfig, UploadConfig
from mints_prep.models import Classification, FileRecord, FilenameFinding, FormatStatus
from mints_prep.processors.batch_planner import (
    Batch,
    find_batch_conflicts,
    plan_batches,
    resolve_batch_dir,
)
from mints_prep.processors.output_writer import PlannedCopy, copy_to_output

_EVIDENCE_CONFIG = EvidenceNumberConfig(symbols=("甲", "乙", "丙"), digits=3)


def write_batches(batches: list[Batch], output_dir: Path) -> dict[int, list[Path]]:
    """テスト用ヘルパー：各バッチをそのまま書き出す。

    本番コード（ui/app.py）は逐次レビュー（1件ずつの確認）を経由して書き出す
    ため、この一括書き出しはテストのフォルダ構造検証（提出用_NNサブフォルダの
    有無）専用。旧・batch_planner.write_batchesを本番コードから削除した際に
    こちらへ移した（未使用コードを残さない方針）。
    """
    written: dict[int, list[Path]] = {}
    for batch in batches:
        batch_dir = resolve_batch_dir(batches, batch, output_dir)
        batch_dir.mkdir(parents=True, exist_ok=True)
        written[batch.index] = copy_to_output(list(batch.items), batch_dir, None)
    return written


def _upload_config(max_total_mb: float, safety_margin_ratio: float = 0.9) -> UploadConfig:
    return UploadConfig(max_total_mb=max_total_mb, safety_margin_ratio=safety_margin_ratio)


def _planned(name: str, size_bytes: int) -> PlannedCopy:
    record = FileRecord(
        path=Path(name),
        size_bytes=size_bytes,
        format_status=FormatStatus.RECORD_READY,
        detected_format="PDF",
        classification=Classification.A_COMPLIANT,
        filename_finding=FilenameFinding(
            length=len(name), exceeds_max_length=False, exceeds_warning_length=False
        ),
    )
    return PlannedCopy(record=record, output_filename=name)


def test_planning_preserves_input_order_without_sorting():
    # 要件定義書4.5節：ファイル名順・証拠番号順への並べ替えは行わず、入力順
    # （スキャン結果の一覧表示順）のまま積み上げる。
    upload_config = _upload_config(max_total_mb=1)
    scrambled = [
        _planned("甲003 c.pdf", 100),
        _planned("甲001 a.pdf", 100),
        _planned("甲002 b.pdf", 100),
    ]

    batches = plan_batches(scrambled, upload_config, _EVIDENCE_CONFIG)

    assert len(batches) == 1
    names_in_batch = [i.output_filename for i in batches[0].items]
    assert names_in_batch == ["甲003 c.pdf", "甲001 a.pdf", "甲002 b.pdf"]


def test_planning_allows_wasted_space_by_not_reordering_to_optimize():
    # 入力順の後方にある小さいファイルが、実は手前のバッチにまだ収まる場合でも、
    # 詰め替え（最適化）は行わない。無駄が生じても入力順を優先する。
    upload_config = _upload_config(max_total_mb=1)  # 安全マージン ≈ 943,718 bytes
    planned = [
        _planned("大きいファイル1.pdf", 900_000),  # 単独でバッチ1をほぼ埋める
        _planned("大きいファイル2.pdf", 900_000),  # バッチ1に入らないためバッチ2へ
        _planned("小さいファイル.pdf", 1_000),  # バッチ1にまだ余裕があっても詰めない
    ]

    batches = plan_batches(planned, upload_config, _EVIDENCE_CONFIG)

    assert len(batches) == 2
    assert [i.output_filename for i in batches[0].items] == ["大きいファイル1.pdf"]
    assert [i.output_filename for i in batches[1].items] == [
        "大きいファイル2.pdf",
        "小さいファイル.pdf",
    ]


def test_evidence_number_group_insertion_position_follows_first_member_input_order():
    # 同一証拠番号のグループは、グループ内で最初に現れたファイルの入力順を
    # 基準に挿入される（グループ全体をファイル名順に並べ替えたりはしない）。
    upload_config = _upload_config(max_total_mb=1)
    planned = [
        _planned("乙001 b.pdf", 100),
        _planned("甲001-1 a1.pdf", 100),
        _planned("甲001-2 a2.pdf", 100),
    ]

    batches = plan_batches(planned, upload_config, _EVIDENCE_CONFIG)

    assert len(batches) == 1
    names_in_batch = [i.output_filename for i in batches[0].items]
    assert names_in_batch == ["乙001 b.pdf", "甲001-1 a1.pdf", "甲001-2 a2.pdf"]


def test_single_small_batch_stays_together():
    planned = [_planned("甲001 a.pdf", 1000), _planned("甲002 b.pdf", 1000)]
    upload_config = _upload_config(max_total_mb=1)  # 1MB上限、安全マージン0.9MB

    batches = plan_batches(planned, upload_config, _EVIDENCE_CONFIG)

    assert len(batches) == 1
    assert batches[0].index == 1
    assert batches[0].folder_name == "提出用_01"
    assert [i.output_filename for i in batches[0].items] == ["甲001 a.pdf", "甲002 b.pdf"]


def test_splits_into_multiple_batches_when_exceeding_safety_margin():
    # 安全マージン = 1MB * 0.9 = 943718 bytes。各400000byteのファイルを3つ入れると
    # 1〜2個目で943718を超えるため、2バッチ以上に分かれるはず。
    upload_config = _upload_config(max_total_mb=1)
    planned = [
        _planned("甲001 a.pdf", 400_000),
        _planned("甲002 b.pdf", 400_000),
        _planned("甲003 c.pdf", 400_000),
    ]

    batches = plan_batches(planned, upload_config, _EVIDENCE_CONFIG)

    assert len(batches) >= 2
    all_items = [i.output_filename for b in batches for i in b.items]
    assert sorted(all_items) == ["甲001 a.pdf", "甲002 b.pdf", "甲003 c.pdf"]
    # 各バッチは安全マージンを超えない
    for b in batches:
        assert b.total_size_bytes <= upload_config.safe_batch_bytes


def test_batch_folder_names_are_sequential():
    upload_config = _upload_config(max_total_mb=1)
    planned = [_planned(f"甲{str(i).zfill(3)}.pdf", 500_000) for i in range(1, 4)]

    batches = plan_batches(planned, upload_config, _EVIDENCE_CONFIG)

    names = [b.folder_name for b in batches]
    assert names == [f"提出用_{i:02d}" for i in range(1, len(batches) + 1)]


def test_same_evidence_number_branch_files_stay_in_same_batch():
    # 甲001-1, 甲001-2 は同一証拠番号（枝番違い）なので分割してはならない。
    upload_config = _upload_config(max_total_mb=1)
    planned = [
        _planned("甲001-1 領収書.pdf", 400_000),
        _planned("甲001-2 領収書.pdf", 400_000),
        _planned("甲002 契約書.pdf", 400_000),
    ]

    batches = plan_batches(planned, upload_config, _EVIDENCE_CONFIG)

    batch_of = {}
    for b in batches:
        for item in b.items:
            batch_of[item.output_filename] = b.index

    assert batch_of["甲001-1 領収書.pdf"] == batch_of["甲001-2 領収書.pdf"]


def test_non_evidence_filenames_treated_as_independent_groups():
    upload_config = _upload_config(max_total_mb=1)
    planned = [
        _planned("準備書面1.pdf", 100),
        _planned("準備書面2.pdf", 100),
    ]

    batches = plan_batches(planned, upload_config, _EVIDENCE_CONFIG)

    assert len(batches) == 1
    assert len(batches[0].items) == 2


def test_empty_input_produces_no_batches():
    upload_config = _upload_config(max_total_mb=1)
    assert plan_batches([], upload_config, _EVIDENCE_CONFIG) == []


def test_oversized_single_group_not_split():
    # 1グループだけで安全マージンを超える場合でも、証拠番号のまとまりを
    # 優先し、そのファイル分割自体はこのモジュールの対象外とする。
    upload_config = _upload_config(max_total_mb=1)
    planned = [_planned("甲001 巨大ファイル.pdf", 2_000_000)]

    batches = plan_batches(planned, upload_config, _EVIDENCE_CONFIG)

    assert len(batches) == 1
    assert batches[0].total_size_bytes == 2_000_000


def _planned_real_file(directory: Path, name: str, content: bytes = b"data") -> PlannedCopy:
    path = directory / name
    path.write_bytes(content)
    record = FileRecord(
        path=path,
        size_bytes=len(content),
        format_status=FormatStatus.RECORD_READY,
        detected_format="PDF",
        classification=Classification.A_COMPLIANT,
        filename_finding=FilenameFinding(
            length=len(name), exceeds_max_length=False, exceeds_warning_length=False
        ),
    )
    return PlannedCopy(record=record, output_filename=name)


def test_write_batches_creates_submission_subfolders_when_split_occurs(tmp_path: Path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    upload_config = _upload_config(max_total_mb=1)
    planned = [
        _planned_real_file(src_dir, "甲001 a.pdf", b"x" * 400_000),
        _planned_real_file(src_dir, "甲002 b.pdf", b"y" * 400_000),
        _planned_real_file(src_dir, "甲003 c.pdf", b"z" * 400_000),
    ]
    batches = plan_batches(planned, upload_config, _EVIDENCE_CONFIG)
    assert len(batches) >= 2  # 前提：分割が発生すること

    written = write_batches(batches, out_dir)

    for batch in batches:
        batch_dir = out_dir / batch.folder_name
        assert batch_dir.is_dir()
        for item in batch.items:
            assert (batch_dir / item.output_filename).exists()
    assert sum(len(paths) for paths in written.values()) == 3


def test_write_batches_writes_directly_to_output_dir_when_single_batch(tmp_path: Path):
    # 要件定義書 第3章処理フロー⑥：サブフォルダ化は「合計サイズが200MBを
    # 超える場合」のみ。1バッチで収まる場合は提出用_01を作らず出力フォルダ
    # 直下に書き出す。
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    upload_config = _upload_config(max_total_mb=200)  # 十分大きい上限
    planned = [_planned_real_file(src_dir, "甲001 a.pdf", b"small")]
    batches = plan_batches(planned, upload_config, _EVIDENCE_CONFIG)
    assert len(batches) == 1

    written = write_batches(batches, out_dir)

    assert (out_dir / "甲001 a.pdf").exists()
    assert not (out_dir / "提出用_01").exists()
    assert written[1] == [out_dir / "甲001 a.pdf"]


def test_find_batch_conflicts_uses_submission_subfolder_when_split_occurs(tmp_path: Path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    out_dir = tmp_path / "out"
    (out_dir / "提出用_01").mkdir(parents=True)
    (out_dir / "提出用_01" / "甲001 a.pdf").write_bytes(b"already here")

    upload_config = _upload_config(max_total_mb=1)
    planned = [
        _planned_real_file(src_dir, "甲001 a.pdf", b"x" * 400_000),
        _planned_real_file(src_dir, "甲002 b.pdf", b"y" * 400_000),
        _planned_real_file(src_dir, "甲003 c.pdf", b"z" * 400_000),
    ]
    batches = plan_batches(planned, upload_config, _EVIDENCE_CONFIG)
    assert len(batches) >= 2  # 前提：分割が発生すること

    conflicts = find_batch_conflicts(batches, out_dir)

    assert conflicts == ["提出用_01/甲001 a.pdf"]


def test_find_batch_conflicts_checks_output_dir_directly_when_single_batch(tmp_path: Path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "甲001 a.pdf").write_bytes(b"already here")

    upload_config = _upload_config(max_total_mb=200)
    planned = [_planned_real_file(src_dir, "甲001 a.pdf")]
    batches = plan_batches(planned, upload_config, _EVIDENCE_CONFIG)
    assert len(batches) == 1

    conflicts = find_batch_conflicts(batches, out_dir)

    assert conflicts == ["甲001 a.pdf"]


def test_find_batch_conflicts_empty_when_no_overlap(tmp_path: Path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    upload_config = _upload_config(max_total_mb=1)
    planned = [_planned_real_file(src_dir, "甲001 a.pdf")]
    batches = plan_batches(planned, upload_config, _EVIDENCE_CONFIG)

    assert find_batch_conflicts(batches, out_dir) == []
