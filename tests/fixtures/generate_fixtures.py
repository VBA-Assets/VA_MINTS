"""テストフィクスチャ生成。

docs/03_requirements.md「テスト方針」：mints本番環境には接続せず、要件定義書に
記載された各種エラー条件（パスワード付きPDF、レターサイズPDF、100文字超の
ファイル名、非対応形式ファイル等）を再現したサンプルファイルで検証ロジックを
確認する。ここではそれらのサンプルファイルを都度生成する関数群を提供する
（バイナリを直接リポジトリにコミットせず、pytest実行のたびに再現可能にするため）。
"""

from __future__ import annotations

from pathlib import Path

import pikepdf
from PIL import Image
from reportlab.lib.pagesizes import A3, A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

LETTER_SIZE_PT = (612.0, 792.0)  # 215.9mm x 279.4mm（US Letter。A4との誤判定検出用）

# 日本語を含むテキストをPDFに描画する際のフォント。標準のHelvetica等は
# 日本語グリフを持たず、文字化け（'n'等の代替グリフへの置換）を起こし、
# 個人情報パターン検出等の正規表現の単語境界判定が壊れる原因になるため、
# processors/evidence_stamp.pyと同じAdobe-Japan1標準CIDフォントを使う
# （フォントファイルの同梱・外部取得を要さない）。
_JP_FONT = "HeiseiKakuGo-W5"
_jp_font_registered = False


def _ensure_jp_font_registered() -> None:
    global _jp_font_registered
    if not _jp_font_registered:
        pdfmetrics.registerFont(UnicodeCIDFont(_JP_FONT))
        _jp_font_registered = True


def make_pdf(path: Path, pagesize: tuple[float, float], with_metadata: bool = False) -> Path:
    c = canvas.Canvas(str(path), pagesize=pagesize)
    if with_metadata:
        c.setAuthor("テスト太郎")
        c.setTitle("テストPDF")
    c.drawString(72, pagesize[1] - 72, "VA mints テストフィクスチャ")
    c.showPage()
    c.save()
    return path


def make_a4_pdf(path: Path, with_metadata: bool = False) -> Path:
    return make_pdf(path, A4, with_metadata=with_metadata)


def strip_pdf_metadata(path: Path) -> Path:
    """docinfoを完全に空にする（PDF生成ライブラリが自動付与するProducer等も含めて除去）。

    メタデータ「なし」を検証するテストフィクスチャ用。reportlab等のPDF生成ライブラリは
    Producer・CreationDate等を自動的に書き込むため、生成直後のPDFは常にメタデータ
    「あり」となる。除去済み状態を再現するために用いる。
    """
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        for key in list(pdf.docinfo.keys()):
            del pdf.docinfo[key]
        pdf.save(path)
    return path


def make_a4_pdf_no_metadata(path: Path) -> Path:
    make_pdf(path, A4, with_metadata=False)
    return strip_pdf_metadata(path)


def make_rotated_pdf(path: Path, rotate: int = 90) -> Path:
    """縦置きA4のMediaBoxに/Rotate属性を付けたPDF（スキャナ・複合機の出力を模す）。

    閲覧時の見た目はrotate=90/270で横向きになる。回転属性を無視した
    向き判定・スタンプ位置の不具合を検証するためのフィクスチャ。
    """
    make_a4_pdf(path)
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        pdf.pages[0].Rotate = rotate
        pdf.save(path)
    return path


def make_pdf_with_light_fill_rect_under_text(path: Path) -> Path:
    """薄い網掛け（表セルの背景等）の上に文字を描いたPDF。

    ごく普通の文書装飾であり「墨消し」ではない。暗色判定なしの
    墨消し検証が誤検出しないことを確認するためのフィクスチャ。
    """
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFillColorRGB(0.9, 0.9, 0.9)  # 薄灰の網掛け
    c.rect(70, 690, 200, 20, fill=1, stroke=0)
    c.setFillColorRGB(0, 0, 0)
    c.drawString(75, 695, "header text on shading")
    c.showPage()
    c.save()
    return path


