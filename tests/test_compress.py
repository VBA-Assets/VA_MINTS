"""圧縮（Ver300。要件定義書 4.13）。"""

import dataclasses
import functools
import io
from pathlib import Path

import pikepdf
import pytest
from PIL import Image, ImageDraw
from pypdf import PdfReader

from mints_prep.config import CompressConfig, load_config
from mints_prep.processors.compress import (
    compress_pdf_lossless,
    compressed_size,
    reduce_image_file,
    reduce_pdf_images,
)
from mints_prep.processors.updater import APPLIED_COMPRESS, APPLIED_REDUCE, Manifest, restore_all
from mints_prep.scanner.walker import scan_folder
from mints_prep.ui.workspace import apply_all_updates, Workspace, describe_update_plan


def _scan_image(size=(2480, 3508), text="page") -> Image.Image:
    """300dpi の A4 のスキャンに似せた画像（紙の地のざらつきと、字の行）。作るのに時間が
    かかるので、同じものは使い回す（写しを返す）。"""
    return _scan_image_cached(size, text).copy()


@functools.lru_cache(maxsize=None)
def _scan_image_cached(size, text) -> Image.Image:
    noise = Image.effect_noise(size, 12).convert("RGB")
    image = Image.blend(Image.new("RGB", size, (235, 232, 225)), noise, 0.15)
    draw = ImageDraw.Draw(image)
    for y in range(200, size[1] - 200, 80):
        draw.text((150, y), f"{text} line {y} " * 6, fill=(20, 20, 20))
    return image


def make_scanned_pdf(path: Path, pages: int = 2, png: bool = False, size=(2480, 3508)) -> Path:
    """スキャン画像（既定は 300dpi）だけのページからなる PDF（文字のレイヤーは「scan page N」）。"""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=A4)
    for i in range(pages):
        buf = io.BytesIO()
        _scan_image(size=size, text=f"p{i}").save(buf, "PNG" if png else "JPEG", quality=92)
        c.drawImage(ImageReader(io.BytesIO(buf.getvalue())), 0, 0, *A4)
        c.drawString(72, 72, f"scan page {i + 1}")
        c.showPage()
    c.save()
    return path


def _images(page: pikepdf.Page) -> list[pikepdf.Object]:
    xobjects = page.obj.Resources.get("/XObject", {})
    return [o for _n, o in xobjects.items() if o.get("/Subtype") == pikepdf.Name.Image]


def _image_sizes(path: Path) -> list[tuple[int, int]]:
    with pikepdf.open(path) as pdf:
        return [(int(obj.Width), int(obj.Height)) for page in pdf.pages for obj in _images(page)]


def test_lossless_keeps_pages_text_and_image_data(tmp_path: Path):
    src = make_scanned_pdf(tmp_path / "a.pdf", pages=1, png=True, size=(1240, 1754))
    out = tmp_path / "out.pdf"
    compress_pdf_lossless(src, out)
    assert out.stat().st_size <= src.stat().st_size
    before, after = PdfReader(str(src)), PdfReader(str(out))
    assert len(after.pages) == len(before.pages)
    assert [p.extract_text() for p in after.pages] == [p.extract_text() for p in before.pages]
    assert _image_sizes(out) == _image_sizes(src)
    with pikepdf.open(src) as a, pikepdf.open(out) as b:
        img_a = _images(a.pages[0])[0]
        img_b = _images(b.pages[0])[0]
        assert img_a.read_bytes() == img_b.read_bytes()  # 画素は1つも変わらない


def test_lossless_copies_the_file_when_it_does_not_get_smaller(tmp_path: Path):
    src = make_scanned_pdf(tmp_path / "a.pdf")
    once = tmp_path / "once.pdf"
    twice = tmp_path / "twice.pdf"
    compress_pdf_lossless(src, once)
    compress_pdf_lossless(once, twice)
    assert twice.read_bytes() == once.read_bytes()


