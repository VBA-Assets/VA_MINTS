"""リボンのボタンのアイコンを、SVG から PNG にする（開発のときに一度だけ使う。VA Case の make_icons.py にならう）。

アイコンは Microsoft の Fluent UI System Icons（MIT License。scripts\\icons_src\\LICENSE）の 20px の SVG。
VA Case・VA Document と同じく2色にする：塗りつぶし版（<名前>_filled.svg）を薄い青（ICON_TINT）で下に敷き、
その上に線画を濃い色（TEXT）で重ねる。押せないときの版は、灰色（DISABLED_TEXT・ICON_TINT_DISABLED）。
大きいボタンの色付きのアイコン（<名前>_large.svg）があれば、それも PNG にする。

PyMuPDF は AGPL なので、配るもの（requirements.txt・exe）には入れない。ここで一度 PNG にして
src\\mints_prep\\ui\\icons\\ に入れ、起動したときは Pillow で画面の拡大率に合わせて縮める（ui\\icons.py）。
このスクリプトだけが PyMuPDF を使う（開発の PC にあるもの）。

使い方：python scripts\\make_ribbon_icons.py
"""
import re
import sys
from pathlib import Path

import fitz  # PyMuPDF（開発のときだけ）

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from mints_prep.ui import theme  # noqa: E402

SRC = ROOT / "scripts" / "icons_src"
OUT = ROOT / "src" / "mints_prep" / "ui" / "icons"
SVG_FILL = "#212121"
SMALL = 64     # 小さいアイコンの PNG の大きさ（画素。起動したときに縮める）
LARGE = 128    # 大きいボタンの色付きのアイコンの PNG の大きさ


def _body(svg):
    return re.search(r"<svg[^>]*>(.*)</svg>", svg, re.S).group(1)


def _png(svg, size):
    with fitz.open(stream=svg.encode("utf-8"), filetype="svg") as doc:
        page = doc[0]
        zoom = size / page.rect.width
        return page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=True).tobytes("png")


def two_tone(name, color, tint):
    svg = (SRC / f"{name}.svg").read_text(encoding="utf-8").replace(SVG_FILL, color)
    filled = SRC / f"{name}_filled.svg"
    if filled.exists():
        head = re.match(r"<svg[^>]*>", svg).group(0)
        svg = head + _body(filled.read_text(encoding="utf-8")).replace(SVG_FILL, tint) + _body(svg) + "</svg>"
    return svg


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    names = sorted(p.stem for p in SRC.glob("*.svg") if not p.stem.endswith(("_filled", "_large")))
    for name in names:
        (OUT / f"{name}.png").write_bytes(_png(two_tone(name, theme.TEXT, theme.ICON_TINT), SMALL))
        (OUT / f"{name}_disabled.png").write_bytes(
            _png(two_tone(name, theme.DISABLED_TEXT, theme.ICON_TINT_DISABLED), SMALL))
        large = SRC / f"{name}_large.svg"
        if large.exists():
            (OUT / f"{name}_large.png").write_bytes(_png(large.read_text(encoding="utf-8"), LARGE))
    print(f"{len(names)} 個のアイコンを PNG にしました：{OUT}")


if __name__ == "__main__":
    main()
