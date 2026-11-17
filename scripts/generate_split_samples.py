"""ファイルの分割（Ver300。要件定義書 4.10）を、手で確かめるためのサンプル（samples_split/）を
作るスクリプト。

実行方法：
    .venv\\Scripts\\python.exe scripts\\generate_split_samples.py

できるもの（いずれも本スクリプトが作るダミーデータで、実際の証拠の内容は含まない）：
    samples_split\\証拠\\                          対象フォルダ
        まとめてスキャン.pdf                      7ページ。3つの証拠を続けて読み取ったもの
                                                  （契約書 2ページ・領収書 1ページ・写真の説明 4ページ）
        甲040 陳述書.pdf                          3ページ。分けないファイル（比べるため）

各ページの中央に、何の証拠の何ページ目かを大きな字で描く。メタデータは除去しておく
（分割以外の項目を出さない）。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from fixtures.generate_fixtures import strip_pdf_metadata  # noqa: E402

OUT_DIR = ROOT / "samples_split"
FONT = "HeiseiKakuGo-W5"


def _pdf(path: Path, pages: list[tuple[str, int, int]]) -> Path:
    """pages：（何の証拠か、そのページ、その証拠のページ数）の並び。"""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas

    if FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(FONT))
    w, h = A4
    c = canvas.Canvas(str(path), pagesize=A4)
    for title, page, count in pages:
        c.setFont(FONT, 12)
        c.drawString(72, h - 72, "VA mints サンプル（分割）")
        c.setFont(FONT, 36)
        c.drawCentredString(w / 2, h / 2 + 20, title)
        c.setFont(FONT, 20)
        c.drawCentredString(w / 2, h / 2 - 30, f"{page} / {count} ページ")
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
    evidence = OUT_DIR / "証拠"
    evidence.mkdir()

    scanned = (
        [("契約書", i, 2) for i in (1, 2)]
        + [("領収書", 1, 1)]
        + [("写真の説明", i, 4) for i in (1, 2, 3, 4)]
    )
    _pdf(evidence / "まとめてスキャン.pdf", scanned)
    _pdf(evidence / "甲040 陳述書.pdf", [("陳述書", i, 3) for i in (1, 2, 3)])


if __name__ == "__main__":
    build()
    print(f"作成しました：{OUT_DIR}")
