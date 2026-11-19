"""1つのファイルの中の枝番の区切り（Ver300。要件定義書 4.3・4.11）。
証拠番号は、ファイル名の変更で入れる（2026-11-16 班長指示で、プレビューの上の証拠番号の欄は外した）。"""

from pathlib import Path

import pdfplumber
import pytest

from fixtures.generate_fixtures import make_multipage_pdf
from va_mints.naming.evidence_number import name_with_evidence
from va_mints.processors.updater import Manifest, restore_all
from va_mints.scanner.walker import scan_folder
from va_mints.ui.workspace import apply_all_updates, Workspace


def _ws(root: Path, config) -> Workspace:
    return Workspace(scan_folder(root, config), config, Manifest.load(root))


def _apply(root: Path, ws: Workspace, config) -> None:
    """画面の［ファイルを更新］と同じ手順で、計画のすべてを行う。"""
    apply_all_updates(root, ws.plan_update(), config)


def _texts(path: Path) -> list[str]:
    with pdfplumber.open(path) as pdf:
        return [" ".join((p.extract_text() or "").split()) for p in pdf.pages]


def test_name_with_evidence_keeps_separator_and_title(config):
    c = config.evidence_number
    assert name_with_evidence("契約書.pdf", "甲004", c) == "甲004 契約書.pdf"
    assert name_with_evidence("甲001_売買契約書.pdf", "甲002", c) == "甲002_売買契約書.pdf"
    assert name_with_evidence("甲001 売買.pdf", "", c) == "売買.pdf"
    assert name_with_evidence("甲007-1~3 領収書.pdf", "甲007-1~4", c) == "甲007-1~4 領収書.pdf"


def test_rename_keeps_the_branch_range_of_divisions(tmp_path: Path, config):
    """区切りのあるファイルの名前を変えると、枝番の範囲は区切りで決まるものに付け直す。"""
    make_multipage_pdf(tmp_path / "甲005-1~3 領収書.pdf", page_count=4)
    ws = _ws(tmp_path, config)
    record = ws.eligible[0]
    ws.toggle_division(record, 2)
    assert ws.name_of(record) == "甲005-1~2 領収書.pdf"

    ws.rename(record, "甲006 領収書（写し）.pdf")  # 範囲を書かなくても
    assert ws.name_of(record) == "甲006-1~2 領収書（写し）.pdf"
    ws.rename(record, "甲007-1~9 領収書.pdf")  # 違う範囲を書いても
    assert ws.name_of(record) == "甲007-1~2 領収書.pdf"
    assert ws.branch_labels(record) == {0: "甲007-1", 2: "甲007-2"}

    assert "証拠番号" in ws.rename_error(record, "領収書.pdf")
    with pytest.raises(ValueError):
        ws.rename(record, "領収書.pdf")


def test_divisions_split_one_file_into_branches(tmp_path: Path, config):
    """最初から1つにまとまったファイルに、枝番を付ける（4.11）。"""
    src = make_multipage_pdf(tmp_path / "甲005 領収書.pdf", page_count=5)
    ws = _ws(tmp_path, config)
    record = ws.eligible[0]

    assert "証拠番号" not in (ws.division_error(record, 2) or "")
    ws.toggle_division(record, 2)  # 3ページ目から枝番2
    ws.toggle_division(record, 4)  # 5ページ目から枝番3
    assert ws.branch_starts(record) == [0, 2, 4]
    assert ws.name_of(record) == "甲005-1~3 領収書.pdf"
    assert ws.branch_labels(record) == {0: "甲005-1", 2: "甲005-2", 4: "甲005-3"}
    assert ws.preview_page(record, 2)[3] == "甲005-2"
    assert ws.preview_page(record, 1)[3] is None
    option = next(o for o in ws.pane_view(record).selection.options if o.key == "stamp")
    assert "各枝番の最初のページ" in option.label
    counts = ws.plan_update().counts
    assert counts["divisions"] == 1 and counts["pages"] == 0

    _apply(tmp_path, ws, config)
    result = tmp_path / "甲005-1~3 領収書.pdf"
    assert result.exists() and not src.exists()
    texts = _texts(result)
    assert ["甲005-1" in texts[0], "甲005" in texts[1], "甲005-2" in texts[2], "甲005" in texts[3], "甲005-3" in texts[4]] == [True, False, True, False, True]
    restore_all(tmp_path)
    assert src.exists() and not result.exists()


def test_divisions_follow_pages_and_undo_as_one_step(tmp_path: Path, config):
    make_multipage_pdf(tmp_path / "甲005 領収書.pdf", page_count=4)
    ws = _ws(tmp_path, config)
    record = ws.eligible[0]
    ws.toggle_division(record, 2)
    assert ws.name_of(record) == "甲005-1~2 領収書.pdf"

    ws.move_pages(record, [2], 1)  # 区切りのページを前へ → 区切りもついて動く
    assert ws.branch_starts(record) == [0, 1]

    ws.delete_pages(record, [1])  # 区切りのページを消す → 枝番がなくなり、名前も戻る
    assert ws.branch_starts(record) == [0] and ws.name_of(record) == "甲005 領収書.pdf"
    ws.undo()
    assert ws.branch_starts(record) == [0, 1] and ws.name_of(record) == "甲005-1~2 領収書.pdf"

    ws.undo()  # 並べ替えを戻す
    ws.undo()  # 区切りと名前の変更が、1回で戻る
    assert ws.branch_starts(record) == [0] and ws.name_of(record) == "甲005 領収書.pdf"
    assert not ws.has_edits


def test_division_needs_an_evidence_number(tmp_path: Path, config):
    make_multipage_pdf(tmp_path / "資料.pdf", page_count=3)
    ws = _ws(tmp_path, config)
    record = ws.eligible[0]
    assert "ファイル名の先頭に証拠番号" in ws.division_error(record, 1)
    ws.rename(record, "乙003 資料.pdf")
    assert ws.division_error(record, 1) is None
    with pytest.raises(ValueError):
        ws.toggle_division(record, 0)  # 1ページ目は、いつも最初の枝番の始まり
    ws.toggle_division(record, 1)
    assert ws.name_of(record) == "乙003-1~2 資料.pdf"
    ws.undo()  # 区切りと名前が1回で戻る
    assert ws.name_of(record) == "乙003 資料.pdf" and not ws.has_divisions(record)
