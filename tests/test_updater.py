"""その場での更新・更新記録・［元のファイルに戻す］（processors/updater.py。Ver200）。"""

import dataclasses
from pathlib import Path

import pdfplumber
import pytest
from pypdf import PdfReader

from fixtures.generate_fixtures import make_a4_pdf, make_multipage_pdf
from mints_prep.processors.updater import (
    BACKUP_DIRNAME,
    MANIFEST_FILENAME,
    Manifest,
    has_restorable_update,
    restore_all,
    update_item,
)
from mints_prep.scanner.walker import scan_folder
from mints_prep.ui.workspace import apply_all_updates, Workspace


def _workspace(root: Path, config) -> Workspace:
    return Workspace(scan_folder(root, config), config, Manifest.load(root))


def _apply(root: Path, ws: Workspace, config) -> None:
    """画面の［ファイルを更新］と同じ手順で、計画のすべてを行う。"""
    apply_all_updates(root, ws.plan_update(), config)


def _text(path: Path) -> str:
    with pdfplumber.open(path) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def _run_update(root: Path, config) -> Workspace:
    ws = _workspace(root, config)
    _apply(root, ws, config)
    return ws


def test_update_keeps_original_in_backup_folder_and_restore_brings_it_back(tmp_path: Path, config):
    src = make_a4_pdf(tmp_path / "甲001 契約書.pdf", with_metadata=True)
    original_bytes = src.read_bytes()

    _run_update(tmp_path, config)

    backup = tmp_path / BACKUP_DIRNAME / "甲001 契約書.pdf"
    assert backup.read_bytes() == original_bytes  # 更新前のファイルが残っている
    assert src.read_bytes() != original_bytes  # その場で更新された
    assert (tmp_path / BACKUP_DIRNAME / MANIFEST_FILENAME).exists()
    assert has_restorable_update(tmp_path)

    result = restore_all(tmp_path)
    assert result.restored == 1 and not result.failures
    assert src.read_bytes() == original_bytes
    assert not (tmp_path / BACKUP_DIRNAME).exists()  # すべて戻すと、フォルダごとなくなる
    assert not has_restorable_update(tmp_path)




def test_stamped_image_is_not_counted_as_unstamped_after_update(tmp_path: Path, config):
    from fixtures.generate_fixtures import make_image_without_metadata

    make_image_without_metadata(tmp_path / "甲001 写真.png")
    ws = _workspace(tmp_path, config)
    assert ws.plan_update().unstamped_image_count == 1
    ws.pane_view(ws.eligible[0]).selection.choices["stamp"] = True
    _apply(tmp_path, ws, config)
    # 更新の後は、スタンプのチェックは外れているが、済んでいるので数えない。
    assert _workspace(tmp_path, config).plan_update().unstamped_image_count == 0




def test_file_added_after_first_update_is_saved_on_second_update(tmp_path: Path, config):
    """判定はファイル単位（2026-11-16 班長決定）。後から足したファイルの元も残る。"""
    make_a4_pdf(tmp_path / "甲001 契約書.pdf", with_metadata=True)
    _run_update(tmp_path, config)
    added = make_a4_pdf(tmp_path / "甲002 領収書.pdf", with_metadata=True)
    added_bytes = added.read_bytes()

    _run_update(tmp_path, config)

    assert (tmp_path / BACKUP_DIRNAME / "甲002 領収書.pdf").read_bytes() == added_bytes
    assert restore_all(tmp_path).restored == 2
    assert added.read_bytes() == added_bytes


def test_sanitized_name_is_restored_to_original_name(tmp_path: Path, config):
    long_name = "甲001 " + "あ" * 110 + ".pdf"
    make_a4_pdf(tmp_path / long_name)

    ws = _workspace(tmp_path, config)
    ws.pane_view(ws.eligible[0]).selection.choices["stamp"] = False
    _apply(tmp_path, ws, config)

    names = [p.name for p in tmp_path.iterdir() if p.is_file()]
    assert len(names) == 1 and len(names[0]) <= config.filename.max_length
    assert not (tmp_path / long_name).exists()

    restore_all(tmp_path)
    assert [p.name for p in tmp_path.iterdir()] == [long_name]


