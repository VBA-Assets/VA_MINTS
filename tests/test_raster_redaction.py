"""画像の上の黒塗りの下に残った文字（OCR の見えない文字など）の検査（Ver300。2026-11-17
班長指示「隠した所に OCR の文字が残っていないか」の検査を足す）。"""

from pathlib import Path

from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from mints_prep.detectors.redaction_detector import detect_redaction_residue

W, H = A4


def _scan_with_ocr(path: Path, black_over_name: bool, visible_text: bool = False) -> Path:
    """スキャンした紙（白い画像）に OCR の見えない文字を重ねた PDF。black_over_name なら、
    画像の上で名前の所を黒く塗ったもの（OCR の文字は残る）。"""
    image = Image.new("RGB", (int(W), int(H)), "white")
    draw = ImageDraw.Draw(image)
    draw.text((72, 100), "Contract", fill="black")
    if black_over_name:
        draw.rectangle((60, 180, 400, 215), fill="black")
    c = canvas.Canvas(str(path), pagesize=A4)
    c.drawImage(ImageReader(image), 0, 0, W, H)
    text = c.beginText(72, H - 205)
    text.setFont("Helvetica", 18)
    if not visible_text:
        text.setTextRenderMode(3)  # 見えない文字（OCR の文字と同じ）
    text.textLine("Taro Yamada secret")
    c.drawText(text)
    c.showPage()
    c.save()
    return path


def test_ocr_text_under_black_paint_on_the_image_is_found(tmp_path: Path):
    finding = detect_redaction_residue(_scan_with_ocr(tmp_path / "masked.pdf", black_over_name=True))
    assert finding is not None and finding.has_residual_text
    assert finding.pages_with_residual_text == (1,)
    assert any("黒く塗られた所の下" in d and "Taro" in d for d in finding.details)


def test_ocr_text_on_a_clean_scan_is_not_flagged(tmp_path: Path):
    finding = detect_redaction_residue(_scan_with_ocr(tmp_path / "clean.pdf", black_over_name=False))
    assert finding is not None and not finding.has_residual_text


def test_ordinary_black_text_and_mask_glyphs_are_not_flagged(tmp_path: Path):
    path = tmp_path / "plain.pdf"
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica-Bold", 24)
    c.drawString(72, 700, "Bold black text MMMM WWWW ....")
    c.showPage()
    c.save()
    finding = detect_redaction_residue(path)
    assert finding is not None and not finding.has_residual_text


def test_visible_white_text_on_black_band_is_not_flagged(tmp_path: Path):
    """黒い帯の上の白い文字（見出しなど。見えている）は、隠れた文字ではない。"""
    path = tmp_path / "band.pdf"
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFillColorRGB(0, 0, 0)
    c.rect(60, 690, 400, 40, fill=1, stroke=0)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(72, 702, "Section heading")
    c.showPage()
    c.save()
    finding = detect_redaction_residue(path)
    # 黒い矩形の上の文字は、従来の照合（塗りつぶし矩形の下の文字）で当たることがある。
    # 新しい検査（画像にして見る）は、白い字の画素があるので当たらない。
    assert not any("黒く塗られた所の下" in d for d in finding.details)
