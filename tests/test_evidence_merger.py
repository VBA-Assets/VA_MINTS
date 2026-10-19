from pathlib import Path

from pypdf import PdfReader

from fixtures.generate_fixtures import make_a4_pdf, make_image_without_metadata, make_password_pdf
from mints_prep.config import EvidenceNumberConfig, EvidenceStampConfig
from mints_prep.models import Classification, FileRecord, FilenameFinding, FormatStatus, PdfFinding
from mints_prep.processors.evidence_merger import find_branch_merge_groups, merge_branch_group

_EVIDENCE_CONFIG = EvidenceNumberConfig(symbols=("甲", "乙", "丙"), digits=3)
_STAMP_CONFIG = EvidenceStampConfig(font_size_pt=10, margin_top_mm=8, margin_right_mm=8)


def _record(path: Path, detected_format: str = "PDF", pdf_finding: PdfFinding | None = None) -> FileRecord:
    return FileRecord(
        path=path,
        size_bytes=path.stat().st_size if path.exists() else 100,
        format_status=FormatStatus.RECORD_READY,
        detected_format=detected_format,
        classification=Classification.A_COMPLIANT,
        filename_finding=FilenameFinding(
            length=len(path.name), exceeds_max_length=False, exceeds_warning_length=False
        ),
        pdf_finding=pdf_finding,
    )


def test_finds_branch_group_with_two_or_more_members(tmp_path: Path):
    records = [
        _record(make_a4_pdf(tmp_path / "甲001-1 領収書.pdf")),
        _record(make_a4_pdf(tmp_path / "甲001-2 領収書.pdf")),
        _record(make_a4_pdf(tmp_path / "甲001-3 領収書.pdf")),
    ]

    groups = find_branch_merge_groups(records, _EVIDENCE_CONFIG)

    assert len(groups) == 1
    assert [m.match.branch_from for m in groups[0].members] == ["1", "2", "3"]
    assert groups[0].output_filename == "甲001-1~3 領収書.pdf"


def test_output_filename_preserves_underscore_title_separator(tmp_path: Path):
    # 代表ファイル（最小枝番）が標目との区切りに"_"を使っていた場合、結合後の
    # ファイル名もツール既定の空白ではなく、その"_"をそのまま踏襲する（ツールは
    # ファイル名の内容の適否を判断できないため、独自の区切り文字を押し付けない）。
    records = [
        _record(make_a4_pdf(tmp_path / "甲001_1_売買契約書A.pdf")),
        _record(make_a4_pdf(tmp_path / "甲001_2_売買契約書B.pdf")),
    ]

    groups = find_branch_merge_groups(records, _EVIDENCE_CONFIG)

    assert len(groups) == 1
    assert groups[0].output_filename == "甲001-1~2_売買契約書A.pdf"


def test_output_filename_preserves_space_title_separator(tmp_path: Path):
    records = [
        _record(make_a4_pdf(tmp_path / "甲001_1 売買契約書A.pdf")),
        _record(make_a4_pdf(tmp_path / "甲001_2 売買契約書B.pdf")),
    ]

    groups = find_branch_merge_groups(records, _EVIDENCE_CONFIG)

    assert len(groups) == 1
    assert groups[0].output_filename == "甲001-1~2 売買契約書A.pdf"


def test_single_branch_file_not_grouped(tmp_path: Path):
    records = [_record(make_a4_pdf(tmp_path / "甲001-1 領収書.pdf"))]

    groups = find_branch_merge_groups(records, _EVIDENCE_CONFIG)

    assert groups == []


def test_non_branch_files_not_grouped(tmp_path: Path):
    records = [
        _record(make_a4_pdf(tmp_path / "甲001 契約書.pdf")),
        _record(make_a4_pdf(tmp_path / "甲002 契約書.pdf")),
    ]

    groups = find_branch_merge_groups(records, _EVIDENCE_CONFIG)

    assert groups == []


def test_already_ranged_filename_not_grouped(tmp_path: Path):
    # 「甲001-1~3」のように既に範囲表記済みのファイルは結合対象外
    # （既に結合済みとみなす）。
    records = [
        _record(make_a4_pdf(tmp_path / "甲001-1~3 領収書.pdf")),
        _record(make_a4_pdf(tmp_path / "甲001-4 領収書.pdf")),
    ]

    groups = find_branch_merge_groups(records, _EVIDENCE_CONFIG)

    assert groups == []


def test_image_branch_files_excluded_v1_scope(tmp_path: Path):
    # v1スコープ：結合はPDF限定。画像の形式変換を伴う結合は対象外。
    records = [
        _record(make_image_without_metadata(tmp_path / "甲001-1.png"), detected_format="PNG"),
        _record(make_image_without_metadata(tmp_path / "甲001-2.png"), detected_format="PNG"),
    ]

    groups = find_branch_merge_groups(records, _EVIDENCE_CONFIG)

    assert groups == []


def test_password_protected_pdf_excluded_from_merge_candidacy(tmp_path: Path):
    # パスワード付きPDFは復号キーがなければページを読めず、結合処理
    # （ページの読み取り・スタンプ）自体が実行できないため対象外とする。
    records = [
        _record(
            make_password_pdf(tmp_path / "甲001-1 領収書.pdf"),
            pdf_finding=PdfFinding(has_password=True),
        ),
        _record(make_a4_pdf(tmp_path / "甲001-2 領収書.pdf")),
    ]

    groups = find_branch_merge_groups(records, _EVIDENCE_CONFIG)

    assert groups == []