def test_branch_merge_moves_sources_to_backup_and_restore_splits_again(tmp_path: Path, config):
    for i in (1, 2, 3):
        make_a4_pdf(tmp_path / f"甲007-{i} 領収書.pdf")

    _run_update(tmp_path, config)

    files = sorted(p.name for p in tmp_path.iterdir() if p.is_file())
    assert files == ["甲007-1~3 領収書.pdf"]
    assert len(PdfReader(str(tmp_path / files[0])).pages) == 3
    assert sorted(p.name for p in (tmp_path / BACKUP_DIRNAME).glob("*.pdf")) == [
        f"甲007-{i} 領収書.pdf" for i in (1, 2, 3)
    ]
    # 結合したファイルを読み込み直しても、処理の項目に再びチェックが付かない
    # （結合ライブラリの作成ソフト名がメタデータとして検出されない）。
    assert not _workspace(tmp_path, config).plan_update().has_work

    result = restore_all(tmp_path)
    assert result.restored == 3
    assert sorted(p.name for p in tmp_path.iterdir()) == [f"甲007-{i} 領収書.pdf" for i in (1, 2, 3)]


def test_merge_unchecked_updates_members_individually(tmp_path: Path, config):
    for i in (1, 2):
        make_a4_pdf(tmp_path / f"甲007-{i} 領収書.pdf")
    ws = _workspace(tmp_path, config)
    record = ws.eligible[0]
    assert ws.pane_view(record).is_group
    ws.set_merge(record, False)
    view = ws.pane_view(record)
    assert not view.is_group and view.merge_option is not None
    assert ws.plan_update().counts["merge"] == 0
    assert ws.plan_update().counts["stamp"] == 2


def test_split_moves_files_into_batch_folders_and_restore_moves_them_back(tmp_path: Path, config):
    """200MB分割（案A）：分割が要るときは、フォルダの中の「提出用_NN」へ移す。
    内容を変えないファイルは控えを作らず、元の位置だけを記録する。"""
    names = ["準備書面1.pdf", "準備書面2.pdf", "準備書面3.pdf"]
    for name in names:
        make_multipage_pdf(tmp_path / name, page_count=2)
    size = (tmp_path / names[0]).stat().st_size
    # 1回分の上限を、2ファイルで超える大きさにする。
    small = dataclasses.replace(
        config,
        upload=dataclasses.replace(
            config.upload, max_total_mb=size * 1.5 / (1024 * 1024), safety_margin_ratio=1.0
        ),
    )
    ws = _workspace(tmp_path, small)
    for record in ws.eligible:  # 処理は何も行わない（仕分けだけを見る）
        sel = ws.pane_view(record).selection
        sel.choices.update({key: False for key in sel.choices})
    plan = ws.plan_update()
    assert len(plan.batches) == 3 and plan.move_count == 3 and plan.changed_count == 0

    _apply(tmp_path, ws, small)

    assert (tmp_path / "提出用_01" / names[0]).exists()
    assert (tmp_path / "提出用_03" / names[2]).exists()
    assert not list((tmp_path / BACKUP_DIRNAME).glob("*.pdf"))  # 移しただけ。控えなし

    # 読み込み直すと、サブフォルダを含めない設定でも「提出用_NN」の中が見え、もう動かさない。
    again = _workspace(tmp_path, small)
    assert len(again.records) == 3
    assert again.plan_update().move_count == 0

    assert restore_all(tmp_path).restored == 3
    assert sorted(p.name for p in tmp_path.iterdir()) == names


def test_scan_never_reads_backup_folder(tmp_path: Path, config):
    make_a4_pdf(tmp_path / "甲001 契約書.pdf", with_metadata=True)
    _run_update(tmp_path, config)
    result = scan_folder(tmp_path, config)
    assert [r.path.name for r in result.records] == ["甲001 契約書.pdf"]


