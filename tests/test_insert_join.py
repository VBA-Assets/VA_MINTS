"""ページの挿入とファイルの結合（Ver300。要件定義書 4.10・4.12）。"""

from pathlib import Path

import pytest
from pypdf import PdfReader

from fixtures.generate_fixtures import (
    make_a4_pdf_no_metadata,
    make_image_with_metadata,
    make_image_without_metadata,
    make_multipage_pdf,
)
from mints_prep.processors.updater import BACKUP_DIRNAME, Manifest, restore_all
from mints_prep.scanner.walker import scan_folder
from mints_prep.ui.workspace import apply_all_updates, Workspace


def _ws(root: Path, config) -> Workspace:
    return Workspace(scan_folder(root, config), config, Manifest.load(root))


def _apply(root: Path, ws: Workspace, config) -> None:
    """画面の［ファイルを更新］と同じ手順で、計画のすべてを行う。"""
    apply_all_updates(root, ws.plan_update(), config)


def _rec(ws: Workspace, name: str):
    return next(r for r in ws.records if r.path.name == name)


def _pages(path: Path) -> int:
    return len(PdfReader(str(path)).pages)


def _names(root: Path) -> list[str]:
    return sorted(p.name for p in root.iterdir() if p.is_file())


def test_insert_pages_from_a_file_in_the_folder_keeps_that_file(tmp_path: Path, config):
    make_multipage_pdf(tmp_path / "甲001 契約書.pdf", page_count=3)
    make_multipage_pdf(tmp_path / "甲002 覚書.pdf", page_count=2)
    ws = _ws(tmp_path, config)
    target = _rec(ws, "甲001 契約書.pdf")
    added = ws.insert_pages(target, 1, tmp_path / "甲002 覚書.pdf")
    assert added == [1, 2] and ws.page_count(target) == 5
    assert ws.preview_page(target, 1)[0] == tmp_path / "甲002 覚書.pdf"

    _apply(tmp_path, ws, config)
    assert _pages(tmp_path / "甲001 契約書.pdf") == 5
    assert _pages(tmp_path / "甲002 覚書.pdf") == 2  # 材料のファイルは残る
    entry = Manifest.load(tmp_path).get("甲001 契約書.pdf")
    assert entry.originals == ["甲001 契約書.pdf"]
    assert len(entry.materials) == 1 and (tmp_path / BACKUP_DIRNAME / entry.materials[0]).exists()

    result = restore_all(tmp_path)
    # 戻すのは、甲001 と、メタデータ除去をした甲002。材料の写しとはぶつからない。
    assert result.failures == [] and result.restored == 2
    assert _pages(tmp_path / "甲001 契約書.pdf") == 3
    assert _pages(tmp_path / "甲002 覚書.pdf") == 2
    assert not (tmp_path / BACKUP_DIRNAME).exists()


def test_insert_an_image_from_outside_the_folder(tmp_path: Path, config):
    root = tmp_path / "証拠"
    root.mkdir()
    make_a4_pdf_no_metadata(root / "甲001 写真の説明.pdf")
    outside = make_image_with_metadata(tmp_path / "写真.jpg")
    ws = _ws(root, config)
    target = ws.eligible[0]
    ws.insert_pages(target, 1, outside)
    assert ws.page_count(target) == 2

    _apply(root, ws, config)
    reader = PdfReader(str(root / "甲001 写真の説明.pdf"))
    assert len(reader.pages) == 2
    assert b"Exif" not in (root / "甲001 写真の説明.pdf").read_bytes()  # EXIF は持ち込まない
    assert outside.exists()
    assert restore_all(root).failures == []
    assert _pages(root / "甲001 写真の説明.pdf") == 1


def test_insert_rejects_other_formats(tmp_path: Path, config):
    make_a4_pdf_no_metadata(tmp_path / "甲001.pdf")
    other = tmp_path.parent / f"{tmp_path.name}_memo.txt"
    other.write_text("x", encoding="utf-8")
    ws = _ws(tmp_path, config)
    with pytest.raises(ValueError, match="PDF と画像"):
        ws.insert_pages(ws.eligible[0], 0, other)


