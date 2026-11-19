"""ファイルの分割（Ver300。要件定義書 4.10）。"""

from pathlib import Path

import pytest
from pypdf import PdfReader

from fixtures.generate_fixtures import make_multipage_pdf
from va_mints.processors.updater import APPLIED_SPLIT, BACKUP_DIRNAME, Manifest, restore_all
from va_mints.scanner.walker import scan_folder
from va_mints.ui.workspace import apply_all_updates, COUNT_SPLIT_NEW, Workspace, describe_update_plan


def _ws(root: Path, config) -> Workspace:
    return Workspace(scan_folder(root, config), config, Manifest.load(root))


def _apply(root: Path, ws: Workspace, config) -> None:
    """画面の［ファイルを更新］と同じ手順で、計画のすべてを行う。"""
    apply_all_updates(root, ws.plan_update(), config)


def _rec(ws: Workspace, name: str):
    return next(r for r in ws.records if ws.name_of(r) == name)


def _texts(path: Path) -> list[str]:
    return [p.extract_text().strip() for p in PdfReader(str(path)).pages]


def _names(root: Path) -> list[str]:
    return sorted(p.name for p in root.iterdir() if p.is_file())


def test_split_makes_a_new_file_after_the_source(tmp_path: Path, config):
    make_multipage_pdf(tmp_path / "スキャン.pdf", page_count=5)
    make_multipage_pdf(tmp_path / "別.pdf", page_count=1)
    ws = _ws(tmp_path, config)
    source = _rec(ws, "スキャン.pdf")
    new = ws.split(source, 3)
    assert new.path.name == "スキャン_2.pdf" and ws.is_created(new)
    assert [ws.name_of(r) for r in ws.records] == ["スキャン.pdf", "スキャン_2.pdf", "別.pdf"]
    assert ws.page_count(source) == 3 and ws.page_count(new) == 2
    assert ws.split_children(source) == [new] and ws.split_source(new) is source

    plan = ws.plan_update()
    assert plan.counts[APPLIED_SPLIT] == 1 and plan.counts[COUNT_SPLIT_NEW] == 1
    text = describe_update_plan(plan, 200)
    assert "ファイルの分割：1 件" in text and "分割で新しく作るファイル：1 件" in text
    assert "ページの編集" not in text

    _apply(tmp_path, ws, config)
    assert _texts(tmp_path / "スキャン.pdf") == [f"page {i} of 5" for i in (1, 2, 3)]
    assert _texts(tmp_path / "スキャン_2.pdf") == [f"page {i} of 5" for i in (4, 5)]
    manifest = Manifest.load(tmp_path)
    created = manifest.get("スキャン_2.pdf")
    assert created.created and created.originals == [] and len(created.materials) == 1
    assert manifest.get("スキャン.pdf").originals == ["スキャン.pdf"]

    result = restore_all(tmp_path)
    # 戻すのは、スキャン.pdf と、メタデータ除去をした別.pdf。分割で作ったファイルは消す。
    assert result.failures == [] and result.restored == 2
    assert _names(tmp_path) == ["スキャン.pdf", "別.pdf"]
    assert len(_texts(tmp_path / "スキャン.pdf")) == 5
    assert not (tmp_path / BACKUP_DIRNAME).exists()


def test_split_several_times_keeps_page_order_and_names(tmp_path: Path, config):
    make_multipage_pdf(tmp_path / "束.pdf", page_count=6)
    ws = _ws(tmp_path, config)
    source = _rec(ws, "束.pdf")
    second = ws.split(source, 4)  # 5〜6
    third = ws.split(source, 2)  # 3〜4（分ける前のファイルのすぐ後ろに出る）
    fourth = ws.split(second, 1)  # 6
    assert [ws.name_of(r) for r in ws.records] == ["束.pdf", "束_3.pdf", "束_2.pdf", "束_4.pdf"]
    assert [ws.page_count(r) for r in (source, third, second, fourth)] == [2, 2, 1, 1]

    ws.rename(third, "甲002 領収書.pdf")
    ws.rename(second, "甲003 請求書.pdf")
    ws.rename(fourth, "甲004 写真.pdf")
    _apply(tmp_path, ws, config)
    assert _names(tmp_path) == ["束.pdf", "甲002 領収書.pdf", "甲003 請求書.pdf", "甲004 写真.pdf"]
    assert _texts(tmp_path / "甲002 領収書.pdf")[0].startswith("page 3 of 6")
    assert _texts(tmp_path / "甲004 写真.pdf")[0].startswith("page 6 of 6")
    assert restore_all(tmp_path).failures == []
    assert _names(tmp_path) == ["束.pdf"]


