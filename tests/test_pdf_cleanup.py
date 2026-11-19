"""用紙を A4 にそろえる（4.15）・付箋・コメント・添付ファイルを取り除く（4.16）。Ver300。"""

from pathlib import Path

import pikepdf
import pytest
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from fixtures.generate_fixtures import make_a4_pdf, make_letter_size_pdf, make_pdf
from va_mints.config import PdfConfig
from va_mints.detectors.pdf_detector import detect_pdf
from va_mints.processors.pdf_cleanup import clean_pdf, inspect_hidden_content
from va_mints.processors.updater import (
    APPLIED_FIT_A4,
    APPLIED_STRIP_HIDDEN,
    BACKUP_DIRNAME,
    Manifest,
    restore_all,
)
from va_mints.scanner.walker import scan_folder
from va_mints.ui.workspace import Workspace, apply_all_updates, describe_update_plan
from va_mints.user_settings import (
    UserSettings,
    apply_user_settings,
    load_user_settings,
    save_user_settings,
)

A4_W, A4_H = A4
PDF_CONFIG = PdfConfig(a4_mm=(210, 297), a3_mm=(297, 420), size_tolerance_mm=3)


def _box(page) -> tuple[float, float]:
    box = page.mediabox
    return float(box.width), float(box.height)


def _text(path: Path) -> str:
    return "".join(p.extract_text() for p in PdfReader(str(path)).pages)


def _make_with_link(path: Path, pagesize) -> Path:
    c = canvas.Canvas(str(path), pagesize=pagesize)
    c.drawString(72, pagesize[1] - 72, "hello fit")
    c.linkURL("https://example.invalid/", (60, pagesize[1] - 80, 200, pagesize[1] - 60))
    c.showPage()
    c.save()
    return path


# ------------------------------------------------------------------ A4 にそろえる
def test_letter_page_is_shrunk_onto_a4_and_keeps_text_and_link(tmp_path: Path):
    src = _make_with_link(tmp_path / "letter.pdf", (612, 792))
    out = tmp_path / "out.pdf"
    clean_pdf(src, out, fit_a4=PDF_CONFIG)
    reader = PdfReader(str(out))
    w, h = _box(reader.pages[0])
    assert w == pytest.approx(A4_W, abs=0.1) and h == pytest.approx(A4_H, abs=0.1)
    assert "hello fit" in _text(out)  # 画像にしていない（文字が残る）
    annots = reader.pages[0]["/Annots"]
    rect = [float(v) for v in annots[0].get_object()["/Rect"]]
    # リンクも縮めた位置に動き、用紙の中に収まる
    assert 0 <= rect[0] < rect[2] <= A4_W and 0 <= rect[1] < rect[3] <= A4_H
    assert detect_pdf(out, PDF_CONFIG).non_a4a3_pages == ()


def test_small_page_is_placed_at_original_size_in_the_middle(tmp_path: Path):
    src = make_pdf(tmp_path / "a5.pdf", (300, 400))  # A5 より小さい用紙
    out = tmp_path / "out.pdf"
    clean_pdf(src, out, fit_a4=PDF_CONFIG)
    with pikepdf.open(out) as pdf:
        content = pdf.pages[0].obj.Contents.read_bytes().decode()
    # 拡大しない（倍率 1）。真ん中に置く。
    scale, _b, _c, _d, tx, ty = (float(v) for v in content.split("cm")[0].split()[-6:])
    assert scale == pytest.approx(1.0)
    assert tx == pytest.approx((A4_W - 300) / 2, abs=0.1)
    assert ty == pytest.approx((A4_H - 400) / 2, abs=0.1)


def test_landscape_page_goes_onto_landscape_a4_and_a4_pages_are_untouched(tmp_path: Path):
    c = canvas.Canvas(str(tmp_path / "mixed.pdf"))
    c.setPageSize(A4)
    c.drawString(72, 700, "page one a4")
    c.showPage()
    c.setPageSize((792, 612))  # レター・横
    c.drawString(72, 500, "page two letter landscape")
    c.showPage()
    c.save()
    out = tmp_path / "out.pdf"
    clean_pdf(tmp_path / "mixed.pdf", out, fit_a4=PDF_CONFIG)
    with pikepdf.open(tmp_path / "mixed.pdf") as before, pikepdf.open(out) as after:
        assert after.pages[0].obj.Contents.read_bytes() == before.pages[0].obj.Contents.read_bytes()
        assert float(after.pages[1].mediabox[2]) == pytest.approx(A4_H, abs=0.1)
        assert float(after.pages[1].mediabox[3]) == pytest.approx(A4_W, abs=0.1)


