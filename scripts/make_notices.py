"""VA mints が使っている部品のライセンスの表示（THIRD_PARTY_NOTICES.txt）を作る（2026-11-17。
VA Document の scripts/make_notices.py にならう）。

exe に入る部品（Python・Tcl/Tk・pikepdf〔qpdf〕・pdfplumber・pdfminer.six・pypdf・ReportLab・
pypdfium2〔PDFium〕・Pillow・python-docx・openpyxl・python-pptx・tkinterdnd2・tkdnd・PyYAML・
PyInstaller のブートローダーと、それらが使う部品）と、ボタンのアイコン（Fluent UI System Icons）の
ライセンスの全文を、いま入っている部品のファイルから集める。部品を足したり、版を上げたりしたら、
作り直してコミットする（scripts/build_release.py が配布物に入れる）。

VA mints は MIT License。MIT License と合わない部品（GPL・AGPL。Ver201 までの mutagen、PyMuPDF など）は
配るものに入れない（tests/test_license.py で確かめる）。

実行のしかた：
    .venv\\Scripts\\python.exe scripts\\make_notices.py
"""

from __future__ import annotations

import importlib.metadata as metadata
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "THIRD_PARTY_NOTICES.txt"
PY_HOME = Path(sys.base_prefix)
# tkdnd の使用許諾は、tkdnd.tcl の先頭に書いてある（tkinterdnd2 に別のファイルがない）
TKDND_TCL = Path(metadata.distribution("tkinterdnd2").locate_file("tkinterdnd2/tkdnd/win-x64/tkdnd.tcl"))
_PDFIUM_BUILD = [
    "pdfium.txt", "pdfium-binaries.txt", "abseil.txt", "agg23.txt", "fast_float.txt", "freetype.txt", "icu.txt",
    "lcms.txt", "libjpeg_turbo.ijg", "libjpeg_turbo.md", "libopenjpeg.txt", "libpng.txt", "libtiff.txt",
    "llvm-libc.txt", "simdutf.txt", "zlib.txt",
]

