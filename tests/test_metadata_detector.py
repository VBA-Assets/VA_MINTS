from pathlib import Path

from fixtures.generate_fixtures import (
    make_a4_pdf,
    make_a4_pdf_no_metadata,
    make_docx_with_metadata,
    make_image_with_metadata,
    make_image_without_metadata,
)
from mints_prep.detectors.metadata_detector import detect_metadata


def test_pdf_with_metadata_detected(tmp_path: Path):
    p = make_a4_pdf(tmp_path / "with_meta.pdf", with_metadata=True)
    finding = detect_metadata(p)
    assert finding is not None
    assert finding.has_metadata is True
    assert "Author" in finding.detected_fields


def test_pdf_without_metadata_detected(tmp_path: Path):
    p = make_a4_pdf_no_metadata(tmp_path / "no_meta.pdf")
    finding = detect_metadata(p)
    assert finding is not None
    assert finding.has_metadata is False


def test_image_with_exif_detected(tmp_path: Path):
    p = make_image_with_metadata(tmp_path / "photo.jpg")
    finding = detect_metadata(p)
    assert finding is not None
    assert finding.has_metadata is True
    assert "Make" in finding.detected_fields


def test_image_without_exif_detected(tmp_path: Path):
    p = make_image_without_metadata(tmp_path / "plain.png")
    finding = detect_metadata(p)
    assert finding is not None
    assert finding.has_metadata is False


def test_docx_with_metadata_detected(tmp_path: Path):
    p = make_docx_with_metadata(tmp_path / "doc.docx")
    finding = detect_metadata(p)
    assert finding is not None
    assert finding.has_metadata is True
    assert "author" in finding.detected_fields


def test_plain_jpeg_not_flagged_as_metadata(tmp_path: Path):
    """Exifのない素のJPEGはメタデータなしと判定される（PILがJFIFヘッダの
    構造情報〔jfif等〕をimg.infoに常に持つことによる全JPEG誤検出の回帰テスト）。"""
    from fixtures.generate_fixtures import make_plain_jpeg

    p = make_plain_jpeg(tmp_path / "plain.jpg")
    finding = detect_metadata(p)
    assert finding is not None
    assert finding.has_metadata is False, f"検出されたフィールド: {finding.detected_fields}"


def test_unsupported_av_container_returns_none_not_no_metadata(tmp_path: Path):
    """mutagenが解釈できないコンテナ（AVI等）を「メタデータなし」と誤報告
    しない回帰テスト：検出不可（None）を返し、呼び出し側が「検出未対応形式」
    と注記する（iPhoneのMOV等、GPS内蔵が一般的な形式を無警告で通さない）。"""
    p = tmp_path / "video.avi"
    p.write_bytes(b"RIFF\x24\x00\x00\x00AVI LIST" + b"\x00" * 32)
    finding = detect_metadata(p)
    assert finding is None


def test_mp3_id3_tags_detected_via_raw_tags(tmp_path: Path):
    """MP3のID3タグが生タグAPI（easy=False）経由で検出されること
    （EasyタグAPIのマッピング済みキー限定による見逃しの回帰テスト）。"""
    from mutagen.id3 import ID3, TIT2

    p = tmp_path / "audio.mp3"
    # 有効なMPEG1 Layer3フレーム（128kbps・44.1kHz＝417バイト）×4＋ID3タグ
    frame = b"\xff\xfb\x90\x64" + b"\x00" * 413
    p.write_bytes(frame * 4)
    tags = ID3()
    tags.add(TIT2(encoding=3, text=["秘密のタイトル"]))
    tags.save(p)

    finding = detect_metadata(p)
    assert finding is not None
    assert finding.has_metadata is True
    assert any("TIT2" in f for f in finding.detected_fields)


def test_palette_png_transparency_not_flagged_as_metadata(tmp_path: Path):
    """透過付きパレットPNGのtransparency（画像内容の一部）をメタデータ扱い
    しない（除去処理が透過を維持するため、検出し続けると収束しない）。"""
    from PIL import Image as _Image

    p = tmp_path / "transparent.png"
    img = _Image.new("P", (2, 2))
    img.putpalette([0, 0, 0, 255, 255, 255] + [0] * (256 * 3 - 6))
    img.save(p, transparency=0)
    finding = detect_metadata(p)
    assert finding is not None
    assert finding.has_metadata is False, f"検出されたフィールド: {finding.detected_fields}"