def test_join_files_in_list_order_and_restore(tmp_path: Path, config):
    make_multipage_pdf(tmp_path / "甲003-a 領収書.pdf", page_count=2)
    make_image_without_metadata(tmp_path / "甲003-b 領収書.png")
    make_a4_pdf_no_metadata(tmp_path / "甲003-c 領収書.pdf")
    ws = _ws(tmp_path, config)
    a, b, c = (_rec(ws, n) for n in ("甲003-a 領収書.pdf", "甲003-b 領収書.png", "甲003-c 領収書.pdf"))

    target = ws.join([c, b, a])  # 選んだ順ではなく、一覧の並び順
    assert target is a and ws.page_count(a) == 4
    assert ws.is_absorbed(b) and ws.is_absorbed(c) and ws.absorbed_into(a) == [b, c]
    assert ws.join_warning(a) is None  # 同じ証拠番号の材料
    plan = ws.plan_update()
    assert plan.counts["join"] == 1 and plan.counts["pages"] == 0
    assert len(plan.updates) == 1

    _apply(tmp_path, ws, config)
    assert _names(tmp_path) == ["甲003-a 領収書.pdf"]
    assert _pages(tmp_path / "甲003-a 領収書.pdf") == 4

    result = restore_all(tmp_path)
    assert result.failures == [] and result.restored == 3
    assert _names(tmp_path) == ["甲003-a 領収書.pdf", "甲003-b 領収書.png", "甲003-c 領収書.pdf"]
    assert _pages(tmp_path / "甲003-a 領収書.pdf") == 2


def test_join_can_be_undone_in_one_step(tmp_path: Path, config):
    make_a4_pdf_no_metadata(tmp_path / "甲001.pdf")
    make_a4_pdf_no_metadata(tmp_path / "甲002.pdf")
    ws = _ws(tmp_path, config)
    first, second = ws.eligible
    ws.join([first, second])
    assert ws.has_edits
    ws.undo()
    assert not ws.has_edits and not ws.is_absorbed(second) and ws.page_count(first) == 1
    ws.redo()
    assert ws.is_absorbed(second) and ws.page_count(first) == 2


def test_join_errors_and_warning(tmp_path: Path, config):
    make_a4_pdf_no_metadata(tmp_path / "甲005 契約書.pdf")
    make_a4_pdf_no_metadata(tmp_path / "甲006 覚書.pdf")
    make_image_without_metadata(tmp_path / "写真.png")
    make_a4_pdf_no_metadata(tmp_path / "甲007-1 領収書.pdf")
    make_a4_pdf_no_metadata(tmp_path / "甲007-2 領収書.pdf")
    ws = _ws(tmp_path, config)
    k5, k6, photo = _rec(ws, "甲005 契約書.pdf"), _rec(ws, "甲006 覚書.pdf"), _rec(ws, "写真.png")
    branch = _rec(ws, "甲007-1 領収書.pdf")

    assert "2つ以上" in ws.join_error([k5])
    assert "PDF を1つ以上" in ws.join_error([photo, photo])
    assert "枝番ファイル結合" in ws.join_error([k5, branch])  # 自動結合のチェックが入っている
    ws.set_merge(branch, False)
    assert ws.join_error([k5, branch]) is None

    ws.join([k5, k6])
    assert "甲005・甲006" in ws.join_warning(k5)
    with pytest.raises(ValueError):
        ws.join([k5, k6])  # 甲006 は、もう結合してある

    ws.join([k5, branch])
    with pytest.raises(ValueError, match="枝番ファイル結合はできません"):
        ws.set_merge(_rec(ws, "甲007-2 領収書.pdf"), True)


def test_edit_a_joined_file_again_rebuilds_from_the_originals(tmp_path: Path, config):
    make_multipage_pdf(tmp_path / "甲001 資料.pdf", page_count=2)
    make_multipage_pdf(tmp_path / "甲001 資料（続き）.pdf", page_count=2)
    ws = _ws(tmp_path, config)
    ws.join(list(ws.eligible))
    _apply(tmp_path, ws, config)

    ws = _ws(tmp_path, config)
    joined = ws.eligible[0]
    assert ws.page_count(joined) == 4
    ws.delete_pages(joined, [3])
    _apply(tmp_path, ws, config)
    assert _pages(tmp_path / "甲001 資料.pdf") == 3

    result = restore_all(tmp_path)
    assert result.failures == [] and result.restored == 2
    assert _names(tmp_path) == ["甲001 資料.pdf", "甲001 資料（続き）.pdf"]