def test_reduce_pdf_images_lowers_resolution_and_keeps_the_layout(tmp_path: Path):
    src = make_scanned_pdf(tmp_path / "a.pdf")
    out = tmp_path / "out.pdf"
    result = reduce_pdf_images(src, out, 150, 75)
    assert result.images == 2
    assert out.stat().st_size < src.stat().st_size / 3
    # A4（8.27 インチ）の幅に 150dpi → 約 1240 画素。
    assert all(abs(w - 1240) <= 2 for w, _h in _image_sizes(out))
    before, after = PdfReader(str(src)), PdfReader(str(out))
    assert [p.mediabox for p in after.pages] == [p.mediabox for p in before.pages]
    assert [p.extract_text() for p in after.pages] == [p.extract_text() for p in before.pages]


def test_reduce_leaves_small_and_unsafe_images_alone(tmp_path: Path):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    src = tmp_path / "a.pdf"
    c = canvas.Canvas(str(src), pagesize=A4)
    small = io.BytesIO()
    _scan_image(size=(600, 800)).save(small, "JPEG")  # 約 72dpi：縮めない
    c.drawImage(ImageReader(io.BytesIO(small.getvalue())), 0, 0, 300, 400)
    masked = io.BytesIO()
    # 透明の情報（SMask）付き：触れない（約170dpi。150dpi より細かいが、縮めない）
    _scan_image(size=(1400, 1980)).convert("RGBA").save(masked, "PNG")
    c.drawImage(ImageReader(io.BytesIO(masked.getvalue())), 0, 0, *A4, mask="auto")
    c.showPage()
    c.save()
    out = tmp_path / "out.pdf"
    result = reduce_pdf_images(src, out, 150, 75)
    assert result.images == 0 and result.skipped == 1
    assert sorted(_image_sizes(out)) == sorted(_image_sizes(src))


def test_reduce_jpeg_file_applies_orientation_and_keeps_other_exif(tmp_path: Path):
    src = tmp_path / "photo.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6  # 右に90°回して見る
    exif[0x010F] = "SampleCamera"
    _scan_image(size=(4000, 3000)).save(src, "JPEG", quality=95, exif=exif.tobytes())
    out = tmp_path / "out.jpg"
    assert reduce_image_file(src, out, "JPEG", 150, 80)
    with Image.open(out) as im:
        assert im.format == "JPEG"
        assert im.height > im.width  # 向きを画素に反映した（縦長）
        assert max(im.size) == pytest.approx(11.69 * 150, abs=3)
        kept = im.getexif()
        assert kept.get(0x0112) == 1 and kept.get(0x010F) == "SampleCamera"
    assert out.stat().st_size < src.stat().st_size


def test_reduce_png_stays_png_and_small_images_are_copied(tmp_path: Path):
    src = tmp_path / "scan.png"
    _scan_image().save(src)
    out = tmp_path / "out.png"
    assert reduce_image_file(src, out, "PNG", 150, 80)
    with Image.open(out) as im:
        assert im.format == "PNG"
    small = tmp_path / "small.png"
    _scan_image(size=(500, 700)).save(small)
    copied = tmp_path / "copied.png"
    assert not reduce_image_file(small, copied, "PNG", 150, 80)
    assert copied.read_bytes() == small.read_bytes()


def test_compressed_size_estimate(tmp_path: Path):
    src = make_scanned_pdf(tmp_path / "a.pdf")
    work = tmp_path / "work"
    work.mkdir()
    size = compressed_size(src, "PDF", True, 150, 75, work)
    assert 0 < size < src.stat().st_size / 3
    assert list(work.iterdir()) == []  # 作業の一時ファイルは残さない


# ---------------------------------------------------------------- 画面の選択と更新


def _ws(root: Path, config) -> Workspace:
    return Workspace(scan_folder(root, config), config, Manifest.load(root))


def _apply(root: Path, ws: Workspace, config) -> None:
    """画面の［ファイルを更新］と同じ手順で、計画のすべてを行う。"""
    apply_all_updates(root, ws.plan_update(), config)


def test_lossless_is_checked_automatically_only_for_large_files(tmp_path: Path, config):
    config = dataclasses.replace(config, compress=CompressConfig(auto_lossless_mb=0.5))
    make_scanned_pdf(tmp_path / "甲001 大きい.pdf")
    small = tmp_path / "甲002 小さい.pdf"
    from fixtures.generate_fixtures import make_a4_pdf_no_metadata

    make_a4_pdf_no_metadata(small)
    ws = _ws(tmp_path, config)
    big_sel = ws.pane_view(next(r for r in ws.records if "大きい" in r.path.name)).selection
    small_sel = ws.pane_view(next(r for r in ws.records if "小さい" in r.path.name)).selection
    assert big_sel.choices["compress"] is True and big_sel.choices["reduce"] is False
    assert small_sel.choices["compress"] is False and small_sel.choices["reduce"] is False


