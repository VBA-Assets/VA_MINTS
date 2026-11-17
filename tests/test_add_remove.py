"""左の一覧のファイルの削除と追加（Ver300。2026-11-17 班長指示）。"""

from pathlib import Path

import pytest
from pypdf import PdfReader

from fixtures.generate_fixtures import make_a4_pdf, make_multipage_pdf
from mints_prep.processors.updater import (
    APPLIED_REMOVE,
    APPLIED_STAMP,
    BACKUP_DIRNAME,
    Manifest,
    restore_all,
)
from mints_prep.scanner.walker import scan_folder
from mints_prep.ui.workspace import (
    COUNT_ADD,
    COUNT_REMOVE,
    Workspace,
    apply_all_updates,
    describe_update_plan,
)


def _ws(root: Path, config) -> Workspace:
    return Workspace(scan_folder(root, config), config, Manifest.load(root))


def _apply(root: Path, ws: Workspace, config) -> None:
    apply_all_updates(root, ws.plan_update(), config)


def _rec(ws: Workspace, name: str):
    return next(r for r in ws.records if ws.name_of(r) == name)


def _names(root: Path) -> list[str]:
    return sorted(p.name for p in root.iterdir() if p.is_file())


def _folders(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "証拠"
    outside = tmp_path / "ほかの場所"
    root.mkdir()
    outside.mkdir()
    return root, outside


def test_remove_moves_the_file_to_backup_and_restore_brings_it_back(tmp_path: Path, config):
    root, _ = _folders(tmp_path)
    make_a4_pdf(root / "甲001 契約書.pdf")
    make_a4_pdf(root / "不要.pdf")
    original = (root / "不要.pdf").read_bytes()
    ws = _ws(root, config)
    ws.remove([_rec(ws, "不要.pdf")])
    assert [ws.name_of(r) for r in ws.records] == ["甲001 契約書.pdf"]
    assert ws.has_edits

    # 更新の前なら、元に戻せる。
    ws.undo()
    assert sorted(ws.name_of(r) for r in ws.records) == ["不要.pdf", "甲001 契約書.pdf"]
    ws.redo()

    plan = ws.plan_update()
    assert plan.counts[COUNT_REMOVE] == 1 and plan.removals == [root / "不要.pdf"]
    assert "一覧から削除するファイル（「元のファイル」フォルダへ移します）：1 件" in describe_update_plan(plan, 200)

    _apply(root, ws, config)
    assert _names(root) == ["甲001 契約書.pdf"]
    assert (root / BACKUP_DIRNAME / "不要.pdf").read_bytes() == original
    entries = [e for e in Manifest.load(root).entries if e.removed]
    assert len(entries) == 1 and entries[0].applied == [APPLIED_REMOVE]
    # 読み込み直しても、削除したファイルは出ない（「元のファイル」の中は読まない）。
    assert [r.path.name for r in _ws(root, config).records] == ["甲001 契約書.pdf"]

    assert restore_all(root).failures == []
    assert _names(root) == ["不要.pdf", "甲001 契約書.pdf"]
    assert (root / "不要.pdf").read_bytes() == original
    assert not (root / BACKUP_DIRNAME).exists()


def test_removing_an_updated_file_restores_the_original(tmp_path: Path, config):
    """以前に更新した（スタンプを付けた）ファイルを削除しても、戻すと更新する前の姿に戻る。"""
    root, _ = _folders(tmp_path)
    make_a4_pdf(root / "甲001 契約書.pdf")
    original = (root / "甲001 契約書.pdf").read_bytes()
    ws = _ws(root, config)
    ws.pane_view(ws.eligible[0]).selection.choices["stamp"] = True
    _apply(root, ws, config)
    assert APPLIED_STAMP in Manifest.load(root).get("甲001 契約書.pdf").applied

    again = _ws(root, config)
    again.remove([again.records[0]])
    _apply(root, again, config)
    assert _names(root) == []

    assert restore_all(root).failures == []
    assert (root / "甲001 契約書.pdf").read_bytes() == original


def test_add_copies_the_file_and_leaves_the_source(tmp_path: Path, config):
    root, outside = _folders(tmp_path)
    make_a4_pdf(root / "甲001 契約書.pdf")
    make_a4_pdf(outside / "甲002 領収書.pdf")
    source_bytes = (outside / "甲002 領収書.pdf").read_bytes()
    ws = _ws(root, config)
    added, errors = ws.add_files([outside / "甲002 領収書.pdf"])
    assert errors == [] and len(added) == 1 and ws.is_added(added[0])
    assert [ws.name_of(r) for r in ws.records] == ["甲001 契約書.pdf", "甲002 領収書.pdf"]
    assert ws.is_eligible(added[0]) and ws.pane_view(added[0]).selection is not None

    # 同じものは二度追加しない。一覧のファイルも追加しない。
    _, errors = ws.add_files([outside / "甲002 領収書.pdf", root / "甲001 契約書.pdf"])
    assert len(errors) == 2

    plan = ws.plan_update()
    assert plan.counts[COUNT_ADD] == 1
    assert "追加するファイル（対象フォルダに写します）：1 件" in describe_update_plan(plan, 200)
    _apply(root, ws, config)
    assert _names(root) == ["甲001 契約書.pdf", "甲002 領収書.pdf"]
    assert (outside / "甲002 領収書.pdf").read_bytes() == source_bytes  # 元の場所はそのまま
    entry = Manifest.load(root).get("甲002 領収書.pdf")
    assert entry.added  # 処理をしても、追加したファイルであることは引き継ぐ

    assert restore_all(root).failures == []
    assert _names(root) == ["甲001 契約書.pdf"]
    assert (outside / "甲002 領収書.pdf").exists()
    assert not (root / BACKUP_DIRNAME).exists()


def test_added_file_with_processing_and_name_collision(tmp_path: Path, config):
    """追加したファイルにスタンプを付ける。同じ名前のファイルがあれば、名前を変えて写す。
    戻すと、写したファイルと、その控えを消す。"""
    root, outside = _folders(tmp_path)
    make_a4_pdf(root / "甲003 写真.pdf")
    make_multipage_pdf(outside / "甲003 写真.pdf", page_count=2)
    ws = _ws(root, config)
    (record,), _ = ws.add_files([outside / "甲003 写真.pdf"])
    sel = ws.pane_view(record).selection
    sel.choices["stamp"] = True
    _apply(root, ws, config)
    assert _names(root) == ["甲003 写真.pdf", "甲003 写真_2.pdf"]
    added = root / "甲003 写真_2.pdf"
    assert len(PdfReader(str(added)).pages) == 2
    assert "甲003" in PdfReader(str(added)).pages[0].extract_text()
    entry = Manifest.load(root).get("甲003 写真_2.pdf")
    assert entry.added and APPLIED_STAMP in entry.applied

    assert restore_all(root).failures == []
    assert _names(root) == ["甲003 写真.pdf"]
    assert not (root / BACKUP_DIRNAME).exists()


def test_undo_add_and_remove_an_added_file(tmp_path: Path, config):
    root, outside = _folders(tmp_path)
    make_a4_pdf(root / "甲001 契約書.pdf")
    make_a4_pdf(outside / "追加.pdf")
    ws = _ws(root, config)
    (record,), _ = ws.add_files([outside / "追加.pdf"])
    ws.undo()
    assert len(ws.records) == 1 and not ws.has_edits
    ws.redo()
    assert len(ws.records) == 2
    ws.remove([record])  # 追加したファイルを削除する＝追加をやめる
    assert len(ws.records) == 1
    plan = ws.plan_update()
    assert plan.counts[COUNT_ADD] == 0 and not any(u.added for u in plan.updates)


def test_remove_is_refused_for_joined_and_split_files(tmp_path: Path, config):
    root, _ = _folders(tmp_path)
    make_multipage_pdf(root / "a.pdf", page_count=3)
    make_a4_pdf(root / "b.pdf")
    ws = _ws(root, config)
    a, b = _rec(ws, "a.pdf"), _rec(ws, "b.pdf")
    ws.join([a, b])
    with pytest.raises(ValueError, match="結合"):
        ws.remove([a])
    ws.undo()
    new = ws.split(a, 1)
    with pytest.raises(ValueError, match="分"):
        ws.remove([new])
    with pytest.raises(ValueError, match="分割"):
        ws.remove([a])


def test_folder_drop_adds_files_directly_inside(tmp_path: Path, config):
    root, outside = _folders(tmp_path)
    make_a4_pdf(outside / "1.pdf")
    (outside / "中").mkdir()
    make_a4_pdf(outside / "中" / "2.pdf")
    (outside / "Thumbs.db").write_bytes(b"x")
    ws = _ws(root, config)
    assert ws.expand_dropped([outside]) == [outside / "1.pdf"]
