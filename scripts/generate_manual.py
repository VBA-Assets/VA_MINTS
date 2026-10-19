"""操作マニュアルPDF（VA_mints_操作マニュアル_VerNNN.pdf）を生成する。

ソースは docs/manual/VA_mints_操作マニュアル.md（編集用のMarkdown）。
このスクリプトがMarkdownの部分集合（見出し・段落・箇条書き・番号付きリスト・
表・`code`・**bold**・先頭のフロントマター）を解釈し、reportlabで体裁の整った
PDFを生成する。バイナリ（PDF）はビルド生成物としてリポジトリには含めず、
ソース（.md）とこのスクリプトから再現する（samples/・アイコンと同じ方針）。

実行方法：
    .venv\\Scripts\\python.exe scripts\\generate_manual.py

出力：docs/manual/VA_mints_操作マニュアル_<version>.pdf
      （<version>はフロントマターのversionから「.」を除いたもの。例：Ver110）

フォントはWindows標準搭載の游ゴシック（本文）とConsolas（等幅）を埋め込む。
Consolasは日本語グリフを持たず、`code`内に日本語が混じると文字が脱落する
ため、`code`内の非ASCII部分のみ等幅の和文フォント（MSゴシック）に切り替える
（2026-07-30。ファイル名例`20260726 甲001 売買契約書.pdf`等の漢字がPDFで
消えていた不具合の修正）。
配布・生成はWindows環境を前提とする（動作環境と一致）。
"""

from __future__ import annotations

import re
from html import escape
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    HRFlowable,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "docs" / "manual" / "VA_mints_操作マニュアル.md"
OUTPUT_DIR = ROOT / "docs" / "manual"

# アプリのテーマ（ui/theme.py）と同じMicrosoft系アクセント青。見出しの
# 左バー等に用いてツールと体裁をそろえる。
ACCENT = colors.HexColor("#0078d4")
HEADER_BG = colors.HexColor("#eeeeee")
GRID = colors.HexColor("#cccccc")
TEXT = colors.HexColor("#1a1a1a")

_FONT_REGULAR = "ManualBody"
_FONT_BOLD = "ManualBodyBold"
_FONT_MONO = "ManualMono"
_FONT_MONO_JP = "ManualMonoJP"  # code内の日本語用（Consolasに無いグリフを補う）
_FONT_FILES = {
    _FONT_REGULAR: r"C:\Windows\Fonts\YuGothR.ttc",
    _FONT_BOLD: r"C:\Windows\Fonts\YuGothB.ttc",
    _FONT_MONO: r"C:\Windows\Fonts\consola.ttf",
    _FONT_MONO_JP: r"C:\Windows\Fonts\msgothic.ttc",  # index 0 = MS ゴシック（等幅）
}


def _register_fonts() -> None:
    for name, path in _FONT_FILES.items():
        pdfmetrics.registerFont(TTFont(name, path, subfontIndex=0))
    pdfmetrics.registerFontFamily(
        _FONT_REGULAR, normal=_FONT_REGULAR, bold=_FONT_BOLD, italic=_FONT_REGULAR, boldItalic=_FONT_BOLD
    )


# ------------------------------------------------------------------ スタイル
def _styles() -> dict[str, ParagraphStyle]:
    body = ParagraphStyle(
        "body",
        fontName=_FONT_REGULAR,
        fontSize=10,
        leading=17,
        textColor=TEXT,
        alignment=TA_JUSTIFY,
        wordWrap="CJK",  # 日本語を任意の位置で折り返す
        spaceAfter=8,
    )
    return {
        "body": body,
        "title": ParagraphStyle(
            "title", parent=body, fontName=_FONT_BOLD, fontSize=22, leading=30, alignment=TA_CENTER
        ),
        "subtitle": ParagraphStyle(
            "subtitle", parent=body, fontSize=13, leading=20, alignment=TA_CENTER, textColor=colors.HexColor("#555555")
        ),
        "titlemeta": ParagraphStyle(
            "titlemeta", parent=body, fontSize=10, leading=18, alignment=TA_CENTER, textColor=colors.HexColor("#555555")
        ),
        "chapter": ParagraphStyle(
            "chapter", parent=body, fontName=_FONT_BOLD, fontSize=15, leading=22, spaceAfter=2, spaceBefore=0
        ),
        "toc_chapter": ParagraphStyle(
            "toc_chapter", parent=body, fontName=_FONT_BOLD, fontSize=10.5, leading=20, spaceAfter=0
        ),
        "toc_section": ParagraphStyle(
            "toc_section", parent=body, fontSize=10, leading=18, leftIndent=16, spaceAfter=0
        ),
        "cell": ParagraphStyle("cell", parent=body, alignment=TA_JUSTIFY, spaceAfter=0, leading=15, fontSize=9.5),
        "cellhead": ParagraphStyle(
            "cellhead", parent=body, fontName=_FONT_BOLD, spaceAfter=0, leading=15, fontSize=9.5
        ),
        "listitem": ParagraphStyle("listitem", parent=body, spaceAfter=3),
    }


