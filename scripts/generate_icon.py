"""実行ファイル用アイコン（assets/VA_mints.ico）を生成するスクリプト。

ツール名「VA mints」を表示するとの指示（2026-07-29。当初は「mints」のみの
1行組みだったが、「VA」「mints」の2行組みへ変更）に基づき、アプリのテーマ
（ui/theme.py）と同じMicrosoft系アクセント青の角丸タイルに、白い太字で
「VA」「mints」を2行で描画する。

フォント・角丸半径・余白・行間はVA FAX（VA_FAX/build/make_icon.py）と
揃える（2026-07-30。当初は游ゴシック太字だったが、VA FAXと比べ線が細く
くっきりしないため、同じSegoe UI Boldへ変更）。表示文字は欧文のみのため
和文フォントは不要。

フォントサイズは行ごとに決める（2026-07-30。当初はVA FAXの実装に倣い
幅の広い行〔mints〕基準の共通サイズとしていたが、「VA」行がVA FAXの
「VA」より小さくなるため変更）。「VA」行はVA FAXと同一サイズにするため、
VA FAXのサイズ決定基準（「FAX」3文字が左右余白内に収まる最大サイズ）を
そのまま用いて算出し、「mints」行は自身が左右余白内に収まる最大サイズと
する。

バイナリ（.ico）をリポジトリにコミットしつつ、再現手順もコードとして
残すため、生成はこのスクリプトで行う（samples/のgenerate_sample_folder.py
と同じ方針）。

実行方法：
    .venv\\Scripts\\python.exe scripts\\generate_icon.py

生成後、mints_prep.specのicon指定によりexeへ埋め込まれる。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ICON_PATH = ROOT / "assets" / "VA_mints.ico"

# ui/theme.py の ACCENT と同じMicrosoft系アクセント青。
BACKGROUND = "#0078d4"
TEXT_COLOR = "#ffffff"

_CANVAS = 256  # マスター画像の一辺（ここから各サイズへ縮小生成する）
_CORNER_RADIUS = 28  # VA FAXと同じ角丸半径
_SIDE_PADDING = 30  # タイル端から文字までの左右余白（VA FAXと同じ）
_LINE_GAP = 10  # 「VA」と「mints」の行間（VA FAXと同じ）

# 欧文太字フォント候補（Windows標準搭載。上から順に探索。VA FAXと同じ）。
_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\segoeuib.ttf",  # Segoe UI Bold
    r"C:\Windows\Fonts\arialbd.ttf",  # Arial Bold
)

_ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]

_LINES = ("VA", "mints")


def _load_font(size_px: int) -> ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES:
        if Path(path).is_file():
            return ImageFont.truetype(path, size_px)
    raise FileNotFoundError(f"太字フォントが見つかりません（候補：{', '.join(_FONT_CANDIDATES)}）")


def _fit_font_size(text: str, max_width: int) -> int:
    """textがmax_widthに収まる最大のフォントサイズを求める。"""
    size = 10
    while True:
        font = _load_font(size + 2)
        bbox = font.getbbox(text)
        if bbox[2] - bbox[0] > max_width:
            return size
        size += 2


def build() -> None:
    ICON_PATH.parent.mkdir(parents=True, exist_ok=True)

    image = Image.new("RGBA", (_CANVAS, _CANVAS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (0, 0, _CANVAS - 1, _CANVAS - 1), radius=_CORNER_RADIUS, fill=BACKGROUND
    )

    # 行ごとのフォントサイズ。「VA」行はVA FAXの「VA」と同一サイズにするため、
    # VA FAXと同じ基準（「FAX」3文字が左右余白内に収まる最大サイズ）で決める。
    # 「mints」行は自身が左右余白内に収まる最大サイズとする。
    max_width = _CANVAS - _SIDE_PADDING * 2
    fonts = [
        _load_font(_fit_font_size("FAX", max_width)),  # VA
        _load_font(_fit_font_size(_LINES[1], max_width)),  # mints
    ]

    # 2行（VA / mints）を全体として上下中央に配置する
    bboxes = [font.getbbox(text) for text, font in zip(_LINES, fonts)]
    heights = [b[3] - b[1] for b in bboxes]
    total_height = sum(heights) + _LINE_GAP * (len(_LINES) - 1)
    y = (_CANVAS - total_height) // 2
    for text, font, bbox, height in zip(_LINES, fonts, bboxes, heights):
        x = (_CANVAS - (bbox[2] - bbox[0])) // 2 - bbox[0]
        draw.text((x, y - bbox[1]), text, font=font, fill=TEXT_COLOR)
        y += height + _LINE_GAP

    image.save(ICON_PATH, format="ICO", sizes=_ICO_SIZES)
    print(f"生成完了：{ICON_PATH}")


if __name__ == "__main__":
    build()
