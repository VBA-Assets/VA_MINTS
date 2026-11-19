"""画像（JPEG・PNG）を、PDF の1ページにする（Ver300。要件定義書 4.12）。

ページの挿入・ファイルの結合の材料にするときだけ使う（画像を単独のファイルとして
提出する場合は、これまでどおり変換しない）。

- ページは A4 で、画像の縦横に合わせて縦置き又は横置きにする。
- 画素は作り直さない。JPEG は再圧縮せず、そのままの圧縮データを埋め込む（画質が
  落ちない）。PNG は、画質を損なわない形（Flate）で埋め込む。
- 表示の大きさは、画像の解像度（dpi。記録がなければ 96dpi）どおりとし、ページ
  （余白 10mm を除く）に収まらないときだけ、収まるように縮めて表示する（拡大はしない）。
  表示の大きさを変えても、画素はそのまま。
- 画像の向き（EXIF の Orientation）は反映する。JPEG は、圧縮データはそのままで、
  ページの上で回して描く（左右反転を含む向きのときだけ、画素を並べ替えて PNG と
  同じ形で埋め込む。画質は落ちない）。
- JPEG に付いている EXIF（撮影日時・撮影場所〔GPS〕など）・XMP・コメントは、
  埋め込む前に取り除く（圧縮データには触れない）。色の情報（ICC プロファイル・
  Adobe の色変換の印）は、見た目が変わらないように残す。

完全ローカル動作（設計原則1）：reportlab・Pillow だけを使い、外部通信はしない。
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageOps
from reportlab import rl_config
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
_MARGIN = 10 * mm
_DEFAULT_DPI = 96.0
# 取り除く JPEG のセグメント：APP1（EXIF・XMP）、APP3〜APP13・APP15、COM（コメント）。
# APP0（JFIF）・APP2（ICC プロファイル）・APP14（Adobe の色変換の印）は、見た目に関わるので残す。
_KEEP_APP = {0xE0, 0xE2, 0xEE}
# Orientation → ページの上で回す角度（反時計回り。reportlab の rotate と同じ向き）。
_JPEG_ROTATION = {1: 0, 3: 180, 6: -90, 8: 90}


def strip_jpeg_metadata(data: bytes) -> bytes:
    """JPEG のデータから、EXIF・XMP・コメントなどのセグメントを取り除く（圧縮データは
    そのまま）。JPEG として読めない形なら ValueError。"""
    if data[:2] != b"\xff\xd8":
        raise ValueError("JPEG ではありません。")
    out = bytearray(b"\xff\xd8")
    i = 2
    while i < len(data):
        if data[i] != 0xFF:
            raise ValueError("JPEG の形が崩れています。")
        while i < len(data) and data[i] == 0xFF:  # 詰め物の 0xFF
            i += 1
        if i >= len(data):
            break
        marker = data[i]
        start = i - 1
        i += 1
        if marker == 0xD9:  # EOI
            out += b"\xff\xd9"
            break
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:  # 長さのないマーカー
            out += bytes((0xFF, marker))
            continue
        if i + 2 > len(data):
            raise ValueError("JPEG の形が崩れています。")
        length = int.from_bytes(data[i:i + 2], "big")
        end = i + length
        if marker == 0xDA:  # SOS：ここから後は圧縮データ。最後まで、そのまま写す
            out += data[start:]
            break
        drop = (0xE1 <= marker <= 0xEF and marker not in _KEEP_APP) or marker == 0xFE
        if not drop:
            out += data[start:end]
        i = end
    return bytes(out)


def _orientation(image: Image.Image) -> int:
    try:
        return int(image.getexif().get(0x0112, 1) or 1)
    except Exception:  # noqa: BLE001 - 読めなければ向きの記録なしとみなす
        return 1


def _display_size(pixels: tuple[int, int], dpi, area: tuple[float, float]) -> tuple[float, float]:
    """表示の大きさ（ポイント）：解像度どおり。ページに収まらなければ縮める。"""
    try:
        dx, dy = (float(dpi[0]), float(dpi[1])) if dpi else (_DEFAULT_DPI, _DEFAULT_DPI)
    except (TypeError, ValueError, IndexError):
        dx = dy = _DEFAULT_DPI
    if not dx or dx < 1 or not dy or dy < 1:
        dx = dy = _DEFAULT_DPI
    w, h = pixels[0] * 72.0 / dx, pixels[1] * 72.0 / dy
    scale = min(1.0, area[0] / w, area[1] / h)
    return w * scale, h * scale


def image_to_pdf(src: Path, dst: Path) -> Path:
    """画像 src を、A4 の1ページの PDF にして dst に書く。読めない画像は ValueError。"""
    try:
        with Image.open(src) as probe:
            fmt = probe.format
            pixels = probe.size
            dpi = probe.info.get("dpi")
            orientation = _orientation(probe)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"画像を読めません：{src.name}") from exc
    if fmt not in ("JPEG", "PNG"):
        raise ValueError(f"JPEG・PNG 以外の画像は使えません：{src.name}")

    raw_jpeg = fmt == "JPEG" and orientation in _JPEG_ROTATION
    if raw_jpeg:
        angle = _JPEG_ROTATION[orientation]
        upright = (pixels[1], pixels[0]) if angle in (90, -90) else pixels
        source = ImageReader(io.BytesIO(strip_jpeg_metadata(src.read_bytes())))
    else:
        with Image.open(src) as im:
            im.load()
            fixed = ImageOps.exif_transpose(im)
            if fixed.mode not in ("RGB", "RGBA", "L", "LA"):
                fixed = fixed.convert("RGBA" if "A" in fixed.getbands() else "RGB")
            # Flate（画質を損なわない形）で埋め込むため、PNG にしてから渡す。EXIF は付けない。
            buf = io.BytesIO()
            fixed.save(buf, format="PNG")
        angle = 0
        upright = fixed.size
        source = ImageReader(io.BytesIO(buf.getvalue()))

    page = landscape(A4) if upright[0] > upright[1] else A4
    area = (page[0] - 2 * _MARGIN, page[1] - 2 * _MARGIN)
    if angle in (90, -90) and dpi:
        dpi = (dpi[1], dpi[0])
    shown_w, shown_h = _display_size(upright, dpi, area)

    # 画像のデータを ASCII85 で包まない（包んでも画質は変わらないが、大きさが 25% 増える）。
    saved_a85 = rl_config.useA85
    rl_config.useA85 = 0
    try:
        c = canvas.Canvas(str(dst), pagesize=page, invariant=1)
        c.translate(page[0] / 2, page[1] / 2)
        c.rotate(angle)
        # 回す前の座標で描く（90°回すときは、幅と高さが入れ替わる）。
        w, h = (shown_h, shown_w) if angle in (90, -90) else (shown_w, shown_h)
        c.drawImage(source, -w / 2, -h / 2, width=w, height=h, mask="auto" if not raw_jpeg else None)
        c.showPage()
        c.save()
    finally:
        rl_config.useA85 = saved_a85
    return dst