def test_reduce_update_records_and_restores(tmp_path: Path, config):
    src = make_scanned_pdf(tmp_path / "甲001 写真.pdf")
    original = src.read_bytes()
    ws = _ws(tmp_path, config)
    record = ws.eligible[0]
    sel = ws.pane_view(record).selection
    sel.choices["reduce"] = True
    sel.reduce_dpi = 150
    plan = ws.plan_update()
    assert plan.counts[APPLIED_REDUCE] == 1
    assert "画像を軽くする" in describe_update_plan(plan, 200)

    _apply(tmp_path, ws, config)
    assert src.stat().st_size < len(original) / 3
    entry = Manifest.load(tmp_path).get("甲001 写真.pdf")
    assert APPLIED_REDUCE in entry.applied and entry.reduce_dpi == 150

    # 読み込み直すと、②はチェックの入った状態（済んでいる）。そのまま更新しても何もしない。
    again = _ws(tmp_path, config)
    sel = again.pane_view(again.eligible[0]).selection
    assert sel.choices["reduce"] is True and sel.reduced_dpi == 150 and sel.reduce_dpi == 150
    assert again.estimate_key(again.eligible[0]) is None
    assert not any(u.keys for u in again.plan_update().updates if "写真" in u.item.output_filename)

    assert restore_all(tmp_path).failures == []
    assert src.read_bytes() == original


def test_unchecking_reduce_rebuilds_the_pdf_from_the_original(tmp_path: Path, config):
    """済ませた②のチェックを外して更新すると、元のファイルから作り直す（スタンプは押し直す）。"""
    src = make_scanned_pdf(tmp_path / "甲001 写真.pdf")
    original_size = src.stat().st_size
    ws = _ws(tmp_path, config)
    sel = ws.pane_view(ws.eligible[0]).selection
    sel.choices["reduce"] = True
    sel.reduce_dpi = 200  # 見本は 300dpi のスキャンなので、既定（300dpi）では縮まない
    _apply(tmp_path, ws, config)
    assert src.stat().st_size < original_size / 3

    again = _ws(tmp_path, config)
    sel = again.pane_view(again.eligible[0]).selection
    assert sel.stamped and sel.choices["stamp"] is True
    sel.choices["reduce"] = False
    plan = again.plan_update()
    text = describe_update_plan(plan, 200)
    assert "圧縮をやめる・解像度を変える：1 件" in text and "ページの編集" not in text
    _apply(tmp_path, again, config)
    assert src.stat().st_size > original_size * 0.9  # 元の解像度に戻った
    assert _image_sizes(src) == [(2480, 3508)] * 2
    text = "".join(p.extract_text() for p in PdfReader(str(src)).pages)
    assert text.count("甲001") == 1  # スタンプは1つだけ（二重にならない）
    entry = Manifest.load(tmp_path).get("甲001 写真.pdf")
    assert APPLIED_REDUCE not in entry.applied and entry.reduce_dpi is None


def test_changing_dpi_of_a_reduced_image_rebuilds_from_the_original(tmp_path: Path, config):
    src = tmp_path / "甲001 写真.jpg"
    _scan_image(size=(4000, 3000)).save(src, "JPEG", quality=95)
    ws = _ws(tmp_path, config)
    sel = ws.pane_view(ws.eligible[0]).selection
    sel.choices["reduce"] = True
    sel.reduce_dpi = 150
    _apply(tmp_path, ws, config)
    with Image.open(src) as im:
        small = im.size

    again = _ws(tmp_path, config)
    sel = again.pane_view(again.eligible[0]).selection
    sel.reduce_dpi = 200  # 元の画像から 200dpi で作り直す（粗くした画像を広げはしない）
    _apply(tmp_path, again, config)
    with Image.open(src) as im:
        assert im.size[0] > small[0] and max(im.size) == pytest.approx(11.69 * 200, abs=3)
    assert Manifest.load(tmp_path).get("甲001 写真.jpg").reduce_dpi == 200

    third = _ws(tmp_path, config)
    third.pane_view(third.eligible[0]).selection.choices["reduce"] = False
    _apply(tmp_path, third, config)
    with Image.open(src) as im:
        assert im.size == (4000, 3000)  # 元の画像に戻った
    assert restore_all(tmp_path).failures == []


