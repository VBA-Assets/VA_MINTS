"""枝番ファイル結合（要件定義書 4.7）。

準備の手引29頁：「枝番のある複数の領収書等を1ファイルで出す場合は『甲001-1〜20』
と入力し、各枝番を区別できるよう証拠データ右上に...記載する」に基づき、同一の
証拠番号（符号＋添字＋番号）を共有し、枝番違いで別ファイルとなっている書証群を
1つのPDFへ結合する。

スコープ：**結合はPDF形式の枝番ファイル同士に限る。** 画像（JPEG/PNG）を
PDFページへ変換して結合することは、チェック専念方針により廃止した
「形式変換機能」（4.4節）に該当するため対象外とする（画像の枝番ファイルは、
結合せず個別に4.7の右上スタンプのみ適用できる）。

この処理はファイルの内容を変更する（複数ファイルを1ファイルへ統合する）ため、
CLAUDE.md「絶対に譲れない設計原則」3に従い、呼び出し側（GUI）で必ず実行前に
ユーザーの確認を得ること。本モジュール自体は確認済みの実行を担うのみ。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfWriter

from mints_prep.config import EvidenceNumberConfig, EvidenceStampConfig
from mints_prep.models import EvidenceNumberMatch, FileRecord
from mints_prep.naming.evidence_number import match_evidence_number
from mints_prep.processors.evidence_stamp import stamp_pdf_page


@dataclass(frozen=True)
class BranchMember:
    record: FileRecord
    match: EvidenceNumberMatch


@dataclass(frozen=True)
class BranchGroup:
    """同一証拠番号を共有し、単一の枝番（範囲でない）を持つファイル群。

    members はファイル名中の枝番の昇順（数値として比較。数値化できない場合は
    文字列順）に並べる。範囲表記の起点・終点はグループ内の最小・最大の枝番から
    導出し、個々のファイルの入力順（メンバー間の元の並び）には依存しない。
    """

    members: tuple[BranchMember, ...]

    @property
    def representative(self) -> BranchMember:
        return self.members[0]

    @property
    def total_size_bytes(self) -> int:
        return sum(m.record.size_bytes for m in self.members)

    @property
    def output_filename(self) -> str:
        # 標目・区切り文字はグループ先頭（最小枝番）のファイルのものを採用する。
        # 標目については、メンバー間で異なる場合（「甲001-1 領収書A」「甲001-2
        # 領収書B」等）、2件目以降の標目は結合後ファイル名には反映されない
        # （確認画面〔枝番ファイル結合のチェックリスト〕には全メンバーの
        # ファイル名が表示されるため、利用者はそこで結合可否を判断できる）。
        # 区切り文字（標目の前の空白・"-"・"_"等）も、ツールが独自の書式を
        # 押し付けるのではなく、利用者がその代表ファイルに元々使っていた
        # 表記（title_separator）をそのまま踏襲する（4.3節「ファイル名の内容の
        # 適否を判断できない」原則。CLAUDE.md参照）。
        rep = self.representative.match
        branches = [m.match.branch_from for m in self.members]
        low, high = branches[0], branches[-1]
        base = f"{rep.symbol}{rep.subletter}{rep.number}-{low}~{high}"
        title = rep.title.strip()
        suffix = Path(self.representative.record.path).suffix
        return f"{base}{rep.title_separator}{title}{suffix}" if title else f"{base}{suffix}"


def find_branch_merge_groups(
    records: list[FileRecord], evidence_config: EvidenceNumberConfig
) -> list[BranchGroup]:
    """結合候補となる枝番グループを検出する。

    対象は、(a) PDF形式、(b) ファイル名が証拠番号パターンに合致し単一の枝番
    （branch_fromのみ。範囲表記branch_toを含む名称は既に結合済みとみなし対象外）
    を持つファイルのうち、(c) 同一の証拠番号（符号＋添字＋番号）を2件以上共有
    するもの。1件しかない枝番は結合候補にならない（結合の意味がないため）。

    同一の枝番を複数ファイルが持つグループ（例：甲001-1が2ファイル）は結合
    候補から除外する。どちらが正かをツールは判断できず、そのまま結合すると
    「甲001-1~1」のような不自然な名前で重複内容が統合されてしまうため
    （重複自体は号証の連番・欠番チェック〔4.7〕が別途警告する）。
    """
    candidates: dict[str, list[BranchMember]] = {}
    order: list[str] = []
    for record in records:
        if record.detected_format != "PDF":
            continue
        if record.pdf_finding is not None and record.pdf_finding.has_password:
            # パスワード付きPDFは復号キーがないと内容を読めず、結合処理
            # （ページの読み取り・スタンプ）自体が実行できない。
            continue
        match = match_evidence_number(record.path.name, evidence_config)
        if match is None or not match.branch_from or match.branch_to:
            continue
        key = f"{match.symbol}{match.subletter}{match.number}"
        if key not in candidates:
            candidates[key] = []
            order.append(key)
        candidates[key].append(BranchMember(record=record, match=match))

    groups = []
    for key in order:
        members = candidates[key]
        if len(members) < 2:
            continue

        def _normalized_branch(value: str | None) -> int | str:
            try:
                return int(value)  # 「1」と「01」の表記揺れも同一枝番として扱う
            except (TypeError, ValueError):
                return value or ""

        branch_values = [_normalized_branch(m.match.branch_from) for m in members]
        if len(set(branch_values)) != len(branch_values):
            # 同一枝番の重複があるグループは結合候補にしない（docstring参照）。
            continue

        def _branch_sort_key(m: BranchMember) -> tuple[int, str]:
            try:
                return (0, f"{int(m.match.branch_from):020d}")
            except (TypeError, ValueError):
                return (1, m.match.branch_from or "")

        members = sorted(members, key=_branch_sort_key)
        groups.append(BranchGroup(members=tuple(members)))
    return groups


def merge_branch_group(
    group: BranchGroup,
    output_path: Path,
    stamp: bool,
    stamp_config: EvidenceStampConfig | None,
) -> None:
    """グループ内のPDFを枝番順に連結し、output_pathへ書き出す。

    stamp=Trueの場合、各ソースファイルのページに、そのファイル自身の枝番
    ラベル（証拠番号処理・右上スタンプ）を焼き込んでから結合する
    （準備の手引29頁「各枝番を区別できるよう...記載する」に対応）。
    """
    writer = PdfWriter()
    for member in group.members:
        start_index = len(writer.pages)
        writer.append(str(member.record.path))
        if stamp:
            assert stamp_config is not None
            for page in writer.pages[start_index:]:
                # 回転属性（/Rotate 90/270等）を持つページは、単独ファイルの
                # スタンプ（evidence_stamp.stamp_pdf）と同様に、回転をページ
                # 内容へ転写（見た目不変のまま/Rotateを0に正規化）してから
                # スタンプする。これを省くと「閲覧時の右上」ではない隅に
                # 90度回転したスタンプが載る（スキャナ・複合機のPDFで頻発）。
                if (page.rotation or 0) % 360 != 0:
                    page.transfer_rotation_to_content()
                stamp_pdf_page(page, member.match.evidence_number_label, stamp_config)
    with open(output_path, "wb") as f:
        writer.write(f)
