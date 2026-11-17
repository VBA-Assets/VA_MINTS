"""1つのファイルの中の枝番の区切り（Ver300。要件定義書 4.11）を、手で確かめるための
サンプルフォルダ（samples_branch/）を作るスクリプト。

実行方法：
    .venv\\Scripts\\python.exe scripts\\generate_branch_samples.py

できるファイル（いずれも本スクリプトが作るダミーデータで、実際の証拠の内容は含まない）：
    領収書の束（5枚）.pdf          証拠番号のない5ページ。区切りの前に証拠番号が要ることと、
                                   名前を変えてから区切る手順を確かめる
    甲010 請求書の束（4枚）.pdf    証拠番号のある4ページ。すぐに区切りを付けられる
    甲011-1~3 診療明細書.pdf       範囲の付いた名前の3ページ。区切りの数と範囲が食い違う
                                   ときに、名前の範囲が区切りで決まるものに直ることを確かめる
    契約書.pdf                     1ページ。名前の先頭に証拠番号を付けると、スタンプの
                                   項目が出ることを確かめる

各ページの中央に、何の何枚目かが一目で分かる大きな字を描き、右上は空けておく
（スタンプが載る場所）。メタデータは除去しておく（区切り以外の項目を出さない）。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from fixtures.generate_fixtures import strip_pdf_metadata  # noqa: E402

OUT_DIR = ROOT / "samples_branch"
FONT = "HeiseiKakuGo-W5"


def _make(path: Path, title: str, pages: list[tuple[str, str]]) -> Path:
    """pages：各ページの（大きな見出し、その下の説明）。"""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas

    if FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(FONT))
    w, h = A4
    c = canvas.Canvas(str(path), pagesize=A4)
    for i, (heading, note) in enumerate(pages, start=1):
        c.setFont(FONT, 12)
        c.drawString(72, h - 72, f"VA mints サンプル（{title}）")
        c.setFont(FONT, 40)
        c.drawCentredString(w / 2, h / 2 + 30, heading)
        c.setFont(FONT, 16)
        c.drawCentredString(w / 2, h / 2 - 20, note)
        c.setFont(FONT, 12)
        c.drawCentredString(w / 2, 60, f"{i} / {len(pages)} ページ")
        c.showPage()
    c.save()
    return strip_pdf_metadata(path)


def build() -> None:
    # OneDrive の下ではフォルダの削除がロックされることがあるため、中身だけを消す。
    if OUT_DIR.exists():
        for p in OUT_DIR.iterdir():
            shutil.rmtree(p) if p.is_dir() else p.unlink()
    else:
        OUT_DIR.mkdir(parents=True)

    _make(
        OUT_DIR / "領収書の束（5枚）.pdf", "領収書の束",
        [
            ("領収書 A", "1枚目（A は2枚。ここから枝番1）"),
            ("領収書 A", "2枚目（A の続き）"),
            ("領収書 B", "1枚目（ここから枝番2）"),
            ("領収書 C", "1枚目（ここから枝番3）"),
            ("領収書 C", "2枚目（C の続き）"),
        ],
    )
    _make(
        OUT_DIR / "甲010 請求書の束（4枚）.pdf", "請求書の束",
        [
            ("請求書 1月分", "ここから枝番1"),
            ("請求書 2月分", "ここから枝番2"),
            ("請求書 3月分", "ここから枝番3"),
            ("請求書 3月分 別紙", "3月分の続き"),
        ],
    )
    _make(
        OUT_DIR / "甲011-1~3 診療明細書.pdf", "診療明細書",
        [
            ("診療明細書 X病院", "ここから枝番1"),
            ("診療明細書 X病院", "X病院の続き"),
            ("診療明細書 Y医院", "ここから枝番2（区切りは2つ。名前の「1~3」は「1~2」に直る）"),
        ],
    )
    _make(OUT_DIR / "契約書.pdf", "契約書", [("契約書", "証拠番号のない1ページ")])


if __name__ == "__main__":
    build()
    print(f"作成しました：{OUT_DIR}")
