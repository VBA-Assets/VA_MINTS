"""ファイル名の変更（Ver300。要件定義書 4.3）。"""

from pathlib import Path

import pytest

from fixtures.generate_fixtures import make_a4_pdf, make_a4_pdf_no_metadata, make_multipage_pdf
from mints_prep.processors.updater import BACKUP_DIRNAME, Manifest, restore_all
from mints_prep.scanner.walker import scan_folder
from mints_prep.ui.workspace import apply_all_updates, Workspace, describe_update_plan


def _ws(root: Path, config) -> Workspace:
    return Workspace(scan_folder(root, config), config, Manifest.load(root))


def _apply(root: Path, ws: Workspace, config) -> None:
    """画面の［ファイルを更新］と同じ手順で、計画のすべてを行う。"""
    apply_all_updates(root, ws.plan_update(), config)


def _record(ws: Workspace, part: str):
    return next(r for r in ws.records if part in r.path.name)


def test_rename_only_moves_the_file_without_a_backup_copy(tmp_path: Path, config):
    src = make_a4_pdf_no_metadata(tmp_path / "準備書面（案）.pdf")
    original = src.read_bytes()
    ws = _ws(tmp_path, config)
    record = ws.eligible[0]

    ws.rename(record, "原告第1準備書面.pdf")
    assert ws.name_of(record) == "原告第1準備書面.pdf" and ws.is_changed(record)
    plan = ws.plan_update()
    assert plan.counts["rename"] == 1
    assert "・ファイル名の変更：1 件" in describe_update_plan(plan, config.upload.max_total_mb)

    _apply(tmp_path, ws, config)

    renamed = tmp_path / "原告第1準備書面.pdf"
    assert renamed.read_bytes() == original and not src.exists()
    assert not list((tmp_path / BACKUP_DIRNAME).glob("*.pdf"))  # 中身は同じなので控えない
    assert restore_all(tmp_path).restored == 1
    assert src.read_bytes() == original and not renamed.exists()


def test_rename_errors(tmp_path: Path, config):
    make_a4_pdf_no_metadata(tmp_path / "a.pdf")
    make_a4_pdf_no_metadata(tmp_path / "b.pdf")
    ws = _ws(tmp_path, config)
    a = _record(ws, "a.pdf")

    assert "拡張子" in ws.rename_error(a, "a.docx")
    assert "拡張子" in ws.rename_error(a, ".pdf")
    assert "使えない文字" in ws.rename_error(a, "a:b.pdf")
    assert "文字以内" in ws.rename_error(a, "あ" * 101 + ".pdf")
    assert "同じ名前" in ws.rename_error(a, "B.pdf")  # 大文字小文字は区別しない
    assert ws.rename_error(a, "c.pdf") is None
    with pytest.raises(ValueError):
        ws.rename(a, "b.pdf")

    # 下書きの名前どうしも重ならないようにする。
    ws.rename(a, "c.pdf")
    b = _record(ws, "b.pdf")
    assert "同じ名前" in ws.rename_error(b, "c.pdf")


def test_rename_reads_the_evidence_number_from_the_new_name(tmp_path: Path, config):
    make_a4_pdf(tmp_path / "契約書.pdf", with_metadata=True)
    ws = _ws(tmp_path, config)
    record = ws.eligible[0]
    assert "stamp" not in ws.pane_view(record).selection.choices

    ws.rename(record, "甲004 契約書.pdf")
    sel = ws.pane_view(record).selection
    assert sel.choices["stamp"] is True  # 新しい名前の先頭の証拠番号でスタンプの項目が出る
    assert sel.item.preliminary.stamp_label == "甲004"

    _apply(tmp_path, ws, config)
    result = tmp_path / "甲004 契約書.pdf"
    assert result.exists() and not (tmp_path / "契約書.pdf").exists()
    assert (tmp_path / BACKUP_DIRNAME / "契約書.pdf").exists()  # 内容も変わるので控える
    restore_all(tmp_path)
    assert (tmp_path / "契約書.pdf").exists() and not result.exists()


def test_renamed_file_hides_the_sanitize_item(tmp_path: Path, config):
    long_name = "甲001 " + "あ" * 110 + ".pdf"
    make_a4_pdf_no_metadata(tmp_path / long_name)
    ws = _ws(tmp_path, config)
    record = ws.eligible[0]
    assert "sanitize" in ws.pane_view(record).selection.choices

    ws.rename(record, "甲001 短い名前.pdf")
    assert "sanitize" not in ws.pane_view(record).selection.choices
    assert ws.output_name(record) == "甲001 短い名前.pdf"


def test_rename_undo_redo_together_with_page_edits(tmp_path: Path, config):
    make_multipage_pdf(tmp_path / "資料.pdf", page_count=3)
    ws = _ws(tmp_path, config)
    record = ws.eligible[0]
    ws.delete_pages(record, [0])
    ws.rename(record, "資料（抜粋）.pdf")

    assert ws.undo() == record.path and ws.name_of(record) == "資料.pdf"
    assert ws.is_edited(record)  # ページの編集は残っている
    ws.undo()
    assert not ws.has_edits
    ws.redo()
    ws.redo()
    assert ws.name_of(record) == "資料（抜粋）.pdf" and ws.page_count(record) == 2

    _apply(tmp_path, ws, config)
    result = tmp_path / "資料（抜粋）.pdf"
    assert result.exists() and not (tmp_path / "資料.pdf").exists()
    restore_all(tmp_path)
    assert (tmp_path / "資料.pdf").exists() and not result.exists()


def test_member_of_a_merge_cannot_be_renamed_while_merging(tmp_path: Path, config):
    make_a4_pdf_no_metadata(tmp_path / "甲007-1 領収書.pdf")
    make_a4_pdf_no_metadata(tmp_path / "甲007-2 領収書.pdf")
    ws = _ws(tmp_path, config)
    record = ws.eligible[0]
    assert "枝番ファイル結合" in ws.rename_error(record, "甲008 領収書.pdf")
    ws.set_merge(record, False)
    assert ws.rename_error(record, "甲008 領収書.pdf") is None