def test_page_edit_of_a_reduced_file_reduces_again(tmp_path: Path, config):
    """軽くしたファイルのページを編集し直すと、元のファイルから作り直すが、軽くするのも
    やり直す（ページを1枚消しただけで、元の大きさに戻らない）。"""
    src = make_scanned_pdf(tmp_path / "甲001 写真.pdf", pages=3)
    ws = _ws(tmp_path, config)
    sel = ws.pane_view(ws.eligible[0]).selection
    sel.choices["reduce"] = True
    sel.reduce_dpi = 200
    sel.choices["compress"] = True
    _apply(tmp_path, ws, config)
    reduced_size = src.stat().st_size

    again = _ws(tmp_path, config)
    again.delete_pages(again.eligible[0], [2])
    plan = again.plan_update()
    assert plan.counts[APPLIED_REDUCE] == 1 and plan.counts[APPLIED_COMPRESS] == 1
    _apply(tmp_path, again, config)
    assert len(PdfReader(str(src)).pages) == 2
    assert src.stat().st_size < reduced_size  # 3ページの軽くしたものより小さい
    entry = Manifest.load(tmp_path).get("甲001 写真.pdf")
    assert entry.reduce_dpi == 200  # 選んだ解像度のまま（既定に戻らない）


def test_compression_only_update_keeps_the_page_table(tmp_path: Path, config):
    """ページを編集して更新したファイルに、圧縮だけをしても、ページの表を記録に残す
    （残さないと、次にページを編集したときに、元のファイルの全ページから作り直してしまう）。"""
    src = make_scanned_pdf(tmp_path / "甲001 写真.pdf", pages=3)
    ws = _ws(tmp_path, config)
    ws.delete_pages(ws.eligible[0], [0])
    _apply(tmp_path, ws, config)

    again = _ws(tmp_path, config)
    again.pane_view(again.eligible[0]).selection.choices["compress"] = True
    _apply(tmp_path, again, config)
    entry = Manifest.load(tmp_path).get("甲001 写真.pdf")
    assert entry.pages is not None and len(entry.pages) == 2

    third = _ws(tmp_path, config)
    third.rotate_pages(third.eligible[0], [0], 90)
    _apply(tmp_path, third, config)
    assert len(PdfReader(str(src)).pages) == 2


def test_estimate_is_used_for_200mb_batches(tmp_path: Path, config):
    src = make_scanned_pdf(tmp_path / "甲001 写真.pdf")
    ws = _ws(tmp_path, config)
    record = ws.eligible[0]
    ws.pane_view(record).selection.choices["reduce"] = True
    key = ws.estimate_key(record)
    assert key is not None and ws.estimate(record) is None
    ws.set_estimate(key, 1234)
    assert ws.estimate(record) == 1234
    assert ws.planned_items()[0].record.size_bytes == 1234
    assert src.stat().st_size > 1234


def test_config_compress_section(tmp_path: Path):
    base = Path(__file__).resolve().parent.parent / "config.yaml"
    text = base.read_text(encoding="utf-8")
    assert load_config(base).compress == CompressConfig(auto_lossless_mb=10, reduce_dpi=(300, 200, 150), jpeg_quality=80)

    # 古い config.yaml（compress セクションのないもの）でも読める。
    start = text.index("compress:")
    end = text.index("pii_detection:")
    old = tmp_path / "old.yaml"
    old.write_text(text[:start] + text[end:], encoding="utf-8")
    assert load_config(old).compress == CompressConfig()

    bad = tmp_path / "bad.yaml"
    bad.write_text(text.replace("reduce_dpi: [300, 200, 150]", "reduce_dpi: [20]"), encoding="utf-8")
    with pytest.raises(ValueError, match="reduce_dpi"):
        load_config(bad)