def test_split_undo_and_redo(tmp_path: Path, config):
    make_multipage_pdf(tmp_path / "束.pdf", page_count=3)
    ws = _ws(tmp_path, config)
    source = _rec(ws, "束.pdf")
    new = ws.split(source, 1)
    ws.rename(new, "甲002 次.pdf")
    ws.undo()  # 名前
    ws.undo()  # 分割
    assert [r.path.name for r in ws.records] == ["束.pdf"]
    assert ws.page_count(source) == 3 and not ws.has_edits
    ws.redo()
    assert [ws.name_of(r) for r in ws.records] == ["束.pdf", "束_2.pdf"]
    assert ws.page_count(source) == 1 and ws.page_count(new) == 2
    ws.redo()
    assert ws.name_of(new) == "甲002 次.pdf"


def test_split_errors(tmp_path: Path, config):
    make_multipage_pdf(tmp_path / "甲001 束.pdf", page_count=3)
    make_multipage_pdf(tmp_path / "甲002 他.pdf", page_count=1)
    ws = _ws(tmp_path, config)
    source = _rec(ws, "甲001 束.pdf")
    with pytest.raises(ValueError, match="1ページ目の前"):
        ws.split(source, 0)
    ws.toggle_division(source, 1)
    with pytest.raises(ValueError, match="枝番の区切り"):
        ws.split(source, 2)
    ws.undo()
    new = ws.split(source, 2)
    assert "分けて作るファイル" in ws.join_error([new, _rec(ws, "甲002 他.pdf")])


def test_pages_inserted_from_a_file_updated_earlier_use_its_old_content(tmp_path: Path, config):
    """先に更新するファイル（a）のページを、後で更新するファイル（b）に入れても、b には
    a の更新前のページが入る（isolate_shared_pages）。"""
    make_multipage_pdf(tmp_path / "a.pdf", page_count=3)
    make_multipage_pdf(tmp_path / "b.pdf", page_count=1)
    ws = _ws(tmp_path, config)
    a, b = _rec(ws, "a.pdf"), _rec(ws, "b.pdf")
    ws.insert_pages(b, 1, tmp_path / "a.pdf")
    ws.delete_pages(a, [0])
    _apply(tmp_path, ws, config)
    assert _texts(tmp_path / "a.pdf") == ["page 2 of 3", "page 3 of 3"]
    assert _texts(tmp_path / "b.pdf")[1:] == [f"page {i} of 3" for i in (1, 2, 3)]
    assert restore_all(tmp_path).failures == []
    assert not (tmp_path / BACKUP_DIRNAME).exists()


def test_split_file_can_be_edited_again_after_reload(tmp_path: Path, config):
    """更新した後に読み込み直して、分割で作ったファイルを編集し直しても、元に戻せる。"""
    make_multipage_pdf(tmp_path / "甲001 束.pdf", page_count=4)
    ws = _ws(tmp_path, config)
    new = ws.split(_rec(ws, "甲001 束.pdf"), 2)
    ws.rename(new, "甲002 後半.pdf")
    _apply(tmp_path, ws, config)

    ws = _ws(tmp_path, config)
    again = _rec(ws, "甲002 後半.pdf")
    ws.delete_pages(again, [0])
    _apply(tmp_path, ws, config)
    texts = _texts(tmp_path / "甲002 後半.pdf")
    # 4ページ目だけが残り、スタンプは1つだけ（二重にならない）。
    assert len(texts) == 1 and "page 4 of 4" in texts[0] and texts[0].count("甲002") == 1
    entry = Manifest.load(tmp_path).get("甲002 後半.pdf")
    assert entry.created and entry.originals == []

    assert restore_all(tmp_path).failures == []
    assert _names(tmp_path) == ["甲001 束.pdf"]
    assert len(_texts(tmp_path / "甲001 束.pdf")) == 4
    assert not (tmp_path / BACKUP_DIRNAME).exists()
