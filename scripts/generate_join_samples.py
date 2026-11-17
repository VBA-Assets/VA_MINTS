"""ページの挿入とファイルの結合（Ver300。要件定義書 4.10・4.12）を、手で確かめるための
サンプル（samples_join/）を作るスクリプト。

実行方法：
    .venv\\Scripts\\python.exe scripts\\generate_join_samples.py

できるもの（いずれも本スクリプトが作るダミーデータで、実際の証拠の内容は含まない）：
    samples_join\\証拠\\               対象フォルダ
        甲020 契約書.pdf               2ページ。結合先にする
        甲020 契約書（別紙）.pdf       1ページ。甲020 契約書.pdf に結合する
        甲020 現場写真.jpg             撮影日時・GPS などの EXIF 付きの写真。結合・挿入の材料にする
        甲021 覚書.pdf                 別の証拠番号。甲020 と結合すると、注意が出る
    samples_join\\挿入用（フォルダの外）\\  対象フォルダの外にあるファイル
        追加資料.pdf                   2ページ。［ページを挿入］で入れる
        追加の写真.png                 横長の画像。A4 横置きの1ページになる

各ページの中央に、何のファイルの何ページ目かを大きな字で描く。メタデータは除去しておく
（結合・挿入以外の項目を出さない）。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from fixtures.generate_fixtures import strip_pdf_metadata  # noqa: E402

OUT_DIR = ROOT / "samples_join"
FONT = "HeiseiKakuGo-W5"


def _pdf(path: Path, title: str, page_count: int) -> Path:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas

    if FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(FONT))
    w, h = A4
    c = canvas.Canvas(str(path), pagesize=A4)
    for i in range(1, page_count + 1):
        c.setFont(FONT, 12)
        c.drawString(72, h - 72, "VA mints サンプル（挿入・結合）")
        c.setFont(FONT, 36)
        c.drawCentredString(w / 2, h / 2 + 20, title)
        c.setFont(FONT, 20)
        c.drawCentredString(w / 2, h / 2 - 30, f"{i} / {page_count} ページ")
        c.showPage()
    c.save()
    return strip_pdf_metadata(path)


def _photo(path: Path, size: tuple[int, int], text: str, with_exif: bool) -> Path:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", size, (220, 235, 245))
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, size[0] - 20, size[1] - 20), outline=(40, 80, 140), width=8)
    draw.text((60, 60), text, fill=(20, 40, 80))
    if with_exif:
        exif = Image.Exif()
        exif[0x0132] = "2026:11:16 10:00:00"  # 撮影日時
        exif[0x010F] = "SampleCamera"  # メーカー
        gps = {1: "N", 2: (35.0, 41.0, 0.0), 3: "E", 4: (139.0, 45.0, 0.0)}
        exif[0x8825] = gps
        image.save(path, format="JPEG", quality=90, exif=exif.tobytes())
    else:
        image.save(path)
    return path


def build() -> None:
    # OneDrive の下ではフォルダの削除がロックされることがあるため、中身だけを消す。
    if OUT_DIR.exists():
        for p in OUT_DIR.iterdir():
            shutil.rmtree(p) if p.is_dir() else p.unlink()
    else:
        OUT_DIR.mkdir(parents=True)
    evidence = OUT_DIR / "証拠"
    outside = OUT_DIR / "挿入用（フォルダの外）"
    evidence.mkdir()
    outside.mkdir()

    _pdf(evidence / "甲020 契約書.pdf", "契約書", 2)
    _pdf(evidence / "甲020 契約書（別紙）.pdf", "契約書（別紙）", 1)
    _photo(evidence / "甲020 現場写真.jpg", (1600, 1200), "Photo of the site (EXIF/GPS)", True)
    _pdf(evidence / "甲021 覚書.pdf", "覚書", 1)
    _pdf(outside / "追加資料.pdf", "追加資料", 2)
    _photo(outside / "追加の写真.png", (1400, 700), "Wide image (PNG)", False)


if __name__ == "__main__":
    build()
    print(f"作成しました：{OUT_DIR}")
