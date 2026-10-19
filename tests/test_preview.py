from pathlib import Path

from PIL import Image
from pypdf import PdfReader

from fixtures.generate_fixtures import (
    make_a4_pdf,
    make_image_without_metadata,
    make_multipage_pdf,
    make_password_pdf,
)
from mints_prep.config import EvidenceStampConfig
from mints_prep.models import Classification, FileRecord, FilenameFinding, FormatStatus, PdfFinding
from mints_prep.ui.preview import get_page_count, render_preview, render_preview_with_stamp

_STAMP_CONFIG = EvidenceStampConfig(font_size_pt=14, margin_top_mm=8, margin_right_mm=8)


def _record(path: Path, detected_format: str, pdf_finding: PdfFinding | None = None) -> FileRecord:
    return FileRecord(
        path=path,
        size_bytes=100,
        format_status=FormatStatus.RECORD_READY,
        detected_format=detected_format,
        classification=Classification.A_COMPLIANT,
        filename_finding=FilenameFinding(
            length=len(path.name), exceeds_max_length=False, exceeds_warning_length=False
        ),
        pdf_finding=pdf_finding,
    )


def test_render_preview_returns_image_for_pdf(tmp_path: Path):
    path = make_a4_pdf(tmp_path / "甲001.pdf")

    image = render_preview(_record(path, "PDF"))

    assert isinstance(image, Image.Image)


def test_render_preview_returns_none_for_password_protected_pdf(tmp_path: Path):
    path = make_password_pdf(tmp_path / "甲002.pdf")

    image = render_preview(_record(path, "PDF", pdf_finding=PdfFinding(has_password=True)))

    assert image is None


def test_render_preview_returns_image_for_png(tmp_path: Path):
    path = make_image_without_metadata(tmp_path / "甲003.png")

    image = render_preview(_record(path, "PNG"))

    assert isinstance(image, Image.Image)


def test_render_preview_returns_none_for_audio_video(tmp_path: Path):
    path = tmp_path / "甲004.mp3"
    path.write_bytes(b"fake mp3 content")

    image = render_preview(_record(path, "MP3"))

    assert image is None


def test_render_preview_returns_none_for_missing_file(tmp_path: Path):
    path = tmp_path / "does_not_exist.pdf"

    image = render_preview(_record(path, "PDF"))

    assert image is None


def test_render_preview_respects_max_dimensions(tmp_path: Path):
    path = make_a4_pdf(tmp_path / "甲005.pdf")

    image = render_preview(_record(path, "PDF"), max_width=200, max_height=200)

    assert image.width <= 200
    assert image.height <= 200


def test_render_preview_with_stamp_shows_label_on_pdf(tmp_path: Path):
    path = make_a4_pdf(tmp_path / "甲006 契約書.pdf")

    image = render_preview_with_stamp(_record(path, "PDF"), "甲006", _STAMP_CONFIG)

    assert isinstance(image, Image.Image)


def test_render_preview_with_stamp_does_not_modify_original_pdf(tmp_path: Path):
    path = make_a4_pdf(tmp_path / "甲007 契約書.pdf")
    original_page_count = len(PdfReader(str(path)).pages)
    original_text = PdfReader(str(path)).pages[0].extract_text()

    render_preview_with_stamp(_record(path, "PDF"), "甲007", _STAMP_CONFIG)

    assert len(PdfReader(str(path)).pages) == original_page_count
    assert "甲007" not in original_text
    assert "甲007" not in PdfReader(str(path)).pages[0].extract_text()


def test_render_preview_with_stamp_returns_none_for_password_protected_pdf(tmp_path: Path):
    path = make_password_pdf(tmp_path / "甲008.pdf")

    image = render_preview_with_stamp(
        _record(path, "PDF", pdf_finding=PdfFinding(has_password=True)), "甲008", _STAMP_CONFIG
    )

    assert image is None


def test_render_preview_with_stamp_returns_image_for_png(tmp_path: Path):
    path = make_image_without_metadata(tmp_path / "甲009.png")

    image = render_preview_with_stamp(_record(path, "PNG"), "甲009", _STAMP_CONFIG)

    assert isinstance(image, Image.Image)


def test_render_preview_with_stamp_differs_from_plain_preview(tmp_path: Path):
    path = make_a4_pdf(tmp_path / "甲010 契約書.pdf")
    record = _record(path, "PDF")

    plain = render_preview(record)
    stamped = render_preview_with_stamp(record, "甲010", _STAMP_CONFIG)

    assert plain.tobytes() != stamped.tobytes()


def test_get_page_count_returns_page_count_for_multipage_pdf(tmp_path: Path):
    path = make_multipage_pdf(tmp_path / "甲011.pdf", page_count=3)

    assert get_page_count(_record(path, "PDF")) == 3


def test_get_page_count_returns_one_for_image(tmp_path: Path):
    path = make_image_without_metadata(tmp_path / "甲012.png")

    assert get_page_count(_record(path, "PNG")) == 1


def test_get_page_count_returns_zero_for_password_protected_pdf(tmp_path: Path):
    path = make_password_pdf(tmp_path / "甲013.pdf")

    count = get_page_count(_record(path, "PDF", pdf_finding=PdfFinding(has_password=True)))

    assert count == 0


def test_get_page_count_returns_zero_for_audio_video(tmp_path: Path):
    path = tmp_path / "甲014.mp3"
    path.write_bytes(b"fake mp3 content")

    assert get_page_count(_record(path, "MP3")) == 0


def test_render_preview_can_show_pages_other_than_first(tmp_path: Path):
    path = make_multipage_pdf(tmp_path / "甲015.pdf", page_count=3)
    record = _record(path, "PDF")

    page1 = render_preview(record, page_index=0)
    page2 = render_preview(record, page_index=1)

    assert isinstance(page1, Image.Image)
    assert isinstance(page2, Image.Image)
    assert page1.tobytes() != page2.tobytes()


def test_render_preview_returns_none_for_out_of_range_page(tmp_path: Path):
    path = make_multipage_pdf(tmp_path / "甲016.pdf", page_count=2)

    image = render_preview(_record(path, "PDF"), page_index=5)

    assert image is None


def test_render_preview_with_stamp_can_show_pages_other_than_first(tmp_path: Path):
    path = make_multipage_pdf(tmp_path / "甲017.pdf", page_count=3)
    record = _record(path, "PDF")

    page1 = render_preview_with_stamp(record, "甲017", _STAMP_CONFIG, page_index=0)
    page2 = render_preview_with_stamp(record, "甲017", _STAMP_CONFIG, page_index=1)

    assert isinstance(page1, Image.Image)
    assert isinstance(page2, Image.Image)
    assert page1.tobytes() != page2.tobytes()


def test_render_preview_with_stamp_returns_none_for_out_of_range_page(tmp_path: Path):
    path = make_multipage_pdf(tmp_path / "甲018.pdf", page_count=2)

    image = render_preview_with_stamp(_record(path, "PDF"), "甲018", _STAMP_CONFIG, page_index=5)

    assert image is None
