"""圧縮（Ver300。要件定義書 4.13）。

① 画質を損なわない圧縮（PDF）：compress_pdf_lossless
   PDF の中の使っていない資源の除去・データの詰め直し（オブジェクトストリーム化・
   可逆圧縮のかけ直し）。ページの見た目・文字（テキストレイヤー）・画像のデータは
   変わらない。小さくならなかったときは、元のファイルをそのまま写す。

② 画像を軽くする（PDF の中の画像、JPEG・PNG のファイル）：reduce_pdf_images・reduce_image_file
   解像度を下げ、JPEG にするものは画質（quality）を下げる。細かい字や写真が粗くなる
   ことがあるので、利用者が選んだときだけ行う（自動ではチェックを付けない）。
   - PDF の中の画像の解像度は、その画像を置いたページの大きさ（いちばん大きいページ）
     に引き伸ばしたとみなして見積もる。実際にはページより小さく置かれていることが
     多いので、実際の解像度は見積もり以上になる（下げすぎない側）。
   - 目標の解像度を少し（_MARGIN）超える画像だけを縮める。白黒（1ビット）・
     色の表（インデックス）・CMYK・マスク付き・特殊なデコードの画像は、色や形が
     変わるおそれがあるので触れない。縮めた結果が元より大きければ、元のまま残す。
   - JPEG・PNG のファイルは、A4 に収めて置いたときの解像度で見積もる。PNG は PNG の
     まま（可逆）、JPEG は JPEG で保存し直す。写真の向き（EXIF の Orientation）は
     画素に反映し、ほかの EXIF・色の情報（ICC）は残す（メタデータ除去は別の処理）。

いずれも、ファイルの内容を変える処理である。呼び出し側は、必ず［ファイルを更新］の
確認を経てから実行すること（CLAUDE.md 設計原則3）。外部との通信はしない。
"""

from __future__ import annotations

import io
import shutil
from dataclasses import dataclass
from pathlib import Path

import pikepdf
from PIL import Image, ImageOps

# 目標の解像度をこの比率より超える画像だけを縮める（ほとんど変わらない画像を、
# 画質を落としてまで作り直さないため）。
_MARGIN = 1.05
# A4（インチ。短辺・長辺）。JPEG・PNG のファイルの解像度の見積もりに使う。
_A4_INCHES = (210 / 25.4, 297 / 25.4)


@dataclass(frozen=True)
class ReduceResult:
    images: int = 0  # 縮めた画像の数
    skipped: int = 0  # 触れなかった画像（白黒・CMYK・マスク付きなど）の数


def compress_pdf_lossless(src: Path, dst: Path) -> None:
    """① 画質を損なわない圧縮。小さくならなければ、src をそのまま dst に写す。"""
    tmp = dst.with_name(dst.name + ".lossless_tmp")
    try:
        with pikepdf.open(src) as pdf:
            pdf.remove_unreferenced_resources()
            pdf.save(
                tmp,
                compress_streams=True,
                object_stream_mode=pikepdf.ObjectStreamMode.generate,
                stream_decode_level=pikepdf.StreamDecodeLevel.generalized,
                recompress_flate=True,
            )
        if tmp.stat().st_size < src.stat().st_size:
            tmp.replace(dst)
        else:
            shutil.copyfile(src, dst)
    finally:
        tmp.unlink(missing_ok=True)


def _page_inches(page: pikepdf.Page) -> tuple[float, float]:
    box = page.mediabox
    w = abs(float(box[2]) - float(box[0])) / 72
    h = abs(float(box[3]) - float(box[1])) / 72
    return max(w, 0.1), max(h, 0.1)


def _reducible(image: pikepdf.Object) -> bool:
    """縮めてよい画像か（色・形が変わるおそれのあるものは触れない）。"""
    if image.get("/ImageMask", False) or "/SMask" in image or "/Mask" in image:
        return False
    if "/Decode" in image or int(image.get("/BitsPerComponent", 8)) != 8:
        return False
    space = image.get("/ColorSpace")
    if space in (pikepdf.Name.DeviceRGB, pikepdf.Name.DeviceGray):
        return True
    if isinstance(space, pikepdf.Array) and len(space) == 2 and space[0] == pikepdf.Name.ICCBased:
        return int(space[1].get("/N", 0)) in (1, 3)
    return False


