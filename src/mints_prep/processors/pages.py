"""ページの表から PDF を作る（要件定義書 4.0・4.10。Ver300）。

ページの削除・挿入・並べ替え・回転、ファイルの結合・分割は、いずれも「どのファイルの
何ページ目を、どの順に、何度回して並べるか」の表（ページの表。PageRef の並び）で
表す。この表のとおりに新しい PDF を作るのが build_pdf である。

**ページは画像にしない**（要件定義書 4.0・第8章「原本忠実性」）。元の PDF のページを
そのまま写して並べる（pypdf のページの複写）ので、ページの中身・画質・テキスト
レイヤーは変わらない。回転は PDF の回転の属性（/Rotate）を足すだけである。

この処理はファイルの内容を変えるため、呼び出し側（GUI）で必ず実行前にユーザーの
確認を得ること（CLAUDE.md「絶対に譲れない設計原則」3）。完全ローカル動作（原則1）の
とおり、外部通信はしない。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader, PdfWriter


@dataclass(frozen=True)
class PageRef:
    """ページの表の1行：path の PDF の index ページ目（0始まり）を、rotate 度（時計回り。
    0・90・180・270）足して回したもの。"""

    path: Path
    index: int
    rotate: int = 0

    def rotated(self, degrees: int) -> "PageRef":
        return PageRef(self.path, self.index, (self.rotate + degrees) % 360)


def count_pages(path: Path) -> int:
    return len(PdfReader(str(path)).pages)


def all_pages(path: Path) -> list[PageRef]:
    """path の PDF の全ページを、そのままの順・回転なしで並べた表。"""
    return [PageRef(path, i) for i in range(count_pages(path))]


def build_pdf(pages: list[PageRef] | tuple[PageRef, ...], output_path: Path) -> None:
    """ページの表のとおりに PDF を作り、output_path に書く。

    pages は1行以上であること（ページのない PDF は作らない）。同じページを2度
    並べてもよい（それぞれ写す）。pypdf が書き込む作成ソフト名（Producer）は
    消しておく（読み込み直したときに「メタデータあり」と出ないように。
    evidence_merger.merge_branch_group と同じ）。
    """
    if not pages:
        raise ValueError("ページのない PDF は作れません。")
    readers: dict[Path, PdfReader] = {}
    writer = PdfWriter()
    for ref in pages:
        reader = readers.get(ref.path)
        if reader is None:
            reader = readers[ref.path] = PdfReader(str(ref.path))
        page = writer.add_page(reader.pages[ref.index])
        if ref.rotate % 360:
            page.rotate(ref.rotate % 360)
    writer.metadata = None
    with open(output_path, "wb") as f:
        writer.write(f)