def test_restore_does_not_overwrite_a_file_the_user_put_back(tmp_path: Path, config):
    long_name = "甲001 " + "あ" * 110 + ".pdf"
    make_a4_pdf(tmp_path / long_name)
    _run_update(tmp_path, config)
    # 更新の後に、利用者が元の名前で別のファイルを置いた。
    (tmp_path / long_name).write_bytes(b"user file")

    result = restore_all(tmp_path)

    assert result.restored == 0 and len(result.failures) == 1
    assert (tmp_path / long_name).read_bytes() == b"user file"
    assert (tmp_path / BACKUP_DIRNAME / long_name).exists()  # 控えは残る


def test_failed_processing_leaves_the_file_untouched(tmp_path: Path, config):
    src = make_a4_pdf(tmp_path / "甲001 契約書.pdf", with_metadata=True)
    ws = _workspace(tmp_path, config)
    src.write_bytes(b"broken after scan")  # 読み込みの後に壊れた

    manifest = Manifest.load(tmp_path)
    u = ws.plan_update().updates[0]
    with pytest.raises(Exception):
        update_item(tmp_path, u.item, u.dest_dir, manifest, config.evidence_stamp, config.filename)

    assert src.read_bytes() == b"broken after scan"
    assert not manifest.entries
    assert not (tmp_path / BACKUP_DIRNAME / "更新中").exists()


def test_old_work_folder_left_by_an_earlier_version_is_cleared(tmp_path: Path, config):
    """更新の途中のフォルダは「更新中」（2026-11-17 に「作業中」から改めた）。前の名前の残りも消す。"""
    make_a4_pdf(tmp_path / "甲001 契約書.pdf", with_metadata=True)
    old = tmp_path / BACKUP_DIRNAME / "作業中"
    old.mkdir(parents=True)
    (old / "途中.pdf").write_bytes(b"left over")
    ws = _workspace(tmp_path, config)
    u = ws.plan_update().updates[0]
    update_item(tmp_path, u.item, u.dest_dir, Manifest.load(tmp_path), config.evidence_stamp, config.filename)
    assert not old.exists()
    assert not (tmp_path / BACKUP_DIRNAME / "更新中").exists()


def test_corrupt_manifest_raises_value_error(tmp_path: Path):
    (tmp_path / BACKUP_DIRNAME).mkdir()
    (tmp_path / BACKUP_DIRNAME / MANIFEST_FILENAME).write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError):
        Manifest.load(tmp_path)
    assert not has_restorable_update(tmp_path)


def test_stamp_stays_checked_after_update_and_other_finished_items_are_unchecked(tmp_path: Path, config):
    """更新の後に読み込み直すと、証拠番号を付けたファイルは「証拠番号スタンプ」に
    チェックが入った状態になる（2026-11-16 班長指示）。メタデータ除去のように、済むと
    検出されなくなる項目のチェックは外れる。「元のファイルに戻す」と、最初の状態に戻る。"""
    make_a4_pdf(tmp_path / "甲001 契約書.pdf", with_metadata=True)

    before = _workspace(tmp_path, config)
    sel = before.pane_view(before.eligible[0]).selection
    assert sel.choices == {"strip_metadata": True, "stamp": True, **{"compress": False, "reduce": False}} and not sel.stamped

    _apply(tmp_path, before, config)
    after = _workspace(tmp_path, config)
    sel = after.pane_view(after.eligible[0]).selection
    assert sel.choices == {"stamp": True, **{"compress": False, "reduce": False}} and sel.stamped
    assert "実行済み" not in next(o.label for o in sel.options if o.key == "stamp")
    # チェックが入ったままなら、何もしない（もう一度は付けない）。
    assert not after.plan_update().has_work
    assert after.preview_state(after.eligible[0])[0] is None  # プレビューでも重ねて描かない

    restore_all(tmp_path)
    restored = _workspace(tmp_path, config)
    sel = restored.pane_view(restored.eligible[0]).selection
    assert sel.choices == {"strip_metadata": True, "stamp": True, **{"compress": False, "reduce": False}} and not sel.stamped