def make_plain_jpeg(path: Path) -> Path:
    """Exif等を一切付けない素のJPEG（PILはJFIFヘッダの構造情報のみ持つ）。"""
    Image.new("RGB", (10, 10), "white").save(path, "JPEG")
    return path


def make_palette_png(path: Path) -> Path:
    """パレット形式（Pモード）のPNG。左ピクセルが青系・右が赤。

    メタデータ除去処理がパレットを引き継がないと出力が真っ黒になる
    不具合を検証するためのフィクスチャ。
    """
    img = Image.new("RGB", (2, 1))
    img.putpixel((0, 0), (0, 128, 255))
    img.putpixel((1, 0), (255, 0, 0))
    img.convert("P", palette=Image.ADAPTIVE).save(path)
    return path


def make_multipage_pdf(path: Path, page_count: int = 3) -> Path:
    """複数ページのA4 PDF（プレビュー画面のページ送り機能のテスト用）。
    各ページに識別用のページ番号文字列を描画し、表示中のページを判別できるようにする。
    """
    c = canvas.Canvas(str(path), pagesize=A4)
    for i in range(page_count):
        c.drawString(72, A4[1] - 72, f"page {i + 1} of {page_count}")
        c.showPage()
    c.save()
    return path


def make_a4_landscape_pdf(path: Path) -> Path:
    """A4横置きのPDF（基本形〔A4=縦〕からの向き不一致。向き補正のテスト用）。"""
    return make_pdf(path, landscape(A4))


def make_a3_pdf(path: Path) -> Path:
    """A3・縦置き（基本形である横置きからの逸脱＝向き不一致検出用）。"""
    return make_pdf(path, A3)


def make_a3_landscape_pdf(path: Path) -> Path:
    """A3・横置き（公式の向き規律における基本形）。"""
    return make_pdf(path, landscape(A3))


def make_letter_size_pdf(path: Path) -> Path:
    """A4でもA3でもないレターサイズPDF（用紙寸法エラー検出用）。"""
    return make_pdf(path, LETTER_SIZE_PT)


def make_password_pdf(path: Path, password: str = "test1234") -> Path:
    plain = path.with_suffix(".plain.tmp")
    make_a4_pdf(plain)
    with pikepdf.open(plain) as pdf:
        pdf.save(
            path,
            encryption=pikepdf.Encryption(user=password, owner=password + "-owner"),
        )
    plain.unlink()
    return path


def make_permission_restricted_pdf(path: Path) -> Path:
    """ユーザーパスワードは不要（空）だが、オーナーパスワードにより印刷・コピー等が
    制限されたPDF（登記情報提供サービスのPDF等を想定）。

    要件定義書4.6「操作制限（印刷・コピー制限）PDFの検出」用フィクスチャ。
    パスワード入力なしに開けるが暗号化辞書が存在する点が、通常のパスワード付き
    PDF（make_password_pdf。開くこと自体にパスワードが必須）との違い。
    """
    plain = path.with_suffix(".plain.tmp")
    make_a4_pdf(plain)
    with pikepdf.open(plain) as pdf:
        perms = pikepdf.Permissions(extract=False, print_lowres=False, print_highres=False)
        pdf.save(
            path,
            encryption=pikepdf.Encryption(owner="owner-secret", user="", allow=perms),
        )
    plain.unlink()
    return path


def make_encrypted_pdf_all_permissions_allowed(path: Path) -> Path:
    """ユーザーパスワード不要・全権限許可だが暗号化辞書は存在するPDF。

    要件定義書4.6は「ユーザーパスワードなしで開けるが暗号化辞書が存在する
    場合」を操作制限PDFの該当条件とする。権限フラグの制限有無で絞る旧実装が
    この形態を見逃していた不具合の回帰テスト用フィクスチャ。
    """
    plain = path.with_suffix(".plain.tmp")
    make_a4_pdf(plain)
    with pikepdf.open(plain) as pdf:
        pdf.save(
            path,
            encryption=pikepdf.Encryption(owner="owner-secret", user=""),
        )
    plain.unlink()
    return path


def make_long_filename_pdf(directory: Path, extra_chars: int = 10) -> Path:
    """拡張子込みで100文字を超えるファイル名のPDF。"""
    base = "あ" * (100 + extra_chars - len(".pdf"))
    path = directory / f"{base}.pdf"
    make_a4_pdf(path)
    return path


