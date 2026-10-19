from pathlib import Path

import pikepdf
from PIL import Image

from fixtures.generate_fixtures import make_a4_pdf, make_image_with_metadata
from mints_prep.processors.metadata_remover import (
    strip_image_metadata,
    strip_metadata,
    strip_pdf_metadata,
)


def test_strip_pdf_metadata_removes_author_and_title(tmp_path: Path):
    src = make_a4_pdf(tmp_path / "with_meta.pdf", with_metadata=True)
    with pikepdf.open(src) as pdf:
        assert "/Author" in pdf.docinfo.keys()

    dest = tmp_path / "stripped.pdf"
    strip_pdf_metadata(src, dest)

    with pikepdf.open(dest) as pdf:
        keys = [str(k) for k in pdf.docinfo.keys()] if pdf.docinfo else []
        assert "/Author" not in keys
        assert "/Title" not in keys

    # 元ファイルは変更されない
    with pikepdf.open(src) as pdf:
        assert "/Author" in pdf.docinfo.keys()


def test_strip_pdf_metadata_clears_xmp(tmp_path: Path):
    src = make_a4_pdf(tmp_path / "with_meta.pdf", with_metadata=True)
    dest = tmp_path / "stripped.pdf"
    strip_pdf_metadata(src, dest)

    with pikepdf.open(dest) as pdf:
        with pdf.open_metadata() as meta:
            xmp = dict(meta)
    # dc:title等の実質的なメタデータは残らない（Producer等の技術的な
    # 自動付与フィールドのみが再付与されるのは許容する）。
    assert "{http://purl.org/dc/elements/1.1/}title" not in xmp


def test_strip_image_metadata_removes_exif(tmp_path: Path):
    src = make_image_with_metadata(tmp_path / "with_exif.jpg")
    with Image.open(src) as img:
        assert len(img.getexif()) > 0

    dest = tmp_path / "stripped.jpg"
    strip_image_metadata(src, dest)

    with Image.open(dest) as img:
        assert len(img.getexif()) == 0

    # 元ファイルは変更されない
    with Image.open(src) as img:
        assert len(img.getexif()) > 0


def test_strip_image_metadata_preserves_pixel_data(tmp_path: Path):
    src = tmp_path / "colored.png"
    Image.new("RGB", (4, 4), color=(12, 34, 56)).save(src)

    dest = tmp_path / "stripped.png"
    strip_image_metadata(src, dest)

    with Image.open(dest) as img:
        assert img.getpixel((0, 0)) == (12, 34, 56)
        assert img.size == (4, 4)


def test_strip_metadata_dispatches_by_format(tmp_path: Path):
    pdf_src = make_a4_pdf(tmp_path / "a.pdf", with_metadata=True)
    dest = tmp_path / "a_out.pdf"
    strip_metadata(pdf_src, dest, "PDF")
    with pikepdf.open(dest) as pdf:
        assert "/Author" not in [str(k) for k in pdf.docinfo.keys()]


def test_strip_metadata_unknown_format_falls_back_to_copy(tmp_path: Path):
    src = tmp_path / "data.bin"
    src.write_bytes(b"raw bytes")
    dest = tmp_path / "data_out.bin"

    strip_metadata(src, dest, "UNKNOWN")

    assert dest.read_bytes() == b"raw bytes"


def test_strip_palette_image_preserves_pixel_colors(tmp_path: Path):
    """パレット形式（Pモード）画像の除去で、パレットを引き継がず出力が
    真っ黒になる不具合の回帰テスト（原本忠実性〔設計原則4〕）。"""
    from fixtures.generate_fixtures import make_palette_png

    src = make_palette_png(tmp_path / "palette.png")
    dest = tmp_path / "stripped.png"

    strip_image_metadata(src, dest)

    with Image.open(src) as a, Image.open(dest) as b:
        src_pixels = [a.convert("RGB").getpixel((x, 0)) for x in (0, 1)]
        dest_pixels = [b.convert("RGB").getpixel((x, 0)) for x in (0, 1)]
    assert dest_pixels == src_pixels


def test_strip_jpeg_is_lossless_no_reencode(tmp_path: Path):
    """JPEGのメタデータ除去が再エンコード（画質劣化）を伴わないことの回帰テスト。

    旧実装はピクセル複製→再保存（Pillow既定quality=75）で不可逆に再圧縮して
    いた（原本忠実性〔設計原則4〕違反）。現行のバイトレベルのセグメント除去
    では、デコード結果のピクセルが元とバイト単位で完全一致する。"""
    src = tmp_path / "photo.jpg"
    # 単色でない（再圧縮すれば必ず劣化が出る）グラデーション画像を高品質で保存
    img = Image.new("RGB", (64, 64))
    for x in range(64):
        for y in range(64):
            img.putpixel((x, y), (x * 4, y * 4, (x + y) * 2))
    exif = img.getexif()
    exif[271] = "TestCamera"
    img.save(src, "JPEG", quality=95, exif=exif)

    dest = tmp_path / "stripped.jpg"
    strip_image_metadata(src, dest)

    with Image.open(src) as a, Image.open(dest) as b:
        assert b.getexif().get(271) is None
        assert a.tobytes() == b.tobytes(), "デコード結果が一致しない＝再エンコードが発生している"


def test_strip_jpeg_removes_icc_profile_and_comment(tmp_path: Path):
    """ICCプロファイル（APP2）・コメント（COM）も除去され、検出側
    （metadata_detector）が「メタデータなし」に収束すること。"""
    from mints_prep.detectors.metadata_detector import detect_metadata

    src = tmp_path / "with_icc.jpg"
    Image.new("RGB", (8, 8), "white").save(
        src, "JPEG", icc_profile=b"fake-icc-profile-bytes", comment=b"secret comment"
    )

    dest = tmp_path / "stripped.jpg"
    strip_image_metadata(src, dest)

    finding = detect_metadata(dest)
    assert finding is not None
    assert finding.has_metadata is False, f"再検出されたフィールド: {finding.detected_fields}"


def test_strip_jpeg_malformed_file_raises_instead_of_degrading(tmp_path: Path):
    """壊れたJPEGは（黙って再エンコードにフォールバックせず）エラーにする。
    書き出しループ側が1件ごとのエラーダイアログでスキップ／中止を選ばせる。"""
    import pytest

    src = tmp_path / "broken.jpg"
    src.write_bytes(b"\xff\xd8\x00\x00garbage")
    dest = tmp_path / "out.jpg"

    with pytest.raises(ValueError, match="JPEG"):
        from mints_prep.processors.metadata_remover import _strip_jpeg_metadata_segments

        _strip_jpeg_metadata_segments(src, dest)


def test_strip_pdf_metadata_converges_on_rescan(tmp_path: Path):
    """PDFメタデータ除去後の出力を再検出しても「メタデータあり」に戻らない
    こと（pikepdfがProducer・MetadataDateを再挿入する不具合の回帰テスト。
    これが直っていないと「処理→再読み込み→A」の再検証ループが収束しない）。"""
    from mints_prep.detectors.metadata_detector import detect_metadata

    src = make_a4_pdf(tmp_path / "with_meta.pdf", with_metadata=True)
    dest = tmp_path / "stripped.pdf"

    strip_pdf_metadata(src, dest)

    finding = detect_metadata(dest)
    assert finding is not None
    assert finding.has_metadata is False, f"再検出されたフィールド: {finding.detected_fields}"
