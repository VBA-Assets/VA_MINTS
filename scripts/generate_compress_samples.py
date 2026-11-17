"""圧縮（Ver300。要件定義書 4.13）を、手で確かめるためのサンプル（samples_compress/）を作るスクリプト。

実行方法：
    .venv\\Scripts\\python.exe scripts\\generate_compress_samples.py

できるもの（いずれも本スクリプトが作るダミーデータで、実際の証拠の内容は含まない）：
    samples_compress\\証拠\\
        甲050 スキャンした契約書.pdf   300dpi のカラーのスキャンに似せた 4 ページ（約 12MB）。
                                        大きいので「画質を損なわない圧縮」に自動でチェックが付く。
                                        「画像を軽くする」を試す（字が読めるかをプレビューで見る）。
                                        300dpi（既定）では縮まないので、200dpi・150dpi を選ぶ
        甲051 スマホの写真.jpg         4000×3000 の写真（撮影時の向きの情報付き）
        甲052 画面の写し.png           2480×3508 の PNG
        甲053 文字だけの書面.pdf       文字だけの小さな PDF（圧縮しても、ほとんど変わらない）

各ページ・画像には、細かい字（6〜10ポイント相当）を描いてあり、解像度を下げたときに
読めるかを確かめられる。メタデータは除去しておく（圧縮以外の項目を出さない）。
"""

from __future__ import annotations

import io
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from fixtures.generate_fixtures import strip_pdf_metadata  # noqa: E402

OUT_DIR = ROOT / "samples_compress"
FONT = "HeiseiKakuGo-W5"
_WINDOWS_FONTS = ("C:/Windows/Fonts/meiryo.ttc", "C:/Windows/Fonts/msgothic.ttc", "C:/Windows/Fonts/YuGothM.ttc")


def _font(size: int):
    from PIL import ImageFont

    for path in _WINDOWS_FONTS:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _scan(size: tuple[int, int], title: str, dpi: int = 300):
    """紙の地のざらつき・色の付いた印影・細かい字の行を描いた、スキャンに似せた画像。"""
    from PIL import Image, ImageDraw

    noise = Image.effect_noise(size, 14).convert("RGB")
    image = Image.blend(Image.new("RGB", size, (238, 234, 226)), noise, 0.18)
    draw = ImageDraw.Draw(image)
    px = lambda pt: max(8, round(pt * dpi / 72))  # noqa: E731 - ポイント → 画素
    draw.text((int(size[0] * 0.08), int(size[1] * 0.05)), title, fill=(15, 15, 15), font=_font(px(18)))
    y = int(size[1] * 0.12)
    for pt in (10, 9, 8, 7, 6):
        font = _font(px(pt))
        for _ in range(4):
            draw.text(
                (int(size[0] * 0.08), y),
                f"{pt}ポイントの字：本契約は、甲と乙との間で、令和8年11月16日に締結した。第12条 123-4567",
                fill=(25, 25, 25), font=font,
            )
            y += int(px(pt) * 1.8)
    r = int(size[0] * 0.07)
    cx, cy = int(size[0] * 0.8), int(size[1] * 0.85)
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(200, 30, 30), width=max(3, r // 12))
    draw.text((cx - r // 2, cy - r // 3), "印", fill=(200, 30, 30), font=_font(r))
    return image


def _scanned_pdf(path: Path, pages: int) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=A4)
    for i in range(1, pages + 1):
        buf = io.BytesIO()
        _scan((2480, 3508), f"スキャンした契約書　{i} / {pages} ページ").save(buf, "JPEG", quality=95)
        c.drawImage(ImageReader(io.BytesIO(buf.getvalue())), 0, 0, *A4)
        c.showPage()
    c.save()
    strip_pdf_metadata(path)


def _text_pdf(path: Path) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas

    if FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(FONT))
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont(FONT, 12)
    for i in range(40):
        c.drawString(72, 780 - i * 18, f"文字だけの書面　{i + 1} 行目")
    c.showPage()
    c.save()
    strip_pdf_metadata(path)


def build() -> None:
    from PIL import Image

    # OneDrive の下ではフォルダの削除がロックされることがあるため、中身だけを消す。
    if OUT_DIR.exists():
        for p in OUT_DIR.iterdir():
            shutil.rmtree(p) if p.is_dir() else p.unlink()
    else:
        OUT_DIR.mkdir(parents=True)
    evidence = OUT_DIR / "証拠"
    evidence.mkdir()

    _scanned_pdf(evidence / "甲050 スキャンした契約書.pdf", 4)

    photo = _scan((4000, 3000), "スマホの写真（横向きに撮ったもの）", dpi=340)
    exif = Image.Exif()
    exif[0x0112] = 1
    photo.save(evidence / "甲051 スマホの写真.jpg", "JPEG", quality=95, exif=exif.tobytes())
    # メタデータ除去の項目を出さないよう、EXIF のない形で置き直す。
    with Image.open(evidence / "甲051 スマホの写真.jpg") as im:
        im.load()
        im.save(evidence / "甲051 スマホの写真.jpg", "JPEG", quality=95)

    _scan((2480, 3508), "画面の写し（PNG）").save(evidence / "甲052 画面の写し.png")
    _text_pdf(evidence / "甲053 文字だけの書面.pdf")


if __name__ == "__main__":
    build()
    print(f"作成しました：{OUT_DIR}")
    for p in sorted((OUT_DIR / "証拠").iterdir()):
        print(f"  {p.name}  {p.stat().st_size / (1024 * 1024):.1f}MB")
