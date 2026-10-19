"""向き補正（processors/orientation_fixer.py）のテスト。

/Rotate属性のみの変更で、ページの内容・寸法データは変わらないこと
（原本忠実性）と、補正後の再検出で向き不一致が解消することを確認する。
"""
from pathlib import Path

import pytest
from pypdf import PdfReader

from fixtures.generate_fixtures import make_a4_landscape_pdf, make_a4_pdf
from mints_prep.detectors.pdf_detector import detect_pdf
from mints_prep.processors.orientation_fixer import rotate_pdf_pages


def test_rotate_fixes_orientation_mismatch_on_rescan(tmp_path: Path, config):
    """A4横置き→90°回転→再検出で「縦・不一致なし」になる（再検証ループの収束）。"""
    src = make_a4_landscape_pdf(tmp_path / "甲001 横置き.pdf")
    before = detect_pdf(src, config.pdf)
    assert before.orientation_mismatch_pages == (1,)

    dest = tmp_path / "rotated.pdf"
    rotate_pdf_pages(src, dest, pages=(1,), degrees=90)

    after = detect_pdf(dest, config.pdf)
    assert after.page_size_judgments == ("A4",)
    assert after.page_orientations == ("縦",)
    assert after.orientation_mismatch_pages == ()


def test_rotate_does_not_modify_original_or_content(tmp_path: Path):
    """元ファイルは変更されず、出力の内容（テキスト）も保持される。"""
    src = make_a4_landscape_pdf(tmp_path / "甲001 横置き.pdf")
    original_bytes = src.read_bytes()

    dest = tmp_path / "rotated.pdf"
    rotate_pdf_pages(src, dest, pages=(1,), degrees=90)

    assert src.read_bytes() == original_bytes
    # /Rotate属性のみの変更のため、テキストレイヤーはそのまま読める
    # （フィクスチャの日本語部分はHelveticaのため抽出が保証されず、ASCII部分で確認する）。
    text = PdfReader(str(dest)).pages[0].extract_text()
    assert "VA mints" in text


def test_rotate_only_targets_specified_pages(tmp_path: Path):
    """指定ページ以外の/Rotateは変更しない。"""
    src = make_a4_pdf(tmp_path / "甲001 縦置き.pdf")
    dest = tmp_path / "rotated.pdf"

    rotate_pdf_pages(src, dest, pages=(), degrees=90)

    page = PdfReader(str(dest)).pages[0]
    assert (page.rotation or 0) % 360 == 0


def test_rotate_left_uses_270(tmp_path: Path):
    src = make_a4_landscape_pdf(tmp_path / "甲001 横置き.pdf")
    dest = tmp_path / "rotated.pdf"

    rotate_pdf_pages(src, dest, pages=(1,), degrees=270)

    page = PdfReader(str(dest)).pages[0]
    assert page.rotation % 360 == 270


def test_rotate_rejects_non_right_angle(tmp_path: Path):
    src = make_a4_landscape_pdf(tmp_path / "甲001 横置き.pdf")
    with pytest.raises(ValueError):
        rotate_pdf_pages(src, tmp_path / "out.pdf", pages=(1,), degrees=45)
