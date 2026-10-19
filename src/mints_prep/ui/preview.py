"""ファイルプレビュー画像の生成（証拠番号処理の確認画面で使用）。

証拠番号スタンプを付けるかどうかの判断には、原本に既に証拠番号が記載されて
いないか（重複スタンプになっていないか）を目視で確認できる必要がある。
そのため、逐次レビュー画面（ui/write_review_dialog.py）は各対象ファイルの
プレビューを表示する。証拠番号スタンプのチェックボックスがON/OFFされた際は、
実際にそのラベルを焼き込んだ場合の見た目（render_preview_with_stamp）と、
未適用時の見た目（render_preview）とを切り替えて表示する。

PDFのラスタライズには、既存依存の pdfplumber の page.to_image() を用いる。
これはPure Pythonの実装で、Wand/ImageMagickやpoppler等の外部バイナリを
一切必要としない（CLAUDE.md「完全ローカル動作」原則・配布のしやすさに適合）。

Tkinterに依存しない純粋関数として実装し、単体テストで検証できるようにする。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pdfplumber
from PIL import Image
from pypdf import PdfReader, PdfWriter

from mints_prep.config import EvidenceStampConfig
from mints_prep.models import FileRecord
from mints_prep.processors.evidence_stamp import stamp_image, stamp_pdf

_IMAGE_FORMATS = {"JPEG", "PNG"}


def get_page_count(record: FileRecord) -> int:
    """プレビュー可能なページ数を返す（ページ送りUIの範囲判定に使う）。

    PDFは実際のページ数、画像（JPEG/PNG）は1、パスワード付きPDFや
    それ以外の形式（MP3/MP4等。ページの概念がない）・読み取り不可の場合は0。
    """
    if record.pdf_finding is not None and record.pdf_finding.has_password:
        return 0
    try:
        if record.detected_format == "PDF":
            with pdfplumber.open(record.path) as pdf:
                return len(pdf.pages)
        elif record.detected_format in _IMAGE_FORMATS:
            return 1
        return 0
    except Exception:
        return 0


def render_preview(
    record: FileRecord,
    page_index: int = 0,
    max_width: int = 420,
    max_height: int = 560,
    rotate_degrees: int = 0,
) -> Image.Image | None:
    """プレビュー画像（PIL Image）を生成する。プレビュー不可の場合はNoneを返す。

    page_index：表示するページ（0始まり）。PDFの全ページを確認できるように
    する（画像等、ページの概念がない形式では無視される）。
    rotate_degrees：向き補正（4.6）のプレビュー用の回転角（時計回り・90の倍数）。
    /Rotate属性による回転と同じ見た目になる。
    プレビュー不可となるケース：パスワード付きPDF（復号キーがないと開けない）、
    PDF/JPEG/PNG以外の形式（MP3/MP4等。ページの概念がなく視覚的プレビューが
    成立しない）、指定ページが範囲外、及び読み取り中に例外が発生した場合
    （破損ファイル等）。
    """
    if record.pdf_finding is not None and record.pdf_finding.has_password:
        return None

    try:
        if record.detected_format == "PDF":
            with pdfplumber.open(record.path) as pdf:
                if not (0 <= page_index < len(pdf.pages)):
                    return None
                image = pdf.pages[page_index].to_image(resolution=150).original
        elif record.detected_format in _IMAGE_FORMATS:
            with Image.open(record.path) as opened:
                image = opened.copy()
        else:
            return None
    except Exception:
        return None

    image = image.copy()
    if rotate_degrees % 360:
        # PILのrotateは反時計回りのため、時計回り指定（/Rotate互換）を反転する。
        image = image.rotate(-rotate_degrees, expand=True)
    image.thumbnail((max_width, max_height))
    return image


def render_preview_with_stamp(
    record: FileRecord,
    label: str,
    stamp_config: EvidenceStampConfig,
    page_index: int = 0,
    max_width: int = 420,
    max_height: int = 560,
    rotate_degrees: int = 0,
) -> Image.Image | None:
    """証拠番号スタンプを実際に焼き込んだ場合の見た目でプレビュー画像を生成する。

    render_preview と同じくプレビュー不可の場合はNoneを返す。実際のスタンプ
    処理（evidence_stamp.stamp_pdf/stamp_image）をそのまま一時ファイルへ適用
    してからラスタライズするため、見た目は書き出し結果と一致する（別実装を
    保守する必要がない）。証拠番号スタンプは書き出し時に全ページへ同じ
    ラベルが焼き込まれる（evidence_stamp.stamp_pdf参照）ため、page_indexで
    指定した1ページのみを抽出してスタンプすれば書き出し結果と一致する見た目
    が得られる（全ページスタンプする無駄な処理を避ける）。元ファイルは変更しない。
    """
    if record.pdf_finding is not None and record.pdf_finding.has_password:
        return None

    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            if record.detected_format == "PDF":
                reader = PdfReader(str(record.path))
                if not (0 <= page_index < len(reader.pages)):
                    return None
                first_page_path = tmp_dir / "page1.pdf"
                writer = PdfWriter()
                writer.add_page(reader.pages[page_index])
                if rotate_degrees % 360:
                    # 向き補正（4.6）のプレビュー：書き出しと同じく、回転を
                    # 適用してからスタンプする（スタンプは回転後の右上に載る）。
                    writer.pages[0].rotate(rotate_degrees)
                with open(first_page_path, "wb") as f:
                    writer.write(f)
                stamped_path = tmp_dir / "stamped.pdf"
                stamp_pdf(first_page_path, stamped_path, label, stamp_config)
                with pdfplumber.open(stamped_path) as pdf:
                    image = pdf.pages[0].to_image(resolution=150).original
            elif record.detected_format in _IMAGE_FORMATS:
                stamped_path = tmp_dir / f"stamped{Path(record.path).suffix}"
                stamp_image(record.path, stamped_path, label, stamp_config)
                with Image.open(stamped_path) as opened:
                    image = opened.copy()
            else:
                return None
    except Exception:
        return None

    image = image.copy()
    image.thumbnail((max_width, max_height))
    return image
