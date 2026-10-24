"""証拠番号の右上スタンプ焼き込み（要件定義書 4.7）。

準備の手引29頁「証拠番号は...証拠データの右上にも記載」に基づき、証拠番号
（例：「甲001」「甲001-1」）をPDFの各ページ又は画像の右上に焼き込む。

文字サイズ・余白は一次資料に数値定めがないため config.yaml（evidence_stamp）で
管理する（第7章「公式規定のない事項の扱い」）。

この処理はファイルの内容・体裁を変更するため、CLAUDE.md「絶対に譲れない設計
原則」3に従い、呼び出し側（GUI）で必ず実行前にユーザーの確認を得ること。
本モジュール自体は確認済みの実行を担うのみで、確認ダイアログを持たない。
"""

from __future__ import annotations

import io
import unicodedata
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader, PdfWriter
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

from mints_prep.config import EvidenceStampConfig

# Adobe-Japan1の標準CIDフォント。フォントファイルの同梱・外部取得を要さず、
# reportlabに組み込み済みのメトリクスのみでPDFに直接埋め込める
# （CLAUDE.md「完全ローカル動作」原則に適合）。
_PDF_STAMP_FONT = "HeiseiKakuGo-W5"
_pdf_font_registered = False

# 画像スタンプ用のTTFフォント（Pillowはreportlabと異なりCID組み込み
# フォントを持たないため、実ファイルが必要）。OS標準搭載フォントを
# 優先順に探索する。Windows標準搭載フォントを先頭に置くため、Windowsでの
# 出力は従来どおり（メイリオ）。その後ろは、ソースから動かすMac向けの
# 標準搭載フォント（ヒラギノ角ゴシック。古いMacはArial Unicode）。
_IMAGE_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\meiryo.ttc",
    r"C:\Windows\Fonts\YuGothM.ttc",
    r"C:\Windows\Fonts\msgothic.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
)


def _ensure_pdf_font_registered() -> None:
    global _pdf_font_registered
    if not _pdf_font_registered:
        pdfmetrics.registerFont(UnicodeCIDFont(_PDF_STAMP_FONT))
        _pdf_font_registered = True


# スタンプの見た目（evidence_stamp.style）に対応する描画色。
# 根拠：一次資料に色・枠の規定はなし（config.yaml参照）。旧・紙媒体実務の
# 赤枠・赤字慣行に合わせたい場合の選択肢として用意する。
_PDF_STYLE_COLORS = {"black": (0, 0, 0), "red": (1, 0, 0), "red_boxed": (1, 0, 0)}
_IMAGE_STYLE_COLORS = {"black": "black", "red": "red", "red_boxed": "red"}


def stamp_pdf_page(page, label: str, config: EvidenceStampConfig) -> None:
    """pypdfのページオブジェクトに、右上スタンプのオーバーレイを合成する（in-place）。

    座標はMediaBoxの右上隅（right/top）から算出する。merge_page()はオーバー
    レイの描画内容を対象ページと同一の座標空間へそのまま合成するため、
    MediaBoxの原点が(0,0)でないPDF（クロップ・オフセット付き）でも、幅・高さ
    だけでなく原点座標を踏まえないとスタンプ位置がずれる。
    """
    _ensure_pdf_font_registered()
    width = float(page.mediabox.width)
    height = float(page.mediabox.height)
    right = float(page.mediabox.right)
    top = float(page.mediabox.top)
    color = _PDF_STYLE_COLORS[config.style]

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    c.setFont(_PDF_STAMP_FONT, config.font_size_pt)
    c.setFillColorRGB(*color)
    x = right - config.margin_right_mm * mm
    y = top - config.margin_top_mm * mm - config.font_size_pt

    if config.style == "red_boxed":
        text_width = c.stringWidth(label, _PDF_STAMP_FONT, config.font_size_pt)
        pad = config.box_padding_mm * mm
        c.setStrokeColorRGB(*color)
        c.setLineWidth(config.box_line_width_pt)
        c.rect(
            x - text_width - pad,
            y - pad,
            text_width + 2 * pad,
            config.font_size_pt + 2 * pad,
            fill=0,
            stroke=1,
        )

    c.drawRightString(x, y, label)
    c.save()
    buf.seek(0)

    overlay = PdfReader(buf).pages[0]
    page.merge_page(overlay)