def test_unchecking_stamp_removes_it_and_keeps_other_processing(tmp_path: Path, config):
    """スタンプ済みのファイルでチェックを外して更新すると、スタンプが消える。
    更新前のファイルから作り直すので、済ませたメタデータ除去はそのまま残る。"""
    src = make_a4_pdf(tmp_path / "甲001 契約書.pdf", with_metadata=True)
    original_bytes = src.read_bytes()
    _run_update(tmp_path, config)
    assert "甲001" in _text(src)

    ws = _workspace(tmp_path, config)
    record = ws.eligible[0]
    ws.pane_view(record).selection.choices["stamp"] = False
    plan = ws.plan_update()
    assert plan.counts["unstamp"] == 1 and plan.counts["stamp"] == 0 and plan.has_work
    # プレビューは、スタンプのない姿（更新したときにできるものと同じ）を描く。
    shown = ws.preview_record(record)
    assert shown is not None and shown.path != record.path and "甲001" not in _text(shown.path)
    assert "甲001" in _text(src)  # プレビューを作っただけでは、ファイルは変わらない

    _apply(tmp_path, ws, config)

    assert "甲001" not in _text(src)
    after = _workspace(tmp_path, config)
    sel = after.pane_view(after.eligible[0]).selection
    assert sel.choices == {"stamp": False, **{"compress": False, "reduce": False}} and not sel.stamped  # メタデータは除去済みのまま
    assert (tmp_path / BACKUP_DIRNAME / "甲001 契約書.pdf").read_bytes() == original_bytes
    restore_all(tmp_path)
    assert src.read_bytes() == original_bytes


def test_unchecking_stamp_on_stamp_only_file_returns_the_original(tmp_path: Path, config):
    """スタンプのほかに何もしていなかったファイルは、更新前のファイルそのものに戻り、
    「元のファイル」フォルダも片付く（一度も更新していないのと同じ）。"""
    from fixtures.generate_fixtures import make_a4_pdf_no_metadata

    src = make_a4_pdf_no_metadata(tmp_path / "甲001 契約書.pdf")
    original_bytes = src.read_bytes()
    ws = _workspace(tmp_path, config)
    assert ws.pane_view(ws.eligible[0]).selection.choices == {"stamp": True, **{"compress": False, "reduce": False}}
    _apply(tmp_path, ws, config)
    assert src.read_bytes() != original_bytes

    ws = _workspace(tmp_path, config)
    assert ws.pane_view(ws.eligible[0]).selection.choices == {"stamp": True, **{"compress": False, "reduce": False}}
    ws.pane_view(ws.eligible[0]).selection.choices["stamp"] = False
    _apply(tmp_path, ws, config)

    assert src.read_bytes() == original_bytes
    assert not list((tmp_path / BACKUP_DIRNAME).glob("*.pdf"))  # 控えは片付く
    assert not has_restorable_update(tmp_path)
    # 消したスタンプに、読み込み直しでまたチェックが付くことはない（次の更新で
    # 意図せず付け直されない）。
    again = _workspace(tmp_path, config)
    assert again.pane_view(again.eligible[0]).selection.choices == {"stamp": False, **{"compress": False, "reduce": False}}
    assert not again.plan_update().has_work
    # チェックを入れ直して更新すれば、また付く。その後は「付けない」の印も消える。
    again.pane_view(again.eligible[0]).selection.choices["stamp"] = True
    _apply(tmp_path, again, config)
    final = _workspace(tmp_path, config)
    assert final.pane_view(final.eligible[0]).selection.stamped
    restore_all(tmp_path)
    assert src.read_bytes() == original_bytes
    assert not (tmp_path / BACKUP_DIRNAME).exists()


def test_unchecking_stamp_on_merged_file_rebuilds_the_merge_without_stamp(tmp_path: Path, config):
    for i in (1, 2, 3):
        make_a4_pdf(tmp_path / f"甲007-{i} 領収書.pdf")
    _run_update(tmp_path, config)
    merged = tmp_path / "甲007-1~3 領収書.pdf"
    assert "甲007-2" in _text(merged)

    ws = _workspace(tmp_path, config)
    sel = ws.pane_view(ws.eligible[0]).selection
    assert sel.stamped and sel.choices["stamp"] is True
    sel.choices["stamp"] = False
    _apply(tmp_path, ws, config)

    assert len(PdfReader(str(merged)).pages) == 3
    assert "甲007" not in _text(merged)
    assert not _workspace(tmp_path, config).plan_update().has_work
    assert restore_all(tmp_path).restored == 3


