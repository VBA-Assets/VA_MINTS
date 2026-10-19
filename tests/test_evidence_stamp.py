from pathlib import Path

import pdfplumber
from pypdf import PdfReader
from PIL import Image

from fixtures.generate_fixtures import make_a4_pdf, make_image_without_metadata
from mints_prep.config import EvidenceStampConfig
from mints_prep.processors.evidence_stamp import stamp_image, stamp_pdf

_CONFIG = EvidenceStampConfig(font_size_pt=10, margin_top_mm=8, margin_right_mm=8)


def _top_right_pixel_colors(image: Image.Image, box_size: int = 160) -> set[tuple[int, int, int]]:
    """右上コーナー付近のピクセル色の集合を返す（スタンプの色検証用）。"""
    region = image.convert("RGB").crop((image.width - box_size, 0, image.width, box_size // 2))
    return {color for _, color in region.getcolors(maxcolors=1_000_000)}


def test_stamp_pdf_adds_label_text_without_changing_page_count(tmp_path: Path):
    src = make_a4_pdf(tmp_path / "甲001 契約書.pdf")
    dest = tmp_path / "out.pdf"

    stamp_pdf(src, dest, "甲001", _CONFIG)

    original_pages = len(PdfReader(str(src)).pages)
    stamped = PdfReader(str(dest))
    assert len(stamped.pages) == original_pages
    assert "甲001" in stamped.pages[0].extract_text()


def test_stamp_pdf_preserves_multi_page_documents(tmp_path: Path):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    src = tmp_path / "multi.pdf"
    c = canvas.Canvas(str(src), pagesize=A4)
    for _ in range(3):
        c.drawString(72, 700, "page")
        c.showPage()
    c.save()
    dest = tmp_path / "out.pdf"

    stamp_pdf(src, dest, "乙A001", _CONFIG)

    stamped = PdfReader(str(dest))
    assert len(stamped.pages) == 3
    for page in stamped.pages:
        assert "乙A001" in page.extract_text()


def test_stamp_image_preserves_dimensions_and_format(tmp_path: Path):
    src = make_image_without_metadata(tmp_path / "甲002 写真.png")
    dest = tmp_path / "out.png"
    original = Image.open(src)
    original_size = original.size

    stamp_image(src, dest, "甲002", _CONFIG)

    stamped = Image.open(dest)
    assert stamped.size == original_size
    assert stamped.format == "PNG"


def test_stamp_image_actually_modifies_pixel_data(tmp_path: Path):
    src = make_image_without_metadata(tmp_path / "甲003.png")
    dest = tmp_path / "out.png"

    stamp_image(src, dest, "甲003", _CONFIG)

    assert Path(dest).read_bytes() != Path(src).read_bytes()


def test_stamp_pdf_black_style_draws_black_text(tmp_path: Path):
    config = EvidenceStampConfig(font_size_pt=14, margin_top_mm=8, margin_right_mm=8, style="black")
    src = make_a4_pdf(tmp_path / "src.pdf")
    dest = tmp_path / "out.pdf"

    stamp_pdf(src, dest, "甲001", config)

    with pdfplumber.open(dest) as pdf:
        image = pdf.pages[0].to_image(resolution=150).original
    colors = _top_right_pixel_colors(image)
    assert (0, 0, 0) in colors
    assert (255, 0, 0) not in colors


def test_stamp_pdf_red_style_draws_red_text(tmp_path: Path):
    config = EvidenceStampConfig(font_size_pt=14, margin_top_mm=8, margin_right_mm=8, style="red")
    src = make_a4_pdf(tmp_path / "src.pdf")
    dest = tmp_path / "out.pdf"

    stamp_pdf(src, dest, "甲001", config)

    with pdfplumber.open(dest) as pdf:
        image = pdf.pages[0].to_image(resolution=150).original
    colors = _top_right_pixel_colors(image)
    assert (255, 0, 0) in colors
    assert (0, 0, 0) not in colors


def test_stamp_pdf_red_boxed_style_draws_more_red_pixels_than_plain_red(tmp_path: Path):
    # 枠線の分だけ赤ピクセル数が増えることを、囲みが実際に描画された根拠とする。
    plain_config = EvidenceStampConfig(font_size_pt=14, margin_top_mm=8, margin_right_mm=8, style="red")
    boxed_config = EvidenceStampConfig(
        font_size_pt=14, margin_top_mm=8, margin_right_mm=8, style="red_boxed"
    )

    def _red_pixel_count(config: EvidenceStampConfig) -> int:
        src = make_a4_pdf(tmp_path / f"src_{config.style}.pdf")
        dest = tmp_path / f"out_{config.style}.pdf"
        stamp_pdf(src, dest, "甲001", config)
        with pdfplumber.open(dest) as pdf:
            image = pdf.pages[0].to_image(resolution=150).original
        region = image.convert("RGB").crop((image.width - 160, 0, image.width, 80))
        return sum(
            count for count, color in region.getcolors(maxcolors=1_000_000) if color == (255, 0, 0)
        )

    assert _red_pixel_count(boxed_config) > _red_pixel_count(plain_config)


def test_stamp_image_red_style_draws_red_text(tmp_path: Path):
    config = EvidenceStampConfig(font_size_pt=14, margin_top_mm=8, margin_right_mm=8, style="red")
    src = tmp_path / "src.png"
    Image.new("RGB", (400, 300), "white").save(src)
    dest = tmp_path / "out.png"

    stamp_image(src, dest, "甲001", config)

    colors = _top_right_pixel_colors(Image.open(dest))
    assert (255, 0, 0) in colors
    assert (0, 0, 0) not in colors


def test_stamp_image_red_boxed_style_draws_more_red_pixels_than_plain_red(tmp_path: Path):
    plain_config = EvidenceStampConfig(font_size_pt=14, margin_top_mm=8, margin_right_mm=8, style="red")
    boxed_config = EvidenceStampConfig(
        font_size_pt=14, margin_top_mm=8, margin_right_mm=8, style="red_boxed"
    )

    def _red_pixel_count(config: EvidenceStampConfig) -> int:
        src = tmp_path / f"src_img_{config.style}.png"
        Image.new("RGB", (400, 300), "white").save(src)
        dest = tmp_path / f"out_img_{config.style}.png"
        stamp_image(src, dest, "甲001", config)
        region = Image.open(dest).convert("RGB").crop((400 - 160, 0, 400, 80))
        return sum(
            count for count, color in region.getcolors(maxcolors=1_000_000) if color == (255, 0, 0)
        )

    assert _red_pixel_count(boxed_config) > _red_pixel_count(plain_config)


def test_evidence_stamp_config_rejects_invalid_style():
    import pytest

    with pytest.raises(ValueError):
        EvidenceStampConfig(font_size_pt=10, margin_top_mm=8, margin_right_mm=8, style="blue")