def test_rotated_page_is_judged_by_how_it_looks(tmp_path: Path):
    src = make_letter_size_pdf(tmp_path / "rot.pdf")
    with pikepdf.open(src, allow_overwriting_input=True) as pdf:
        pdf.pages[0].obj.Rotate = 90  # 縦のレターを横に見せる
        pdf.save(src)
    out = tmp_path / "out.pdf"
    clean_pdf(src, out, fit_a4=PDF_CONFIG)
    with pikepdf.open(out) as pdf:
        page = pdf.pages[0]
        assert int(page.obj.get("/Rotate", 0)) == 0
        assert float(page.mediabox[2]) == pytest.approx(A4_H, abs=0.1)  # 横の A4
    finding = detect_pdf(out, PDF_CONFIG)
    assert finding.page_size_judgments == ("A4",) and finding.page_orientations == ("横",)


# ------------------------------------------------------------------ 付箋・コメント・添付ファイル
def _make_with_hidden(path: Path) -> Path:
    make_a4_pdf(path)
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        page = pdf.pages[0]
        popup = pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Popup, Rect=[300, 600, 400, 700],
        ))
        highlight = pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Highlight, Rect=[70, 696, 260, 714],
            QuadPoints=[70, 714, 260, 714, 70, 696, 260, 696], C=[1, 1, 0],
            T=pikepdf.String("事務員A"), Contents=pikepdf.String("ここが大事"), Popup=popup,
        ))
        note = pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Text, Rect=[500, 750, 520, 770],
            Contents=pikepdf.String("相手方に有利かも"),
        ))
        link = pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Link, Rect=[10, 10, 50, 50],
            A=pikepdf.Dictionary(S=pikepdf.Name.JavaScript, JS=pikepdf.String("app.alert(1)")),
        ))
        free = pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.FreeText, Rect=[72, 100, 300, 130],
            Contents=pikepdf.String("見えている文字"), T=pikepdf.String("事務員A"),
            DA=pikepdf.String("/Helv 12 Tf 0 g"),
        ))
        page.obj.Annots = pdf.make_indirect(pikepdf.Array([highlight, popup, note, link, free]))
        pdf.attachments["メモ.txt"] = pikepdf.AttachedFileSpec(pdf, b"internal memo", mime_type="text/plain")
        pdf.Root.OpenAction = pikepdf.Dictionary(S=pikepdf.Name.JavaScript, JS=pikepdf.String("1"))
        pdf.save(path)
    return path


def test_hidden_content_is_counted_and_removed_keeping_the_look(tmp_path: Path):
    src = _make_with_hidden(tmp_path / "hidden.pdf")
    finding = detect_pdf(src, PDF_CONFIG)
    # 付箋1・ハイライトのコメント1・テキストボックスの作成者名1、添付ファイル1、JavaScript
    assert (finding.hidden_comments, finding.hidden_attachments, finding.has_javascript) == (3, 1, True)

    out = tmp_path / "out.pdf"
    clean_pdf(src, out, strip_hidden=True)
    with pikepdf.open(out) as pdf:
        assert not inspect_hidden_content(pdf).found
        assert len(pdf.attachments) == 0 and "/OpenAction" not in pdf.Root
        annots = {str(a.Subtype): a for a in pdf.pages[0].obj.Annots}
        # 付箋・ポップアップは消え、ハイライト・リンク・テキストボックスは残る
        assert set(annots) == {"/Highlight", "/Link", "/FreeText"}
        assert "/Contents" not in annots["/Highlight"] and "/T" not in annots["/Highlight"]
        assert "/QuadPoints" in annots["/Highlight"]  # 見た目は変えない
        assert "/A" not in annots["/Link"]
        assert str(annots["/FreeText"].Contents) == "見えている文字" and "/T" not in annots["/FreeText"]
    assert not detect_pdf(out, PDF_CONFIG).has_hidden_content


# ------------------------------------------------------------------ 画面の選択と更新
def _ws(root: Path, config) -> Workspace:
    return Workspace(scan_folder(root, config), config, Manifest.load(root))


def _rec(ws: Workspace, name: str):
    return next(r for r in ws.records if r.path.name == name)