# (表示する名前, 配布の名前〔版を読む。None は版なし〕, 用途, ライセンス, 全文のファイル)
COMPONENTS = [
    ("Python", None, "プログラムの言語（exe に入る。OpenSSL・libffi・zlib などの同梱の部品の条件も、この中にある）",
     "PSF License Version 2", [PY_HOME / "LICENSE.txt"]),
    ("Tcl/Tk", None, "画面", "Tcl/Tk License（BSD 形式）", [PY_HOME / "tcl" / "tk8.6" / "license.terms"]),
    ("pikepdf", "pikepdf",
     "PDF の検査・編集（パスワード・寸法・メタデータ・ページの編集・圧縮）。中に qpdf（Apache License 2.0）を含む。"
     "pikepdf のソースは https://github.com/pikepdf/pikepdf で手に入る（VA mints は pikepdf を改変していない）",
     "Mozilla Public License 2.0（qpdf は Apache License 2.0）",
     ["licenses/LICENSE.txt", "licenses/licenses-for-wheels.txt"]),
    ("pdfplumber", "pdfplumber", "PDF の字と図形の読み取り（墨消しの検証）", "MIT License", ["licenses/LICENSE.txt"]),
    ("pdfminer.six", "pdfminer.six", "PDF の字の読み取り（pdfplumber が使う）", "MIT License", ["licenses/LICENSE"]),
    ("charset-normalizer", "charset-normalizer", "文字コードの判定（pdfminer.six が使う）", "MIT License",
     ["licenses/LICENSE"]),
    ("cryptography", "cryptography", "暗号化された PDF を読む（pdfminer.six が使う）",
     "Apache License 2.0 または BSD License（3条項）。どちらかを選べる",
     ["licenses/LICENSE", "licenses/LICENSE.APACHE", "licenses/LICENSE.BSD"]),
    ("cffi", "cffi", "cryptography が使う", "MIT License", ["licenses/LICENSE"]),
    ("pycparser", "pycparser", "cffi が使う", "BSD License（3条項）", ["licenses/LICENSE"]),
    ("pypdf", "pypdf", "PDF の証拠番号スタンプの合成", "BSD License（3条項）", ["licenses/LICENSE"]),
    ("ReportLab", "reportlab", "証拠番号スタンプの作成", "BSD License", ["licenses/LICENSE"]),
    ("pypdfium2", "pypdfium2",
     "プレビュー（PDF のページを画像にする）・隠した所の下の文字の検査。Google の PDFium と、PDFium が使う部品を含む",
     "Apache License 2.0・BSD License（3条項）。PDFium と、PDFium が使う部品のライセンスも写す",
     ["licenses/LICENSES/Apache-2.0.txt", "licenses/LICENSES/BSD-3-Clause.txt"]
     + [f"licenses/data/windows_x64/BUILD_LICENSES/{n}" for n in _PDFIUM_BUILD]),
    ("Pillow", "pillow", "画像（JPEG・PNG）の検査・スタンプ・圧縮、アイコン", "MIT-CMU License", ["licenses/LICENSE"]),
    ("python-docx", "python-docx", "Word の文書のプロパティの読み取り（メタデータの検出）", "MIT License",
     ["licenses/LICENSE"]),
    ("openpyxl", "openpyxl", "Excel のブックのプロパティの読み取り（メタデータの検出）", "MIT License", ["LICENCE.rst"]),
    ("et-xmlfile", "et-xmlfile", "openpyxl が使う", "MIT License", ["LICENCE.rst"]),
    ("python-pptx", "python-pptx", "PowerPoint のファイルのプロパティの読み取り（メタデータの検出）", "MIT License",
     ["LICENSE"]),
    ("XlsxWriter", "xlsxwriter", "python-pptx が使う", "BSD License（2条項）", ["LICENSE.txt"]),
    ("lxml", "lxml", "python-docx・python-pptx が使う（XML の読み書き）", "BSD License（3条項）",
     ["licenses/LICENSE.txt", "licenses/LICENSES.txt"]),
    ("typing_extensions", "typing_extensions", "python-docx・python-pptx が使う", "PSF License Version 2",
     ["licenses/LICENSE"]),
    ("tkinterdnd2", "tkinterdnd2", "エクスプローラーからのドラッグアンドドロップ", "MIT License", ["licenses/LICENSE"]),
    ("tkdnd", None, "エクスプローラーからのドラッグアンドドロップ（tkinterdnd2 に入っている Tk の拡張）",
     "BSD 形式（Tcl/Tk と同じ条項）", [TKDND_TCL]),
    ("PyYAML", "pyyaml", "設定ファイル（config.yaml）の読み込み", "MIT License", ["licenses/LICENSE"]),
    ("packaging", "packaging", "PyInstaller の実行時の部品", "Apache License 2.0 または BSD License（2条項）。どちらかを選べる",
     ["licenses/LICENSE", "licenses/LICENSE.APACHE", "licenses/LICENSE.BSD"]),
    ("PyInstaller", "pyinstaller", "実行ファイルの作成（ブートローダーが exe に入る）",
     "GNU General Public License 第2版以降（ブートローダーの例外条項付き。exe に入るブートローダーは、"
     "この例外により、VA mints を MIT License で配ることを妨げない）", ["licenses/COPYING.txt"]),
    ("Fluent UI System Icons", None, "リボンのボタンのアイコン（元に戻す・やり直す・回転は、これをもとに自作）",
     "MIT License", [ROOT / "src" / "mints_prep" / "ui" / "icons" / "LICENSE"]),
]


def _license_files(dist: str | None, files: list) -> list[tuple[str, str]]:
    out = []
    for f in files:
        if isinstance(f, Path):
            path = f
        else:
            d = metadata.distribution(dist)
            matches = [p for p in d.files or [] if str(p).replace("\\", "/").endswith(f".dist-info/{f}")]
            if not matches:
                raise FileNotFoundError(f"{dist} の {f} が見つかりません")
            path = Path(d.locate_file(matches[0]))
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        if path.suffix == ".tcl":
            # Tcl のファイル（tkdnd.tcl）は、先頭のコメント（著作権と使用許諾）だけを写す
            head = []
            for line in text.splitlines():
                if not line.startswith("#"):
                    break
                head.append(line[2:] if line.startswith("# ") else line[1:])
            text = "\n".join(head).strip("\n") + "\n"
        out.append((path.name, text))
    return out


def build() -> Path:
    parts = [
        "VA mints が使っている部品のライセンスの表示",
        "",
        "VA mints そのものは、MIT License で公開しています（LICENSE）。",
        "VA mints の実行ファイル（VA_mints.exe）には、次の部品が入っています。",
        "",
    ]
    for name, dist, use, lic, files in COMPONENTS:
        if name == "Python":
            version = sys.version.split()[0]
        elif name == "tkdnd":
            version = next(
                (p.name.removeprefix("libtkdnd").removesuffix(".dll") for p in TKDND_TCL.parent.glob("libtkdnd*.dll")), ""
            )
        else:
            version = metadata.version(dist) if dist else ""
        parts += ["=" * 78, f"{name} {version}".rstrip(), f"用途：{use}", f"ライセンス：{lic}", "=" * 78]
        for fname, text in _license_files(dist, files):
            parts += [f"--- {fname} ---", text.rstrip(), ""]
        parts.append("")
    OUTPUT.write_text("\n".join(parts) + "\n", encoding="utf-8", newline="\n")
    return OUTPUT


if __name__ == "__main__":
    print(f"作りました：{build()}")