def _code_span(content: str) -> str:
    """code内をフォント指定付きマークアップへ。

    Consolasは日本語グリフを持たないため、非ASCIIの連続部分のみ和文等幅
    フォントに切り替える（ASCII部分は従来どおりConsolas）。contentは
    escape()済みだが、実体参照（&amp;等）はASCIIのみなので分割で壊れない。
    """
    parts = re.split(r"([^\x00-\x7f]+)", content)
    out = []
    for part in parts:
        if not part:
            continue
        face = _FONT_MONO_JP if any(ord(c) > 127 for c in part) else _FONT_MONO
        out.append(f'<font face="{face}" size="9">{part}</font>')
    return "".join(out)


def _inline(text: str) -> str:
    """Markdownのインライン記法（`code`・**bold**）をreportlabのマークアップへ。"""
    out = escape(text)
    out = re.sub(r"`(.+?)`", lambda m: _code_span(m.group(1)), out)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    return out


# ------------------------------------------------------------------ 見出しバー
class SectionHeading(Flowable):
    """節見出し（例「1.1 これは何をするツールか」）。左に色付きの縦バーを描く。"""

    def __init__(self, text: str, style: ParagraphStyle) -> None:
        super().__init__()
        self._text = text
        self._style = style
        self._bar_w = 3.5
        self._gap = 8

    def wrap(self, avail_w: float, avail_h: float) -> tuple[float, float]:
        self._para = Paragraph(_inline(self._text), self._style)
        self._pw, self._ph = self._para.wrap(avail_w - self._bar_w - self._gap, avail_h)
        self._h = max(self._ph, self._style.fontSize)
        return avail_w, self._h

    def draw(self) -> None:
        c = self.canv
        c.setFillColor(ACCENT)
        c.rect(0, 0, self._bar_w, self._h, stroke=0, fill=1)
        self._para.drawOn(c, self._bar_w + self._gap, self._h - self._ph)


# ------------------------------------------------------------------ Markdown解析
def _parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    meta: dict[str, str] = {}
    if text.startswith("---"):
        _, fm, body = text.split("---", 2)
        for line in fm.strip().splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        return meta, body.strip()
    return meta, text


def _split_blocks(body: str) -> list[list[str]]:
    blocks: list[list[str]] = []
    cur: list[str] = []
    for line in body.splitlines():
        if line.strip() == "":
            if cur:
                blocks.append(cur)
                cur = []
        else:
            cur.append(line)
    if cur:
        blocks.append(cur)
    return blocks


