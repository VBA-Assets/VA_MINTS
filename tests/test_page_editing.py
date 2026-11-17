"""ページの編集（削除・並べ替え・回転・元に戻す。Ver300。要件定義書 4.0・4.10）。"""

from pathlib import Path

import pdfplumber
import pytest
from pypdf import PdfReader

from fixtures.generate_fixtures import make_multipage_pdf
from mints_prep.processors.pages import PageRef, build_pdf
from mints_prep.processors.updater import BACKUP_DIRNAME, Manifest, restore_all
from mints_prep.scanner.walker import scan_folder
from mints_prep.ui.workspace import apply_all_updates, Workspace, describe_update_plan


def _ws(root: Path, config) -> Workspace:
    return Workspace(scan_folder(root, config), config, Manifest.load(root))


def _apply(root: Path, ws: Workspace, config) -> None:
    """画面の［ファイルを更新］と同じ手順で、計画のすべてを行う。"""
    apply_all_updates(root, ws.plan_update(), config)


def _texts(path: Path) -> list[str]:
    with pdfplumber.open(path) as pdf:
        # 回したページは、語ごとに改行して取り出されるので、空白をそろえる。
        return [" ".join((page.extract_text() or "").split()) for page in pdf.pages]


def _rotations(path: Path) -> list[int]:
    return [(p.rotation or 0) % 360 for p in PdfReader(str(path)).pages]


def _no_stamp(ws: Workspace) -> None:
    for record in ws.eligible:
        sel = ws.pane_view(record).selection
        if "stamp" in sel.choices:
            sel.choices["stamp"] = False


def test_build_pdf_copies_pages_without_rasterizing(tmp_path: Path):
    src = make_multipage_pdf(tmp_path / "a.pdf", page_count=3)
    out = tmp_path / "out.pdf"

    build_pdf([PageRef(src, 2), PageRef(src, 0, 90)], out)

    assert [t.strip() for t in _texts(out)] == ["page 3 of 3", "page 1 of 3"]  # 字が残る
    assert _rotations(out) == [0, 90]
    with pytest.raises(ValueError):
        build_pdf([], out)


def test_delete_move_rotate_and_undo_redo(tmp_path: Path, config):
    make_multipage_pdf(tmp_path / "準備書面.pdf", page_count=4)
    ws = _ws(tmp_path, config)
    record = ws.eligible[0]
    assert ws.can_edit_pages(record) and ws.page_count(record) == 4 and not ws.is_edited(record)

    ws.delete_pages(record, [1])  # 2ページ目を消す → 1,3,4
    assert [r.index for r in ws.pages_of(record)] == [0, 2, 3]
    moved = ws.move_pages(record, [2], 0)  # 4ページ目を先頭へ → 4,1,3
    assert moved == [0] and [r.index for r in ws.pages_of(record)] == [3, 0, 2]
    ws.rotate_pages(record, [1], 180)
    assert [r.rotate for r in ws.pages_of(record)] == [0, 180, 0]
    assert ws.is_edited(record)

    with pytest.raises(ValueError):
        ws.delete_pages(record, [0, 1, 2])  # すべては消せない

    assert ws.undo() == record.path and [r.rotate for r in ws.pages_of(record)] == [0, 0, 0]
    ws.undo()
    ws.undo()
    assert not ws.is_edited(record) and not ws.can_undo
    ws.redo()
    assert [r.index for r in ws.pages_of(record)] == [0, 2, 3]
    ws.move_pages(record, [0], 3)  # 新しい編集をすると、やり直しは消える
    assert not ws.can_redo


def test_update_writes_edited_pages_and_restore_brings_back_original(tmp_path: Path, config):
    src = make_multipage_pdf(tmp_path / "準備書面.pdf", page_count=4)
    original = src.read_bytes()
    ws = _ws(tmp_path, config)
    record = ws.eligible[0]
    ws.delete_pages(record, [1])
    ws.move_pages(record, [2], 0)
    ws.rotate_pages(record, [0], 90)

    plan = ws.plan_update()
    assert plan.counts["pages"] == 1
    assert "ページの編集（削除・挿入・並べ替え・回転）：1 件" in describe_update_plan(
        plan, config.upload.max_total_mb
    )
    _apply(tmp_path, ws, config)

    assert [t.strip() for t in _texts(src)] == ["page 4 of 4", "page 1 of 4", "page 3 of 4"]
    assert _rotations(src) == [90, 0, 0]
    assert (tmp_path / BACKUP_DIRNAME / "準備書面.pdf").read_bytes() == original
    entry = Manifest.load(tmp_path).get("準備書面.pdf")
    assert entry.pages == [["準備書面.pdf", 3, 90], ["準備書面.pdf", 0, 0], ["準備書面.pdf", 2, 0]]

    # 読み込み直すと、編集していない状態（更新したファイルがそのまま）になる。
    again = _ws(tmp_path, config)
    assert not again.is_edited(again.eligible[0]) and not again.plan_update().has_work

    assert restore_all(tmp_path).restored == 1
    assert src.read_bytes() == original