def stamp_pdf(input_path: Path, output_path: Path, label: str, config: EvidenceStampConfig) -> None:
    """PDFの全ページ右上に証拠番号ラベルを焼き込み、output_pathへ書き出す。

    ページに回転属性（/Rotate 90/270等）がある場合、MediaBox座標系のままの
    オーバーレイ合成では「閲覧時の右上」ではない隅に90度回転したスタンプが
    載ってしまう（スキャナ・複合機のPDFで頻発）。そのため、回転属性を持つ
    ページはtransfer_rotation_to_content()で回転をページ内容へ転写（見た目は
    不変のまま/Rotateを0に正規化）してからスタンプする。
    """
    writer = PdfWriter(clone_from=str(input_path))
    for page in writer.pages:
        if (page.rotation or 0) % 360 != 0:
            page.transfer_rotation_to_content()
        stamp_pdf_page(page, label, config)
    with open(output_path, "wb") as f:
        writer.write(f)


def _load_image_font(size_px: int) -> ImageFont.FreeTypeFont:
    for path in _IMAGE_FONT_CANDIDATES:
        # Macのファイル名は濁点・半濁点を分解した形（NFD）で保存されている
        # ことがあるため、日本語を含むパスは両方の形で探す。
        for candidate in dict.fromkeys((path, unicodedata.normalize("NFD", path))):
            if Path(candidate).exists():
                return ImageFont.truetype(candidate, size_px, index=0)
    raise FileNotFoundError(
        "証拠番号スタンプ用の日本語フォントが見つかりません"
        f"（候補：{', '.join(_IMAGE_FONT_CANDIDATES)}）。"
        "OS標準の日本語フォント（Windowsはメイリオ等、Macはヒラギノ角ゴシック）が必要です。"
    )


def stamp_image(input_path: Path, output_path: Path, label: str, config: EvidenceStampConfig) -> None:
    """画像（JPEG/PNG）の右上に証拠番号ラベルを焼き込み、output_pathへ書き出す。

    ポイント換算のPDFと異なり画像はピクセル単位のため、文字サイズはmm指定の
    font_size_ptをそのままピクセル数として用いる（画像解像度に応じた換算表は
    一次資料に定めがなく、簡明さを優先する）。

    EXIFのOrientation（縦横補正）が付いたJPEGは、ピクセルデータ上の「右上」と
    閲覧時の「右上」が一致しないため、先にexif_transposeで実際の向きへ正規化
    してから描画する（Orientationタグは転写により消化される）。残りのEXIFは
    出力へ引き継ぐ——メタデータ除去は別途の確認必須処理（4.5節）であり、
    スタンプ処理が黙ってメタデータを消してはならない（CLAUDE.md設計原則3）。
    """
    from PIL import ImageOps

    image = Image.open(input_path)
    original_format = image.format
    image = ImageOps.exif_transpose(image)
    size_px = max(1, round(config.font_size_pt))
    font = _load_image_font(size_px)
    draw = ImageDraw.Draw(image)

    # 小さい画像（サムネイル等）ではmm指定の余白がキャンバス外にはみ出し、
    # スタンプが全く描画されなくなる場合があるため、画像寸法に収まるように
    # クランプする。
    margin_right_px = min(round(config.margin_right_mm * 96 / 25.4), max(0, image.width - 1))
    margin_top_px = min(
        round(config.margin_top_mm * 96 / 25.4), max(0, image.height - size_px)
    )
    x = image.width - margin_right_px
    y = margin_top_px

    text_color = _IMAGE_STYLE_COLORS[config.style]

    # 縁取り（白）を先に描き、本文を重ねることで、背景の濃淡に関わらず
    # 判読性を確保する。
    for dx, dy in ((-1, -1), (-1, 1), (1, -1), (1, 1)):
        draw.text((x + dx, y + dy), label, font=font, fill="white", anchor="ra")
    draw.text((x, y), label, font=font, fill=text_color, anchor="ra")

    if config.style == "red_boxed":
        bbox = draw.textbbox((x, y), label, font=font, anchor="ra")
        pad_px = round(config.box_padding_mm * 96 / 25.4)
        box = (bbox[0] - pad_px, bbox[1] - pad_px, bbox[2] + pad_px, bbox[3] + pad_px)
        line_width_px = max(1, round(config.box_line_width_pt * 96 / 72))
        draw.rectangle(box, outline=text_color, width=line_width_px)

    exif_bytes = image.info.get("exif")
    if exif_bytes:
        image.save(output_path, format=original_format, exif=exif_bytes)
    else:
        image.save(output_path, format=original_format)


_IMAGE_FORMATS = {"JPEG", "PNG"}


def stamp_file(
    input_path: Path,
    output_path: Path,
    label: str,
    config: EvidenceStampConfig,
    detected_format: str,
) -> None:
    """検出形式に応じてPDF/画像いずれかのスタンプ処理を実行する。"""
    if detected_format == "PDF":
        stamp_pdf(input_path, output_path, label, config)
    elif detected_format in _IMAGE_FORMATS:
        stamp_image(input_path, output_path, label, config)
    else:
        raise ValueError(f"証拠番号スタンプに対応していない形式です：{detected_format}")
