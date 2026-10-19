from pathlib import Path

from fixtures.generate_fixtures import (
    make_a4_pdf_no_metadata,
    make_password_pdf,
    make_pdf_with_black_box_redaction_failure,
    make_pdf_with_highlight_redaction_failure,
    make_pdf_with_proper_redaction,
)
from mints_prep.detectors.redaction_detector import detect_redaction_residue


def test_black_box_redaction_failure_detected(tmp_path: Path):
    p = make_pdf_with_black_box_redaction_failure(tmp_path / "black_box.pdf")
    finding = detect_redaction_residue(p)
    assert finding is not None
    assert finding.has_residual_text is True
    assert finding.pages_with_residual_text == (1,)
    assert any("SECRET" in d for d in finding.details)


def test_highlight_redaction_failure_detected(tmp_path: Path):
    p = make_pdf_with_highlight_redaction_failure(tmp_path / "highlight.pdf")
    finding = detect_redaction_residue(p)
    assert finding is not None
    assert finding.has_residual_text is True
    assert any("マーカー" in d for d in finding.details)


def test_proper_redaction_has_no_residual_text(tmp_path: Path):
    p = make_pdf_with_proper_redaction(tmp_path / "proper.pdf")
    finding = detect_redaction_residue(p)
    assert finding is not None
    assert finding.has_residual_text is False
    assert finding.pages_with_residual_text == ()


def test_plain_pdf_without_covering_objects_has_no_residual_text(tmp_path: Path):
    p = make_a4_pdf_no_metadata(tmp_path / "plain.pdf")
    finding = detect_redaction_residue(p)
    assert finding is not None
    assert finding.has_residual_text is False


def test_password_protected_pdf_returns_none(tmp_path: Path):
    p = make_password_pdf(tmp_path / "secret.pdf")
    finding = detect_redaction_residue(p)
    assert finding is None


def test_light_fill_rect_under_text_not_flagged(tmp_path: Path):
    """表セルの網掛け（薄灰）の上に文字を描く、ごく普通の文書装飾を
    「墨消し下に文字残存」と誤検出しない（暗色判定の回帰テスト）。"""
    from fixtures.generate_fixtures import make_pdf_with_light_fill_rect_under_text

    p = make_pdf_with_light_fill_rect_under_text(tmp_path / "shaded_header.pdf")
    finding = detect_redaction_residue(p)
    assert finding is not None
    assert finding.has_residual_text is False, f"誤検出: {finding.details}"