def reduce_pdf_images(src: Path, dst: Path, dpi: int, jpeg_quality: int) -> ReduceResult:
    """② PDF の中の画像を、dpi（見積もり）まで縮め、JPEG（jpeg_quality）にする。"""
    reduced = skipped = 0
    with pikepdf.open(src) as pdf:
        # 画像（オブジェクト）ごとに、置かれたページのうち最も大きいページでの解像度を見積もる。
        found: dict[tuple[int, int], tuple[pikepdf.Object, float]] = {}
        for page in pdf.pages:
            inches = _page_inches(page)
            resources = page.obj.get("/Resources")
            xobjects = resources.get("/XObject") if resources is not None else None
            if xobjects is None:
                continue
            for _name, obj in xobjects.items():
                if not isinstance(obj, pikepdf.Stream) or obj.get("/Subtype") != pikepdf.Name.Image:
                    continue
                width, height = int(obj.get("/Width", 0)), int(obj.get("/Height", 0))
                if width <= 0 or height <= 0:
                    continue
                # 画像の向きが分からないので、ページの長辺・短辺に、画像の長辺・短辺を当てる。
                est = min(max(width, height) / max(inches), min(width, height) / min(inches))
                key = obj.objgen
                if key not in found or est < found[key][1]:
                    found[key] = (obj, est)
        for obj, est in found.values():
            if est <= dpi * _MARGIN:
                continue
            if not _reducible(obj):
                skipped += 1
                continue
            try:
                image = pikepdf.PdfImage(obj).as_pil_image()
            except Exception:  # noqa: BLE001 - 読めない画像は触れない
                skipped += 1
                continue
            if image.mode not in ("RGB", "L"):
                skipped += 1
                continue
            scale = dpi / est
            size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
            small = image.resize(size, Image.LANCZOS)
            buf = io.BytesIO()
            small.save(buf, format="JPEG", quality=jpeg_quality, optimize=True)
            data = buf.getvalue()
            if len(data) >= len(obj.read_raw_bytes()):
                continue  # 小さくならない：元のまま
            obj.write(data, filter=pikepdf.Name.DCTDecode)
            obj.Width, obj.Height = size
            obj.BitsPerComponent = 8
            if "/DecodeParms" in obj:
                del obj.DecodeParms
            reduced += 1
        pdf.save(dst)
    return ReduceResult(images=reduced, skipped=skipped)


def reduce_image_file(src: Path, dst: Path, fmt: str, dpi: int, jpeg_quality: int) -> bool:
    """② JPEG・PNG のファイルを、A4 に収めて置いたときの解像度が dpi になるまで縮める。
    縮めたら True。縮める必要がない・小さくならないときは、src をそのまま dst に写して False。"""
    with Image.open(src) as opened:
        opened.load()
        info = dict(opened.info)
        exif = opened.getexif()
        image = ImageOps.exif_transpose(opened)
    long_px, short_px = max(image.size), min(image.size)
    est = min(long_px / _A4_INCHES[1], short_px / _A4_INCHES[0])
    if est <= dpi * _MARGIN:
        shutil.copyfile(src, dst)
        return False
    scale = dpi / est
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    small = image.resize(size, Image.LANCZOS)
    buf = io.BytesIO()
    if fmt == "JPEG":
        if small.mode not in ("RGB", "L"):
            small = small.convert("RGB")
        kwargs = {"quality": jpeg_quality, "optimize": True}
        if exif:
            exif[0x0112] = 1  # 向きは画素に反映した
            kwargs["exif"] = exif.tobytes()
        if info.get("icc_profile"):
            kwargs["icc_profile"] = info["icc_profile"]
        small.save(buf, format="JPEG", **kwargs)
    else:
        from PIL import PngImagePlugin

        meta = PngImagePlugin.PngInfo()
        for key, value in (getattr(image, "text", None) or {}).items():
            meta.add_text(key, value)
        kwargs = {"optimize": True, "pnginfo": meta}
        if info.get("icc_profile"):
            kwargs["icc_profile"] = info["icc_profile"]
        if info.get("dpi"):
            kwargs["dpi"] = info["dpi"]
        small.save(buf, format="PNG", **kwargs)
    data = buf.getvalue()
    if len(data) >= src.stat().st_size:
        shutil.copyfile(src, dst)
        return False
    dst.write_bytes(data)
    return True


def compressed_size(
    src: Path, fmt: str, lossless: bool, dpi: int | None, jpeg_quality: int, work: Path
) -> int:
    """src に、選んだ圧縮だけを行った場合の大きさ（バイト）。work は作業用のフォルダ。"""
    current = src
    made: list[Path] = []
    try:
        if dpi is not None:
            out = work / f"reduced{src.suffix}"
            if fmt == "PDF":
                reduce_pdf_images(current, out, dpi, jpeg_quality)
            else:
                reduce_image_file(current, out, fmt, dpi, jpeg_quality)
            made.append(out)
            current = out
        if lossless and fmt == "PDF":
            out = work / "lossless.pdf"
            compress_pdf_lossless(current, out)
            made.append(out)
            current = out
        return current.stat().st_size
    finally:
        for p in made:
            p.unlink(missing_ok=True)
