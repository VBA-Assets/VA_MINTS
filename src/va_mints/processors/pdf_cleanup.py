"""用紙を A4 にそろえる（要件定義書 4.15）と、付箋・コメント・添付ファイルを取り除く
（要件定義書 4.16）。いずれも Ver300（2026-11-17 班長指示）。

**用紙を A4 にそろえる**：mints の「記録」は、A4・A3 以外の大きさのページを含む PDF を
受け付けない（操作マニュアル66頁・別紙2 エラー#46）。A4・A3 以外のページ（レター
サイズ・B5・大きさのばらばらなスキャンなど）を、同じ向きの A4 の用紙に載せる。
- 大きいページは縮めて収め、小さいページは原寸のまま、用紙の真ん中に置く（小さい
  ものを引き伸ばさない。原本の大きさを保つ）。
- **ページは画像にしない**（第8章「原本忠実性」）。元のページの中身を、そのまま
  フォーム XObject として新しい用紙に描く（QPDF の placeFormXObject。文字・画像・
  テキストレイヤーは変わらない）。リンクなどの注釈も、同じ縮め方で位置を合わせて写す。
- A4・A3 のページには触れない。

**付箋・コメント・添付ファイルを取り除く**：事務所の中で付けた付箋のコメント・作成者名、
埋め込んだ添付ファイル、JavaScript（スクリプト）が、気づかれないまま提出されるのを防ぐ。
見た目はできるだけ変えない：
- 取り除く注釈：付箋（Text）・ポップアップ（Popup）・添付ファイル（FileAttachment）・
  音声（Sound）。付箋と添付ファイルのアイコンは消える。
- ハイライト・下線・図形・スタンプ・手書きなど（見た目の一部）は残し、その中の
  コメントの文（Contents・RC）・作成者名（T）・ポップアップへの参照だけを外す。
  テキストボックス（FreeText）は、Contents が見えている文字そのものなので、作成者名と
  ポップアップだけを外す。
- リンク（Link）とフォームの欄（Widget）は残す（フォームの欄を消すと、入力した値が
  消える）。ただし、それらに付いた JavaScript の動作は外す。
- 文書に埋め込んだ添付ファイル（EmbeddedFiles・AF）、文書の JavaScript（Names の
  JavaScript・開いたときの動作・AA）を外す。

いずれもファイルの内容を変えるため、呼び出し側（GUI）で必ず実行前にユーザーの確認を
得ること（CLAUDE.md「絶対に譲れない設計原則」3）。完全ローカル動作（原則1）のとおり、
外部通信はしない。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pikepdf
from pikepdf import Array, Dictionary, Name, Rectangle

from va_mints.config import PdfConfig

_PT_TO_MM = 25.4 / 72.0

# 取り除く注釈（付箋・ポップアップ・添付ファイル・音声）。
_REMOVE_SUBTYPES = {"/Text", "/Popup", "/FileAttachment", "/Sound"}
# コメントの文・作成者名を外す対象にしない注釈（マークアップ注釈でないもの）。
_NON_MARKUP_SUBTYPES = {
    "/Link", "/Widget", "/Popup", "/Screen", "/PrinterMark", "/TrapNet", "/Watermark",
    "/3D", "/Movie", "/RichMedia",
}


@dataclass(frozen=True)
class HiddenContent:
    """付箋・コメント・添付ファイル・JavaScript の数（取り除くと、どれだけ変わるか）。"""

    comments: int = 0  # 取り除く付箋と、コメントの文・作成者名を外す注釈の数
    attachments: int = 0  # 埋め込んだ添付ファイルと、添付ファイルの注釈の数
    javascript: bool = False

    @property
    def found(self) -> bool:
        return bool(self.comments or self.attachments or self.javascript)


def visible_size_mm(page: pikepdf.Page) -> tuple[float, float]:
    """閲覧するときの（幅, 高さ）mm。回転（/Rotate 90・270）では幅と高さが入れ替わる
    （detectors/pdf_detector.py と同じ測り方）。"""
    box = page.mediabox
    width = abs(float(box[2]) - float(box[0])) * _PT_TO_MM
    height = abs(float(box[3]) - float(box[1])) * _PT_TO_MM
    if _rotation(page) % 180 == 90:
        width, height = height, width
    return width, height


def _rotation(page: pikepdf.Page) -> int:
    try:
        return int(page.rotation) % 360
    except (TypeError, ValueError, AttributeError):
        return 0


def judge_size(width_mm: float, height_mm: float, config: PdfConfig) -> str:
    """"A4"・"A3"・"非対応"（向きによらない。許容誤差は config.size_tolerance_mm）。"""
    tol = config.size_tolerance_mm
    dims = sorted([width_mm, height_mm])
    for label, size in (("A4", config.a4_mm), ("A3", config.a3_mm)):
        target = sorted(size)
        if abs(dims[0] - target[0]) <= tol and abs(dims[1] - target[1]) <= tol:
            return label
    return "非対応"


def _fit_page(pdf: pikepdf.Pdf, index: int, config: PdfConfig) -> None:
    """index ページ目（0始まり）を、同じ向きの A4 の用紙に載せたものに置き換える。"""
    old = pdf.pages[index]
    width_mm, height_mm = visible_size_mm(old)
    short, long_ = sorted(config.a4_mm)
    if height_mm >= width_mm:
        target_w, target_h = short / _PT_TO_MM, long_ / _PT_TO_MM
    else:
        target_w, target_h = long_ / _PT_TO_MM, short / _PT_TO_MM
    new = pikepdf.Page(
        pdf.make_indirect(
            Dictionary(
                Type=Name.Page,
                MediaBox=Array([0, 0, target_w, target_h]),
                Resources=Dictionary(),
            )
        )
    )
    # 元のページを、回転・UserUnit を織り込んだフォーム XObject にする（ページの中身を
    # そのまま参照するので、画像にはならない）。
    formx = old.as_form_xobject(handle_transformations=True)
    name = new.add_resource(formx, Name.XObject, prefix="Fx")
    rect = Rectangle(0, 0, target_w, target_h)
    # 大きいものは縮め、小さいものは原寸のまま（allow_expand=False）。どちらも真ん中に置く。
    content = new.calc_form_xobject_placement(
        formx, name, rect, allow_shrink=True, allow_expand=False
    )
    matrix = new.get_matrix_for_form_xobject_placement(
        formx, rect, allow_shrink=True, allow_expand=False
    )
    new.obj.Contents = pdf.make_stream(content)
    pdf.pages.insert(index, new)
    del pdf.pages[index + 1]
    # リンクなどの注釈を、同じ縮め方で写す（位置を合わせる）。
    pdf.pages[index].copy_annotations(old, matrix)


def fit_pages_to_a4(pdf: pikepdf.Pdf, config: PdfConfig) -> list[int]:
    """A4・A3 以外のページを A4 にそろえる。そろえたページ（1始まり）を返す。"""
    fitted: list[int] = []
    for index in range(len(pdf.pages)):
        width_mm, height_mm = visible_size_mm(pdf.pages[index])
        if judge_size(width_mm, height_mm, config) != "非対応":
            continue
        _fit_page(pdf, index, config)
        fitted.append(index + 1)
    return fitted


def _is_javascript_action(action) -> bool:
    return isinstance(action, Dictionary) and action.get("/S") == Name.JavaScript


def _hidden_content(pdf: pikepdf.Pdf, apply: bool) -> HiddenContent:
    """付箋・コメント・添付ファイル・JavaScript を数える。apply なら、取り除く。"""
    comments = 0
    attachments = 0
    javascript = False
    for page in pdf.pages:
        annots = page.obj.get("/Annots")
        if isinstance(annots, Array):
            keep = Array()
            for annot in annots:
                if not isinstance(annot, Dictionary):
                    keep.append(annot)
                    continue
                subtype = str(annot.get("/Subtype", ""))
                if subtype in _REMOVE_SUBTYPES:
                    if subtype == "/FileAttachment":
                        attachments += 1
                    elif subtype != "/Popup":
                        comments += 1
                    continue  # keep に入れない（取り除く）
                if _is_javascript_action(annot.get("/A")) or "/AA" in annot:
                    javascript = True
                    if apply:
                        if _is_javascript_action(annot.get("/A")):
                            del annot["/A"]
                        if "/AA" in annot:
                            del annot["/AA"]
                if subtype not in _NON_MARKUP_SUBTYPES:
                    keys = ["/Popup", "/T"]
                    if subtype != "/FreeText":
                        keys += ["/Contents", "/RC"]
                    present = [k for k in keys if k in annot]
                    if present:
                        comments += 1
                        if apply:
                            for k in present:
                                del annot[k]
                keep.append(annot)
            if apply:
                if len(keep):
                    page.obj.Annots = keep
                else:
                    del page.obj["/Annots"]
        if "/AA" in page.obj:
            javascript = True
            if apply:
                del page.obj["/AA"]

    root = pdf.Root
    names = root.get("/Names")
    if isinstance(names, Dictionary):
        if "/EmbeddedFiles" in names:
            count = len(pdf.attachments)
            attachments += count
            if apply:
                del names["/EmbeddedFiles"]
        if "/JavaScript" in names:
            javascript = True
            if apply:
                del names["/JavaScript"]
    if "/AF" in root:
        af = root.get("/AF")
        attachments += len(af) if isinstance(af, Array) else 1
        if apply:
            del root["/AF"]
    if _is_javascript_action(root.get("/OpenAction")):
        javascript = True
        if apply:
            del root["/OpenAction"]
    if "/AA" in root:
        javascript = True
        if apply:
            del root["/AA"]
    return HiddenContent(comments=comments, attachments=attachments, javascript=javascript)


def inspect_hidden_content(pdf: pikepdf.Pdf) -> HiddenContent:
    """付箋・コメント・添付ファイル・JavaScript を数える（ファイルは変えない）。"""
    return _hidden_content(pdf, apply=False)


def clean_pdf(
    input_path: Path,
    output_path: Path,
    fit_a4: PdfConfig | None = None,
    strip_hidden: bool = False,
) -> None:
    """input_path の PDF に、付箋・コメント・添付ファイルの除去（strip_hidden）と、用紙を
    A4 にそろえる処理（fit_a4 が None でなければ、その寸法で）を行い、output_path に書く。

    除去を先に行う（そろえるときに写す注釈を、取り除いた後のものにする）。証拠番号
    スタンプは、この後に押すこと（縮めると、スタンプも小さくなるため。output_writer）。
    """
    with pikepdf.open(input_path) as pdf:
        if strip_hidden:
            _hidden_content(pdf, apply=True)
        if fit_a4 is not None:
            fit_pages_to_a4(pdf, fit_a4)
        pdf.save(output_path)
