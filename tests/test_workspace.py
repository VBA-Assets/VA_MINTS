"""3ペインの画面の選択の状態と、更新の確認の文（ui/workspace.py。Ver200）。"""

from pathlib import Path

from fixtures.generate_fixtures import (
    make_a3_pdf,
    make_a4_pdf,
    make_image_with_metadata,
    make_unsupported_file,
)
from mints_prep.scanner.walker import scan_folder
from mints_prep.ui.workspace import (
    Workspace,
    describe_failure,
    describe_update_plan,
    format_skipped,
)


def _ws(root: Path, config) -> Workspace:
    return Workspace(scan_folder(root, config), config)


def _by_name(ws: Workspace, name: str):
    return next(r for r in ws.records if r.path.name == name)


def test_checks_are_set_automatically_from_detection(tmp_path: Path, config):
    make_a4_pdf(tmp_path / "甲001 契約書.pdf", with_metadata=True)
    make_a4_pdf(tmp_path / "準備書面.pdf")
    make_image_with_metadata(tmp_path / "甲002 写真.jpg")
    ws = _ws(tmp_path, config)

    assert ws.pane_view(_by_name(ws, "甲001 契約書.pdf")).selection.choices == {
        "strip_metadata": True,
        "stamp": True,
        "compress": False,  # 圧縮（4.13）：小さいファイルには自動でチェックを付けない
        "reduce": False,
    }
    # 証拠番号のないファイルにはスタンプの項目がない。
    assert "stamp" not in ws.pane_view(_by_name(ws, "準備書面.pdf")).selection.choices
    # 画像のスタンプは、項目は出すが既定はOFF（要件定義書 第4章）。
    assert ws.pane_view(_by_name(ws, "甲002 写真.jpg")).selection.choices["stamp"] is False


def test_unsupported_file_has_no_options(tmp_path: Path, config):
    make_unsupported_file(tmp_path / "図面.zip")
    ws = _ws(tmp_path, config)
    view = ws.pane_view(ws.records[0])
    assert view.selection is None and view.merge_option is None
    assert ws.output_name(ws.records[0]) is None
    assert not ws.plan_update().has_work


def test_preview_state_follows_checks(tmp_path: Path, config):
    make_a3_pdf(tmp_path / "甲001 図面.pdf")  # A3縦＝向き不一致
    ws = _ws(tmp_path, config)
    record = ws.records[0]
    sel = ws.pane_view(record).selection
    assert ws.preview_state(record) == ("甲001", None)
    sel.choices["stamp"] = False
    assert ws.preview_state(record) == (None, None)

    # 向きの注意（A3の縦向き）：90°回せば外れ、180°では外れない。
    assert ws.orientation_pages(record) == [0]
    ws.rotate_pages(record, [0], 180)
    assert ws.orientation_pages(record) == [0]
    ws.rotate_pages(record, [0], 90)
    assert ws.orientation_pages(record) == []


def test_merged_member_preview_uses_its_own_branch_label(tmp_path: Path, config):
    for i in (1, 2):
        make_a4_pdf(tmp_path / f"甲007-{i} 領収書.pdf")
    ws = _ws(tmp_path, config)
    second = _by_name(ws, "甲007-2 領収書.pdf")
    assert ws.preview_state(second)[0] == "甲007-2"
    assert ws.output_name(second) == "甲007-1~2 領収書.pdf"
    ws.set_merge(second, False)
    assert ws.output_name(second) == "甲007-2 領収書.pdf"


def test_confirmation_text_shows_counts_and_unviewed_files(tmp_path: Path, config):
    make_a4_pdf(tmp_path / "甲001 契約書.pdf", with_metadata=True)
    make_a4_pdf(tmp_path / "甲003 領収書.pdf", with_metadata=True)
    ws = _ws(tmp_path, config)
    ws.mark_viewed(_by_name(ws, "甲001 契約書.pdf"))

    plan = ws.plan_update()
    text = describe_update_plan(plan, config.upload.max_total_mb)

    assert plan.unviewed_count == 1
    assert "・メタデータ除去：2 件" in text
    assert "・証拠番号スタンプ：2 件" in text
    assert "1 件は、まだプレビューを表示していません" in text
    assert "号証の連番・欠番の疑い" in text  # 甲002 が欠番
    assert "「元のファイル」フォルダ" in text
    assert "ファイル名整形" not in text  # 0件の処理は書かない


def test_confirmation_text_counts_images_without_stamp(tmp_path: Path, config):
    """画像のスタンプは自動ではチェックが付かないので、付けない画像の数を確認に示す。"""
    make_a4_pdf(tmp_path / "甲001 契約書.pdf", with_metadata=True)
    make_image_with_metadata(tmp_path / "甲002 写真.jpg")
    make_image_with_metadata(tmp_path / "甲003 写真.jpg")
    make_image_with_metadata(tmp_path / "現場写真.jpg")  # 証拠番号なし＝スタンプの対象外
    ws = _ws(tmp_path, config)

    plan = ws.plan_update()
    assert plan.unstamped_image_count == 2
    assert "・証拠番号スタンプを付けない画像：2 件" in describe_update_plan(
        plan, config.upload.max_total_mb
    )

    ws.pane_view(_by_name(ws, "甲002 写真.jpg")).selection.choices["stamp"] = True
    plan = ws.plan_update()
    assert plan.unstamped_image_count == 1 and plan.counts["stamp"] == 2

    ws.pane_view(_by_name(ws, "甲003 写真.jpg")).selection.choices["stamp"] = True
    assert "付けない画像" not in describe_update_plan(ws.plan_update(), config.upload.max_total_mb)


def test_no_work_when_all_checks_removed(tmp_path: Path, config):
    make_a4_pdf(tmp_path / "甲001 契約書.pdf", with_metadata=True)
    ws = _ws(tmp_path, config)
    sel = ws.pane_view(ws.records[0]).selection
    for key in sel.choices:
        sel.choices[key] = False
    plan = ws.plan_update()
    assert not plan.has_work and plan.changed_count == 0


def test_skipped_files_are_listed_with_name_and_reason():
    """更新できなかったファイルは、完了のお知らせに名前と理由を並べる（レポートは廃止）。"""
    assert format_skipped([]) == ""

    locked = describe_failure("甲001 契約書.pdf", PermissionError(13, "Permission denied"))
    assert locked.startswith("甲001 契約書.pdf（") and "他のアプリで開いている可能性" in locked
    broken = describe_failure("甲002 領収書.pdf", ValueError("bad\n  xref " + "x" * 200))
    assert "ValueError：bad xref" in broken and broken.endswith("…）")

    text = format_skipped([locked, broken])
    assert "次の 2 件は更新できませんでした" in text
    assert f"・{locked}" in text and f"・{broken}" in text

    many = format_skipped([f"{i}.pdf（理由）" for i in range(13)])
    assert "...ほか3件" in many