def test_fit_is_offered_checked_and_applied_before_the_stamp(tmp_path: Path, config):
    root = tmp_path / "証拠"
    root.mkdir()
    make_letter_size_pdf(root / "甲001 契約書.pdf")
    ws = _ws(root, config)
    sel = ws.pane_view(_rec(ws, "甲001 契約書.pdf")).selection
    keys = {o.key: o for o in sel.options if o.enabled}
    assert keys["fit_a4"].default_checked and "strip_hidden" not in keys
    # 用紙の注意（検出のみ）は、そろえる項目に置き換わる
    assert not any("用紙寸法" in o.label for o in sel.options if not o.enabled)
    # プレビューは、そろえた姿
    path, _i, _r, label = ws.preview_page(_rec(ws, "甲001 契約書.pdf"), 0)
    assert path != root / "甲001 契約書.pdf" and label == "甲001"
    assert detect_pdf(path, config.pdf).non_a4a3_pages == ()

    plan = ws.plan_update()
    assert plan.counts[APPLIED_FIT_A4] == 1
    assert "用紙をA4にそろえる：1 件" in describe_update_plan(plan, 200)
    apply_all_updates(root, plan, config)

    target = root / "甲001 契約書.pdf"
    assert detect_pdf(target, config.pdf).non_a4a3_pages == ()
    assert "甲001" in _text(target)  # スタンプはそろえた後に押す
    entry = Manifest.load(root).get("甲001 契約書.pdf")
    assert APPLIED_FIT_A4 in entry.applied

    # 読み込み直すと、もうそろえる項目は出ない。ページを編集し直しても（元のファイルから
    # 作り直す）、A4 のまま。
    ws = _ws(root, config)
    record = _rec(ws, "甲001 契約書.pdf")
    assert "fit_a4" not in {o.key for o in ws.pane_view(record).selection.options}
    ws.rotate_pages(record, [0], 180)
    apply_all_updates(root, ws.plan_update(), config)
    assert detect_pdf(target, config.pdf).non_a4a3_pages == ()
    assert APPLIED_FIT_A4 in Manifest.load(root).get("甲001 契約書.pdf").applied

    restore_all(root)
    assert detect_pdf(target, config.pdf).non_a4a3_pages == (1,)
    assert not (root / BACKUP_DIRNAME).exists()


def test_removing_the_stamp_of_a_fitted_file_keeps_it_a4(tmp_path: Path, config):
    root = tmp_path / "証拠"
    root.mkdir()
    make_letter_size_pdf(root / "甲003 領収書.pdf")
    ws = _ws(root, config)
    apply_all_updates(root, ws.plan_update(), config)
    ws = _ws(root, config)
    record = _rec(ws, "甲003 領収書.pdf")
    sel = ws.pane_view(record).selection
    assert sel.stamped
    sel.choices["stamp"] = False  # スタンプを消す（元のファイルから作り直す）
    apply_all_updates(root, ws.plan_update(), config)
    target = root / "甲003 領収書.pdf"
    assert "甲003" not in _text(target)
    assert detect_pdf(target, config.pdf).non_a4a3_pages == ()


def test_strip_hidden_is_not_checked_and_the_confirmation_says_what_remains(tmp_path: Path, config):
    root = tmp_path / "証拠"
    root.mkdir()
    _make_with_hidden(root / "乙001 メール.pdf")
    ws = _ws(root, config)
    record = _rec(ws, "乙001 メール.pdf")
    sel = ws.pane_view(record).selection
    option = next(o for o in sel.options if o.key == "strip_hidden")
    assert not option.default_checked
    assert "付箋・コメント（作成者名を含む）3件" in option.label and "添付ファイル1件" in option.label

    plan = ws.plan_update()
    assert plan.hidden_remaining_count == 1
    assert "付箋・コメント・添付ファイルが残るファイル：1 件" in describe_update_plan(plan, 200)

    sel.choices["strip_hidden"] = True
    plan = ws.plan_update()
    assert plan.hidden_remaining_count == 0 and plan.counts[APPLIED_STRIP_HIDDEN] == 1
    apply_all_updates(root, plan, config)
    assert not detect_pdf(root / "乙001 メール.pdf", config.pdf).has_hidden_content


def test_fit_for_branch_merge_group(tmp_path: Path, config):
    root = tmp_path / "証拠"
    root.mkdir()
    make_letter_size_pdf(root / "甲002-1 写真.pdf")
    make_a4_pdf(root / "甲002-2 写真.pdf")
    ws = _ws(root, config)
    view = ws.pane_view(_rec(ws, "甲002-1 写真.pdf"))
    assert view.is_group and view.selection.choices.get("fit_a4") is True
    plan = ws.plan_update()
    # 枝番ファイル結合を、ページの編集とは数えない
    assert plan.counts[APPLIED_FIT_A4] == 1 and plan.counts["pages"] == 0
    apply_all_updates(root, plan, config)
    merged = next(p for p in root.iterdir() if p.suffix == ".pdf")
    assert detect_pdf(merged, config.pdf).non_a4a3_pages == ()
    assert len(PdfReader(str(merged)).pages) == 2


# ------------------------------------------------------------------ スタンプの見た目（オプション）
def test_stamp_style_user_setting_round_trip(tmp_path: Path, config):
    path = tmp_path / "settings.json"
    save_user_settings(UserSettings(stamp_style="red_boxed"), path)
    loaded = load_user_settings(path)
    assert loaded.stamp_style == "red_boxed"
    assert apply_user_settings(config, loaded).evidence_stamp.style == "red_boxed"
    # 知らない値は、無いものとして扱う
    path.write_text('{"stamp_style": "blue"}', encoding="utf-8")
    assert load_user_settings(path).stamp_style is None
    # 既定のままなら、ファイルを消す
    save_user_settings(UserSettings(), path)
    assert not path.exists()