def test_editing_an_updated_file_rebuilds_from_the_original(tmp_path: Path, config):
    """更新したファイルのページを編集し直すと、元のファイルから作り直す
    （2回目の表は、元のファイルのページを指す）。"""
    src = make_multipage_pdf(tmp_path / "準備書面.pdf", page_count=4)
    original = src.read_bytes()
    ws = _ws(tmp_path, config)
    ws.delete_pages(ws.eligible[0], [0])  # 2,3,4
    _apply(tmp_path, ws, config)

    ws = _ws(tmp_path, config)
    ws.move_pages(ws.eligible[0], [2], 0)  # 4,2,3
    _apply(tmp_path, ws, config)

    assert [t.strip() for t in _texts(src)] == ["page 4 of 4", "page 2 of 4", "page 3 of 4"]
    entry = Manifest.load(tmp_path).get("準備書面.pdf")
    assert [row[1] for row in entry.pages] == [3, 1, 2]
    restore_all(tmp_path)
    assert src.read_bytes() == original


def test_stamp_goes_to_the_new_first_page_and_is_not_doubled(tmp_path: Path, config):
    """スタンプ済みのファイルのページを並べ替えると、元のファイルから作り直し、新しい
    1ページ目にだけ押す（もとの1ページ目のスタンプは残らない）。"""
    src = make_multipage_pdf(tmp_path / "甲001 契約書.pdf", page_count=3)
    _apply(tmp_path, _ws(tmp_path, config), config)  # スタンプを押す
    assert ["甲001" in t for t in _texts(src)] == [True, False, False]

    ws = _ws(tmp_path, config)
    record = ws.eligible[0]
    assert ws.pane_view(record).selection.stamped
    ws.move_pages(record, [2], 0)  # 3ページ目を先頭へ
    plan = ws.plan_update()
    assert plan.counts["unstamp"] == 0 and plan.counts["stamp"] == 1
    _apply(tmp_path, ws, config)

    texts = _texts(src)
    assert ["甲001" in t for t in texts] == [True, False, False]
    assert "page 3 of 3" in texts[0] and "page 1 of 3" in texts[1]

    # 編集したうえでスタンプのチェックを外すと、スタンプなしで作り直す。
    ws = _ws(tmp_path, config)
    record = ws.eligible[0]
    ws.rotate_pages(record, [1], 180)
    ws.pane_view(record).selection.choices["stamp"] = False
    _apply(tmp_path, ws, config)
    assert not any("甲001" in t for t in _texts(src))
    assert _rotations(src)[1] == 180


def test_preview_page_follows_the_draft(tmp_path: Path, config):
    src = make_multipage_pdf(tmp_path / "甲001 契約書.pdf", page_count=3)
    ws = _ws(tmp_path, config)
    record = ws.eligible[0]
    ws.move_pages(record, [2], 0)
    ws.rotate_pages(record, [0], 270)

    assert ws.preview_page(record, 0) == (src, 2, 270, "甲001")  # 新しい1ページ目にスタンプ
    assert ws.preview_page(record, 1) == (src, 0, 0, None)
    assert ws.preview_page(record, 5) is None


def test_edited_member_of_branch_merge_keeps_each_branch_stamp(tmp_path: Path, config):
    make_multipage_pdf(tmp_path / "甲007-1 領収書.pdf", page_count=3)
    make_multipage_pdf(tmp_path / "甲007-2 領収書.pdf", page_count=2)
    ws = _ws(tmp_path, config)
    first = next(r for r in ws.eligible if "-1" in r.path.name)
    ws.delete_pages(first, [0])  # 1つ目の材料の1ページ目を消す

    _apply(tmp_path, ws, config)

    merged = tmp_path / "甲007-1~2 領収書.pdf"
    texts = _texts(merged)
    assert len(texts) == 4
    assert "甲007-1" in texts[0] and "page 2 of 3" in texts[0]
    assert "甲007-2" in texts[2] and "page 1 of 2" in texts[2]
    assert not any("甲007" in texts[i] for i in (1, 3))
    assert restore_all(tmp_path).restored == 2
