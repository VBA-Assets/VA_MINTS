from pathlib import Path

from fixtures.generate_fixtures import (
    make_a4_pdf,
    make_extension_mismatch_file,
    make_unsupported_file,
)
from mints_prep.detectors.format_detector import detect_format
from mints_prep.models import FormatStatus


def test_valid_pdf_is_record_ready(tmp_path: Path):
    p = make_a4_pdf(tmp_path / "test.pdf")
    status, detected = detect_format(p)
    assert status == FormatStatus.RECORD_READY
    assert detected == "PDF"


def test_docx_is_convertible(tmp_path: Path):
    p = tmp_path / "test.docx"
    from fixtures.generate_fixtures import make_docx_with_metadata

    make_docx_with_metadata(p)
    status, detected = detect_format(p)
    assert status == FormatStatus.CONVERTIBLE
    assert detected == "DOCX"


def test_unsupported_extension(tmp_path: Path):
    p = make_unsupported_file(tmp_path / "test.zip")
    status, _ = detect_format(p)
    assert status == FormatStatus.UNSUPPORTED


def test_extension_mismatch(tmp_path: Path):
    p = make_extension_mismatch_file(tmp_path / "fake.pdf")
    status, _ = detect_format(p)
    assert status == FormatStatus.EXTENSION_MISMATCH


def _write_iso_bmff(path: Path, brand: bytes) -> Path:
    # ISOベースメディアファイルの先頭（ftypボックス）を模す。
    path.write_bytes(b"\x00\x00\x00\x18ftyp" + brand + b"\x00\x00\x00\x00" + brand)
    return path


def test_heic_magic_with_mp4_extension_is_mismatch(tmp_path: Path):
    """HEIC判定が「ftyp→MP4」判定の陰で到達不能になっていた不具合の回帰テスト。
    .mp4拡張子のHEIC実体は記録対応（A分類候補）をすり抜けてはならない。"""
    p = _write_iso_bmff(tmp_path / "photo.mp4", b"heic")
    status, detected = detect_format(p)
    assert status == FormatStatus.EXTENSION_MISMATCH
    assert detected == "HEIC"


def test_heic_magic_with_heic_extension_is_convertible(tmp_path: Path):
    p = _write_iso_bmff(tmp_path / "photo.heic", b"heic")
    status, detected = detect_format(p)
    assert status == FormatStatus.CONVERTIBLE
    assert detected == "HEIC"


def test_mp4_brand_still_detected_as_mp4(tmp_path: Path):
    p = _write_iso_bmff(tmp_path / "movie.mp4", b"isom")
    status, detected = detect_format(p)
    assert status == FormatStatus.RECORD_READY
    assert detected == "MP4"


def test_mov_brand_with_mp4_extension_is_mismatch(tmp_path: Path):
    p = _write_iso_bmff(tmp_path / "movie.mp4", b"qt  ")
    status, detected = detect_format(p)
    assert status == FormatStatus.EXTENSION_MISMATCH
    assert detected == "MOV"


def test_mp3_frame_sync_with_crc_detected(tmp_path: Path):
    """MPEG1 Layer3・CRC付き（FF FA）のフレーム同期がD（対象外）に誤判定
    される偽陽性の回帰テスト。"""
    p = tmp_path / "audio.mp3"
    p.write_bytes(b"\xff\xfa\x90\x00" + b"\x00" * 32)
    status, detected = detect_format(p)
    assert status == FormatStatus.RECORD_READY
    assert detected == "MP3"
