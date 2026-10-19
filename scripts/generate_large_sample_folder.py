"""200MB分割機能を実際のGUIで手動検証するための大容量サンプルフォルダを生成する。

samples/ とは別に samples_large/ を作る（合計200MB超になるため、通常の
samples/ とは異なりリポジトリにはコミットしない。.gitignore参照）。

各ファイルは、pikepdfで有効なPDF構造を保ちつつ、ランダムな（圧縮の効かない）
バイト列を添付ファイルとして埋め込むことでサイズを稼ぐ。証拠として提出する
実際の内容は一切含まない合成データ。

実行方法：
    .venv\\Scripts\\python.exe scripts\\generate_large_sample_folder.py
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pikepdf
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent.parent
SAMPLES_LARGE_DIR = ROOT / "samples_large"

# 各ファイルのおおよそのサイズ（MB）。合計は安全マージン（既定180MB）を
# 確実に超え、かつ複数バッチに分かれるよう調整する。
FILE_SIZES_MB = [55, 55, 55, 55]  # 合計約220MB


def _make_large_pdf(path: Path, size_mb: int) -> None:
    base = path.with_suffix(".base.tmp")
    c = canvas.Canvas(str(base), pagesize=A4)
    c.setFont("Helvetica", 14)
    c.drawString(72, 750, f"200MB分割検証用ダミーファイル（約{size_mb}MB）")
    c.showPage()
    c.save()

    padding_bytes = size_mb * 1024 * 1024
    with pikepdf.open(base) as pdf:
        # ランダムなバイト列はFlate圧縮が効かないため、添付ファイルの
        # サイズがそのままファイルサイズの増分になる。
        pdf.attachments["padding.bin"] = pikepdf.AttachedFileSpec(
            pdf, os.urandom(padding_bytes)
        )
        pdf.save(path)
    base.unlink()


def build() -> None:
    if SAMPLES_LARGE_DIR.exists():
        shutil.rmtree(SAMPLES_LARGE_DIR)
    SAMPLES_LARGE_DIR.mkdir(parents=True)

    total_mb = 0
    for i, size_mb in enumerate(FILE_SIZES_MB, start=1):
        name = f"甲{i:03d} 大容量サンプル{i}.pdf"
        path = SAMPLES_LARGE_DIR / name
        print(f"生成中：{name}（約{size_mb}MB）...")
        _make_large_pdf(path, size_mb)
        actual_mb = path.stat().st_size / 1024 / 1024
        total_mb += actual_mb
        print(f"  完了：{actual_mb:.1f}MB")

    print(f"\n生成完了：{SAMPLES_LARGE_DIR}（合計約{total_mb:.1f}MB）")
    print("このフォルダは.gitignoreによりリポジトリにはコミットされません。")


if __name__ == "__main__":
    build()