def test_different_base_numbers_form_separate_groups(tmp_path: Path):
    records = [
        _record(make_a4_pdf(tmp_path / "甲001-1 a.pdf")),
        _record(make_a4_pdf(tmp_path / "甲001-2 a.pdf")),
        _record(make_a4_pdf(tmp_path / "乙A002-1 b.pdf")),
        _record(make_a4_pdf(tmp_path / "乙A002-2 b.pdf")),
    ]

    groups = find_branch_merge_groups(records, _EVIDENCE_CONFIG)

    assert len(groups) == 2
    labels = {g.representative.match.symbol + g.representative.match.subletter + g.representative.match.number for g in groups}
    assert labels == {"甲001", "乙A002"}


def test_merge_branch_group_concatenates_pages_in_branch_order(tmp_path: Path):
    records = [
        _record(make_a4_pdf(tmp_path / "甲001-2 b.pdf")),  # 入力順は2が先
        _record(make_a4_pdf(tmp_path / "甲001-1 a.pdf")),
    ]
    groups = find_branch_merge_groups(records, _EVIDENCE_CONFIG)
    assert len(groups) == 1
    group = groups[0]
    # 枝番の昇順（1, 2）に並んでいること（入力順ではない）。
    assert [m.match.branch_from for m in group.members] == ["1", "2"]

    dest = tmp_path / group.output_filename
    merge_branch_group(group, dest, stamp=False, stamp_config=None)

    merged = PdfReader(str(dest))
    assert len(merged.pages) == 2


def test_merge_branch_group_stamps_each_source_with_its_own_branch_label(tmp_path: Path):
    records = [
        _record(make_a4_pdf(tmp_path / "甲001-1 a.pdf")),
        _record(make_a4_pdf(tmp_path / "甲001-2 b.pdf")),
    ]
    groups = find_branch_merge_groups(records, _EVIDENCE_CONFIG)
    group = groups[0]
    dest = tmp_path / group.output_filename

    merge_branch_group(group, dest, stamp=True, stamp_config=_STAMP_CONFIG)

    merged = PdfReader(str(dest))
    assert "甲001-1" in merged.pages[0].extract_text()
    assert "甲001-2" in merged.pages[1].extract_text()


def test_merge_stamps_rotated_member_upright(tmp_path: Path):
    """回転属性（/Rotate 90）付きメンバーの結合スタンプの回帰テスト。

    単独ファイル（evidence_stamp.stamp_pdf）には2026-07-25の点検で回転正規化が
    入ったが、結合経路（merge_branch_group）に同じ処理が漏れており、閲覧時の
    右上でない隅に90度回転したスタンプが載っていた。正規化により/Rotateは0に
    なり、ラベルが正しく抽出できる。"""
    from fixtures.generate_fixtures import make_rotated_pdf

    records = [
        _record(make_rotated_pdf(tmp_path / "甲001-1 a.pdf", rotate=90)),
        _record(make_a4_pdf(tmp_path / "甲001-2 b.pdf")),
    ]
    groups = find_branch_merge_groups(records, _EVIDENCE_CONFIG)
    group = groups[0]
    dest = tmp_path / group.output_filename

    merge_branch_group(group, dest, stamp=True, stamp_config=_STAMP_CONFIG)

    merged = PdfReader(str(dest))
    # 回転属性はページ内容へ転写され正規化される（見た目は不変のまま/Rotate=0）
    assert merged.pages[0].rotation % 360 == 0
    assert "甲001-1" in merged.pages[0].extract_text()
    assert "甲001-2" in merged.pages[1].extract_text()


def test_merge_without_stamp_preserves_member_rotation(tmp_path: Path):
    """スタンプなしの結合では、メンバーの回転属性をそのまま維持する
    （原本忠実性：スタンプしないのに内容へ手を加えない）。"""
    from fixtures.generate_fixtures import make_rotated_pdf

    records = [
        _record(make_rotated_pdf(tmp_path / "甲001-1 a.pdf", rotate=90)),
        _record(make_a4_pdf(tmp_path / "甲001-2 b.pdf")),
    ]
    groups = find_branch_merge_groups(records, _EVIDENCE_CONFIG)
    group = groups[0]
    dest = tmp_path / group.output_filename

    merge_branch_group(group, dest, stamp=False, stamp_config=None)

    merged = PdfReader(str(dest))
    assert merged.pages[0].rotation % 360 == 90


def test_duplicate_branch_numbers_excluded_from_merge(tmp_path):
    """同一枝番（甲001-1が2ファイル）を含むグループは結合候補にしない
    （「甲001-1~1」のような不自然な結合を防ぐ。重複は連番チェックが警告する）。"""
    records = [
        _record(make_a4_pdf(tmp_path / "甲001-1 領収書.pdf")),
        _record(make_a4_pdf(tmp_path / "甲001-1 領収書（控）.pdf")),
    ]
    groups = find_branch_merge_groups(records, _EVIDENCE_CONFIG)
    assert groups == []