def make_unsupported_file(path: Path) -> Path:
    """mints「記録」「記録外」いずれにも該当しない形式（処理対象外）。"""
    path.write_bytes(b"PK\x03\x04dummy-zip-not-office")  # .zip等を想定したダミー
    return path


def make_extension_mismatch_file(path: Path) -> Path:
    """拡張子は.pdfだが実体はPDFではないファイル（形式不一致検出用）。"""
    path.write_bytes(b"this is not actually a pdf file")
    return path


def make_image_with_metadata(path: Path) -> Path:
    img = Image.new("RGB", (32, 32), color="white")
    exif = img.getexif()
    exif[271] = "TestCamera"  # Make
    exif[272] = "Model X"  # Model
    img.save(path, exif=exif)
    return path


def make_image_without_metadata(path: Path) -> Path:
    img = Image.new("RGB", (32, 32), color="white")
    img.save(path)
    return path


def make_pdf_with_black_box_redaction_failure(path: Path) -> Path:
    """黒塗り（塗りつぶし矩形）で覆っただけで、下の文字情報は削除されていないPDF。

    準備の手引39頁の懸念（マーカー機能等では文字情報が完全に削除されない）を
    再現する、いわゆる「見た目だけ隠した」墨消し失敗の典型例。
    """
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica", 14)
    c.drawString(72, 700, "SECRET TEXT UNDER BOX")
    c.setFillColorRGB(0, 0, 0)
    c.rect(70, 690, 200, 20, fill=1, stroke=0)  # テキストの上に重なる黒塗り
    c.drawString(72, 600, "plain visible text")
    c.showPage()
    c.save()
    return path


def make_pdf_with_highlight_redaction_failure(path: Path) -> Path:
    """マーカー（ハイライト注釈）で覆っただけで、下の文字情報は削除されていないPDF。"""
    plain = path.with_suffix(".plain.tmp")
    c = canvas.Canvas(str(plain), pagesize=A4)
    c.setFont("Helvetica", 14)
    c.drawString(72, 700, "highlighted secret text")
    c.showPage()
    c.save()

    with pikepdf.open(plain) as pdf:
        page = pdf.pages[0]
        # テキスト行（72,700付近）を覆うハイライト注釈を追加する。
        annot = pikepdf.Dictionary(
            Type=pikepdf.Name.Annot,
            Subtype=pikepdf.Name.Highlight,
            Rect=pikepdf.Array([70, 696, 260, 714]),
            QuadPoints=pikepdf.Array([70, 714, 260, 714, 70, 696, 260, 696]),
            C=pikepdf.Array([1, 1, 0]),
        )
        annot_obj = pdf.make_indirect(annot)
        page.Annots = pikepdf.Array([annot_obj])
        pdf.save(path)
    plain.unlink()
    return path


def make_pdf_with_proper_redaction(path: Path) -> Path:
    """黒塗り箇所の文字情報が実際に存在しない（適切に墨消しされた状態を模した）PDF。

    墨消し完全性検証の誤検出（偽陽性）がないことを確認するための対照群。
    """
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica", 14)
    c.drawString(72, 600, "plain visible text")
    c.setFillColorRGB(0, 0, 0)
    c.rect(70, 690, 200, 20, fill=1, stroke=0)  # 下に文字を配置しない黒塗り
    c.showPage()
    c.save()
    return path


def make_pdf_with_text(path: Path, lines: list[str]) -> Path:
    """任意のテキスト行を持つPDF（個人情報パターン検出テスト用）。日本語を
    含む行も文字化けせず描画できるよう、CIDフォントを使う。"""
    _ensure_jp_font_registered()
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont(_JP_FONT, 12)
    y = 750
    for line in lines:
        c.drawString(72, y, line)
        y -= 20
    c.showPage()
    c.save()
    return path


def make_docx_with_metadata(path: Path) -> Path:
    from docx import Document

    doc = Document()
    doc.add_paragraph("VA mints テストフィクスチャ")
    doc.core_properties.author = "テスト太郎"
    doc.save(path)
    return path