def test_remove_stamp_fails_without_touching_the_file_when_original_is_missing(tmp_path: Path, config):
    src = make_a4_pdf(tmp_path / "甲001 契約書.pdf", with_metadata=True)
    _run_update(tmp_path, config)
    stamped_bytes = src.read_bytes()
    (tmp_path / BACKUP_DIRNAME / "甲001 契約書.pdf").unlink()

    ws = _workspace(tmp_path, config)
    ws.pane_view(ws.eligible[0]).selection.choices["stamp"] = False
    assert ws.preview_record(ws.eligible[0]) is None
    with pytest.raises(FileNotFoundError):
        _apply(tmp_path, ws, config)
    assert src.read_bytes() == stamped_bytes


def test_second_update_does_not_replace_the_saved_original(tmp_path: Path, config):
    """「元のファイル」に同じファイルが既にあれば保存しない（最初の更新の前の姿を残す）。"""
    src = make_a4_pdf(tmp_path / "甲001 契約書.pdf", with_metadata=True)
    original_bytes = src.read_bytes()
    ws = _workspace(tmp_path, config)
    ws.pane_view(ws.eligible[0]).selection.choices["stamp"] = False
    _apply(tmp_path, ws, config)  # 1回目：メタデータ除去だけ
    first_updated = src.read_bytes()

    ws = _workspace(tmp_path, config)
    ws.pane_view(ws.eligible[0]).selection.choices["stamp"] = True
    _apply(tmp_path, ws, config)  # 2回目：スタンプを付ける

    assert src.read_bytes() != first_updated
    assert (tmp_path / BACKUP_DIRNAME / "甲001 契約書.pdf").read_bytes() == original_bytes
    restore_all(tmp_path)
    assert src.read_bytes() == original_bytes


def test_stamp_unchecked_before_update_stays_unchecked_after_reload(tmp_path: Path, config):
    """証拠番号のあるPDFでスタンプのチェックを外して更新したら、読み込み直しても
    外れたまま（原本に番号が既にあるファイルに、次の更新で付いてしまわない）。
    ［元のファイルに戻す］で戻すと、チェックは再び自動で付く。"""
    make_a4_pdf(tmp_path / "甲001 契約書.pdf", with_metadata=True)
    make_a4_pdf(tmp_path / "甲002 領収書.pdf", with_metadata=True)
    ws = _workspace(tmp_path, config)
    first = next(r for r in ws.eligible if r.path.name.startswith("甲001"))
    ws.pane_view(first).selection.choices["stamp"] = False
    _apply(tmp_path, ws, config)

    after = _workspace(tmp_path, config)
    stamp = {r.path.name[:4]: after.pane_view(r).selection.choices["stamp"] for r in after.eligible}
    assert stamp == {"甲001": False, "甲002": True}
    assert not after.plan_update().has_work

    restore_all(tmp_path)
    restored = _workspace(tmp_path, config)
    assert all(restored.pane_view(r).selection.choices["stamp"] for r in restored.eligible)


def test_empty_read_only_folders_are_removed(tmp_path: Path):
    """OneDrive の下のフォルダには「読み取り専用」の印が付いていることがあり、Windows は、空でも
    その印のあるフォルダを消さない。［元のファイルに戻す］の後に、空のフォルダを残さない。"""
    import os
    import stat

    from mints_prep.processors.updater import _remove_empty_dirs

    top = tmp_path / "元のファイル"
    inner = top / "材料"
    inner.mkdir(parents=True)
    keep = tmp_path / "残す"
    keep.mkdir()
    (keep / "a.txt").write_text("x", encoding="utf-8")
    os.chmod(inner, stat.S_IREAD)
    os.chmod(top, stat.S_IREAD)
    _remove_empty_dirs(top)
    _remove_empty_dirs(keep)
    assert not top.exists()
    assert (keep / "a.txt").exists()  # 空でないフォルダは消さない