def _table_flowable(lines: list[str], styles: dict[str, ParagraphStyle]) -> Table:
    rows = [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in lines]
    # 2行目（---|---）は区切りなので除外する。
    header, data = rows[0], rows[2:]
    table_data = [[Paragraph(_inline(c), styles["cellhead"]) for c in header]]
    for r in data:
        table_data.append([Paragraph(_inline(c), styles["cell"]) for c in r])

    ncol = len(header)
    avail = A4[0] - 40 * mm
    # 1列目はやや狭め、残りを均等配分（3列表・2列表の双方で見やすい配分）。
    if ncol == 2:
        widths = [avail * 0.28, avail * 0.72]
    elif ncol == 3:
        widths = [avail * 0.22, avail * 0.30, avail * 0.48]
    else:
        widths = [avail / ncol] * ncol

    table = Table(table_data, colWidths=widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
                ("GRID", (0, 0), (-1, -1), 0.5, GRID),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def _list_flowable(lines: list[str], styles: dict[str, ParagraphStyle], ordered: bool) -> ListFlowable:
    items = []
    for ln in lines:
        text = re.sub(r"^(\s*[-]|\s*\d+\.)\s+", "", ln)
        items.append(ListItem(Paragraph(_inline(text), styles["listitem"]), leftIndent=14))
    return ListFlowable(
        items,
        bulletType="1" if ordered else "bullet",
        bulletFormat="%s." if ordered else None,
        bulletFontName=_FONT_REGULAR,
        bulletColor=ACCENT if not ordered else TEXT,
        leftIndent=18,
        bulletFontSize=9,
    )


def build() -> Path:
    _register_fonts()
    styles = _styles()
    meta, body = _parse_front_matter(SOURCE.read_text(encoding="utf-8"))

    story: list[Flowable] = []

    # 表紙
    story.append(Spacer(1, 60 * mm))
    story.append(Paragraph(_inline(meta.get("title", "")), styles["title"]))
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(meta.get("version", ""), styles["subtitle"]))
    story.append(Spacer(1, 8 * mm))
    story.append(
        Paragraph(
            f"作成者：{meta.get('author', '')}　配布元：{meta.get('distributor', '')}", styles["titlemeta"]
        )
    )
    story.append(Paragraph(meta.get("date", ""), styles["titlemeta"]))
    story.append(PageBreak())

    blocks = _split_blocks(body)

    # 目次（H1「第N章」／H2見出しから自動生成）
    story.append(Paragraph("目次", styles["chapter"]))
    story.append(HRFlowable(width="100%", thickness=1, color=TEXT, spaceBefore=4, spaceAfter=10))
    for block in blocks:
        head = block[0]
        if head.startswith("# ") and "更新履歴" not in head:
            story.append(Paragraph(_inline(head[2:].strip()), styles["toc_chapter"]))
        elif head.startswith("## "):
            story.append(Paragraph(_inline(head[3:].strip()), styles["toc_section"]))
    story.append(PageBreak())

    # 本文
    for block in blocks:
        head = block[0]
        if head.startswith("# "):
            if story and not isinstance(story[-1], PageBreak):
                story.append(PageBreak())
            story.append(Paragraph(_inline(head[2:].strip()), styles["chapter"]))
            story.append(HRFlowable(width="100%", thickness=1, color=TEXT, spaceBefore=4, spaceAfter=12))
        elif head.startswith("## "):
            story.append(Spacer(1, 4))
            story.append(SectionHeading(head[3:].strip(), styles["chapter"]))
            story.append(Spacer(1, 8))
        elif "|" in head and head.strip().startswith("|"):
            story.append(_table_flowable(block, styles))
            story.append(Spacer(1, 8))
        elif re.match(r"^\s*-\s+", head):
            story.append(_list_flowable(block, styles, ordered=False))
            story.append(Spacer(1, 6))
        elif re.match(r"^\s*\d+\.\s+", head):
            story.append(_list_flowable(block, styles, ordered=True))
            story.append(Spacer(1, 6))
        else:
            story.append(Paragraph(_inline(" ".join(block)), styles["body"]))

    version_slug = meta.get("version", "Ver.000").replace(".", "").replace(" ", "")
    output = OUTPUT_DIR / f"VA_mints_操作マニュアル_{version_slug}.pdf"

    def _page_number(canvas, _doc) -> None:
        canvas.setFont(_FONT_REGULAR, 9)
        canvas.setFillColor(colors.HexColor("#777777"))
        canvas.drawCentredString(A4[0] / 2, 12 * mm, str(canvas.getPageNumber()))

    doc = SimpleDocTemplate(
        str(output),
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=22 * mm,
        bottomMargin=20 * mm,
        title=meta.get("title", ""),
        author=meta.get("author", ""),
    )
    doc.build(story, onFirstPage=_page_number, onLaterPages=_page_number)
    return output


if __name__ == "__main__":
    path = build()
    print(f"生成完了：{path}")
