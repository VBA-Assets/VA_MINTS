"""証拠説明書（要件定義書 4.17）：入力の保存と、mints に出す PDF の作成。

証拠説明書は、電子証拠説明書と兼ねて作り、PDF で電子提出する（準備の手引4頁・27〜28頁）。
号証・標目は、証拠のファイル名から決める（同29頁「ファイル名：証拠番号（半角三桁）に加えて
証拠説明書に記載する証拠の標目のとおり記載」）。画面の側（ui/workspace_description.py）が、
ファイルと行を結び付け、標目とファイル名をそろえる。このモジュールは Tk に依存しない。

- PDF は、対象フォルダの直下に作る（mints では、証拠と同じアップロードの画面で、種別のタブだけを
  替えて一緒に提出するため。操作マニュアル 65〜68頁。2026-11-18 班長指摘）。200MB を超えて分ける
  ときは「提出用_01」に入れる。
- 入力は、対象フォルダの中の「証拠説明書データ」フォルダの 証拠説明書.json に保存する（直下に置くと、
  mints に送るファイルと紛れるため。隠しフォルダにはしない：エクスプローラーの設定で見え方が変わり、
  フォルダを写すときに漏れるおそれがあるため。2026-11-18 班長指摘）。「元のファイル」フォルダには置かない（［元のファイルに戻す］で
  すべて戻すと、そのフォルダは消えるため）。
- 行は、証拠のファイル（対象フォルダからの相対位置）と、そのファイルの中の何行目（枝番の何番目。
  1始まり）で覚える。ファイルの名前が変わったときは、更新の後に付け替える（rekey）。名前が
  分からなくなったとき（他のアプリで名前を変えた等）のため、行の証拠番号（label）も覚えておき、
  同じ証拠番号のファイルに付け直す。
- PDF は reportlab で作る（第9章。既に使っている部品）。字は明朝体の TrueType を PDF に埋め込む。
- 作成年月日・作成者は、推測で埋めない（PDF の作成日時は電子化した日であり、準備の手引27頁は、
  もとの資料の作成年月日・作成者を書くとしているため）。
- 完全ローカル動作（設計原則1）：フォントは OS のフォルダのものを使い、外部から取らない。
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

from va_mints.errors import reason

DATA_DIRNAME = "証拠説明書データ"  # 入力を置くフォルダ（対象フォルダの中。読み込みの対象にしない）
DATA_NAME = "証拠説明書.json"
_VERSION = 1

# 原本・写しの別（準備の手引27頁の4つの類型）
CATEGORY_NONE = ""
CATEGORY_ORIGINAL = "original"  # ① 紙媒体・原本確認が必要 → 標目の2行目に「原本」
CATEGORY_DIGITIZED = "digitized"  # ② 紙を電子化して提出 → 備考に「紙を電子化」
CATEGORY_COPY = "copy"  # ③ 紙のまま書証の写しを出す → 標目の2行目に「写し」
CATEGORY_DATA = "data"  # ④ もともとデータ → 何も書かない
CATEGORIES = (CATEGORY_NONE, CATEGORY_ORIGINAL, CATEGORY_DIGITIZED, CATEGORY_COPY, CATEGORY_DATA)
CATEGORY_LABELS = {
    CATEGORY_NONE: "選んでいない",
    CATEGORY_ORIGINAL: "原本（期日に原本を持参する書証）",
    CATEGORY_DIGITIZED: "紙を電子化（電磁的記録として提出）",
    CATEGORY_COPY: "写し（紙の写しを書証として提出）",
    CATEGORY_DATA: "もともとデータ（電磁的記録として提出）",
}
TAIL_ORIGINAL = "原本"
TAIL_COPY = "写し"
DIGITIZED_NOTE = "紙を電子化"
NOTE_LINE = (
    "※「標目」欄に原本・写しの記載がないものは、民事訴訟法２３１条の２の"
    "電磁的記録として提出するものである。"
)

# 表題の欄（表の上）。テンプレート（VA証拠説明書 Ver120）と同じ項目。
HEADER_FIELDS = (
    ("court", "裁判所"),
    ("case_number", "事件番号"),
    ("number", "証拠説明書の番号"),
    ("date", "提出の日"),
    ("party", "提出者の別"),
    ("name", "提出者の名前"),
)
ROW_TEXT_FIELDS = ("date", "author", "purpose", "remarks")


@dataclass
class Header:
    court: str = ""  # 例：東京地方裁判所
    case_number: str = ""  # 例：令和8年（ワ）第123号
    number: str = ""  # 証拠説明書の番号（「証拠説明書（2）」の「2」）
    date: str = ""  # 提出の日（例：令和8年11月18日）
    party: str = ""  # 提出者の別（原告・被告など）。空なら証拠番号の符号から決める
    name: str = ""  # 提出者の名前（例：原告訴訟代理人弁護士　甲野太郎）


@dataclass
class RowData:
    """1行分の入力。title は、枝番の行で、ファイル名の標目と違う標目にしたいとき（それ以外は None）。"""

    title: str | None = None
    category: str = CATEGORY_NONE
    date: str = ""
    author: str = ""
    purpose: str = ""
    remarks: str = ""
    span: bool = False  # 標目を、作成年月日・作成者の欄までまたがせる
    label: str = ""  # 最後に見た証拠番号（名前が分からなくなったときに、付け直すため）

    def is_blank(self) -> bool:
        return self == RowData(label=self.label)


@dataclass(frozen=True)
class Row:
    """PDF に書く1行。"""

    label: str  # 号証（例：甲001、甲005-2）
    title: str  # 標目（原本・写しの語を除いたもの）
    category: str = CATEGORY_NONE
    date: str = ""
    author: str = ""
    purpose: str = ""
    remarks: str = ""
    span: bool = False

    @property
    def title_tail(self) -> str:
        return {CATEGORY_ORIGINAL: TAIL_ORIGINAL, CATEGORY_COPY: TAIL_COPY}.get(self.category, "")

    @property
    def remarks_text(self) -> str:
        if self.category != CATEGORY_DIGITIZED:
            return self.remarks
        return DIGITIZED_NOTE + (f"\n{self.remarks}" if self.remarks else "")

    @property
    def spans(self) -> bool:
        """標目を3欄にまたがせるか（作成年月日・作成者がともに空のときだけ）。"""
        return self.span and not self.date.strip() and not self.author.strip()


def pdf_name(header: Header) -> str:
    number = header.number.strip()
    return f"証拠説明書（{number}）.pdf" if number else "証拠説明書.pdf"


def default_party(labels) -> str:
    """提出者の別の既定：証拠番号の符号から（甲＝原告、乙＝被告）。混じっていれば空。"""
    symbols = {label[:1] for label in labels if label}
    if symbols == {"甲"}:
        return "原告"
    if symbols == {"乙"}:
        return "被告"
    return ""


def split_title(title: str, category: str) -> tuple[str, str]:
    """ファイル名の標目を、（原本・写しの語を除いた標目、類型）に分ける。

    末尾の「原本」は ① とみなす。末尾の「写し」は、標目の一部のこともある（「住民票の写し」）ため、
    利用者が ③ を選んでいるときだけ、写しの語とみなす。それ以外の類型は、category（入力）のとおり
    （① ③ は、ファイル名に語がなければ「選んでいない」）。
    """
    if title.endswith(TAIL_ORIGINAL) and len(title) > len(TAIL_ORIGINAL):
        return title[: -len(TAIL_ORIGINAL)], CATEGORY_ORIGINAL
    if category == CATEGORY_COPY and title.endswith(TAIL_COPY) and len(title) > len(TAIL_COPY):
        return title[: -len(TAIL_COPY)], CATEGORY_COPY
    if category in (CATEGORY_DIGITIZED, CATEGORY_DATA):
        return title, category
    return title, CATEGORY_NONE


# 全角の英数字（Ａ〜Ｚ・ａ〜ｚ・０〜９）→ 半角。記号（「（」など）は変えない。
_HALFWIDTH_ALNUM = {
    **{0xFF10 + i: 0x30 + i for i in range(10)},
    **{0xFF21 + i: 0x41 + i for i in range(26)},
    **{0xFF41 + i: 0x61 + i for i in range(26)},
}


def halfwidth_alnum(text: str) -> str:
    """英数字を半角にする（準備の手引29頁「ファイル名：…標目のとおり記載（英数字は半角）」）。"""
    return text.translate(_HALFWIDTH_ALNUM)


def title_with_tail(base: str, category: str) -> str:
    """ファイル名に書く標目（① ③ は、末尾に「原本」「写し」を付ける。準備の手引29頁の例
    「甲001 売買契約書原本」）。③ で、標目が既に「写し」で終わっているときは、重ねない。"""
    if category == CATEGORY_ORIGINAL:
        return base + TAIL_ORIGINAL
    if category == CATEGORY_COPY:
        return base if base.endswith(TAIL_COPY) else base + TAIL_COPY
    return base


# ---------------------------------------------------------------------- 保存
def data_path(root: Path) -> Path:
    return root / DATA_DIRNAME / DATA_NAME



@dataclass
class DescriptionStore:
    """証拠説明書の入力（表題と、行ごとの入力）。rows のキーは（ファイルの位置, 何行目）。"""

    header: Header = field(default_factory=Header)
    rows: dict[tuple[str, int], RowData] = field(default_factory=dict)
    last_digest: str = ""  # 最後に作った PDF の中身（digest）。変わっていなければ作り直さない
    # 最後に作った PDF の位置（対象フォルダからの相対位置。「証拠説明書.pdf」「提出用_01/証拠説明書.pdf」）。
    # このツールで作った証拠説明書を見分けるため（同じ名前の、利用者のファイルと区別する）と、番号を
    # 変えて名前が変わったときに、前の PDF を消すため。
    last_pdf: str = ""
    exists: bool = False  # 保存したことがあるか（ないフォルダでは、PDF を作らない）

    @classmethod
    def load(cls, root: Path) -> "DescriptionStore":
        """読めない・壊れているときは ValueError（読み込めないことを画面で知らせる）。"""
        path = data_path(root)
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            header = Header(**{
                k: str(v) for k, v in (data.get("header") or {}).items()
                if k in Header.__dataclass_fields__
            })
            rows: dict[tuple[str, int], RowData] = {}
            for key, positions in (data.get("files") or {}).items():
                for position, row in positions.items():
                    values = {k: v for k, v in row.items() if k in RowData.__dataclass_fields__}
                    item = RowData(**values)
                    if item.category not in CATEGORIES:
                        item.category = CATEGORY_NONE
                    rows[(key, int(position))] = item
            return cls(header, rows, str(data.get("last_digest", "")), str(data.get("last_pdf", "")), True)
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            # 部品の英語の文・ファイルの場所は出さない（標準 02「メッセージとエラー」）。元の例外は __cause__ に残る
            why = reason(exc) if isinstance(exc, OSError) else "中身が壊れている可能性があります"
            raise ValueError(f"{DATA_NAME} を読めません。{why}") from exc

    def to_json(self) -> dict:
        files: dict[str, dict[str, dict]] = {}
        for (key, position), row in sorted(self.rows.items()):
            if row.is_blank() and not row.label:
                continue
            files.setdefault(key, {})[str(position)] = dataclasses.asdict(row)
        return {
            "version": _VERSION,
            "header": dataclasses.asdict(self.header),
            "files": files,
            "last_digest": self.last_digest,
            "last_pdf": self.last_pdf,
        }

    def save(self, root: Path) -> None:
        """保存する（書きかけで壊れないよう、一時ファイルに書いてから置き換える）。"""
        path = data_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self.to_json(), ensure_ascii=False, indent=1)
        fd, tmp = tempfile.mkstemp(prefix="desc_", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        self.exists = True

    def rekey(self, mapping: dict[tuple[str, int], tuple[str, int]], dropped: set[str] = frozenset()) -> None:
        """行を付け替える（更新・［元のファイルに戻す］の後）。mapping にない行は、そのまま。
        dropped：消えたファイル（結合の材料など）の行は捨てる。"""
        moved: dict[tuple[str, int], RowData] = {}
        rest: dict[tuple[str, int], RowData] = {}
        for key, row in self.rows.items():
            if key in mapping:
                moved[mapping[key]] = row
            elif key[0] not in dropped:
                rest[key] = row
        rest.update(moved)  # 付け替えた行を優先する
        self.rows = rest


def file_key(root: Path, path: Path) -> str:
    """行を覚えるときの、ファイルの位置（対象フォルダからの相対位置。外のファイルは、そのまま）。"""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def rekey_after_restore(root: Path, entries) -> None:
    """［元のファイルに戻す］の後：行を、戻した後のファイルに付け替えて保存する。entries は、
    戻す前の更新記録（updater.ManifestEntry の並び）。枝番ファイル結合で作ったファイルの n 行目は、
    結合する前の n 番目のファイルへ。証拠説明書の PDF が「提出用_NN」にあれば、直下に戻す
    （証拠のファイルが直下に戻るため）。入力のないフォルダでは何もしない。"""
    from va_mints.processors.updater import APPLIED_MERGE, is_batch_dir_name

    try:
        store = DescriptionStore.load(root)
    except ValueError:
        return  # 読めない入力は、触らない
    if not store.exists:
        return
    mapping: dict[tuple[str, int], tuple[str, int]] = {}
    for entry in entries:
        current = entry.current
        if entry.originals and APPLIED_MERGE in entry.applied and len(entry.originals) > 1:
            for i, original in enumerate(entry.originals, start=1):
                mapping[(current, i)] = (original, 1)
        elif len(entry.originals) == 1 or (entry.moved_from and not entry.originals):
            target = entry.originals[0] if entry.originals else entry.moved_from
            for key, position in store.rows:
                if key == current:
                    mapping[(key, position)] = (target, position)
    store.rekey(mapping)
    pdf = root / store.last_pdf if store.last_pdf else None
    if pdf is not None and pdf.exists() and pdf.parent != root and is_batch_dir_name(pdf.parent.name):
        target = root / pdf.name
        if not target.exists():
            os.replace(pdf, target)
            store.last_pdf = target.name
            try:
                pdf.parent.rmdir()  # 空になった「提出用_NN」
            except OSError:
                pass
    store.save(root)


# ---------------------------------------------------------------------- PDF
def digest(header: Header, rows: list[Row]) -> str:
    payload = json.dumps(
        [dataclasses.asdict(header), [dataclasses.asdict(r) for r in rows]], ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# 明朝体（PDF に埋め込む TrueType）。MS 明朝を先頭に置く（VA証拠説明書のテンプレートと同じ字）。
# PostScript の字形の OpenType（Mac のヒラギノなど）は reportlab で埋め込めないため、見つからなければ
# 組み込みの CID フォント（HeiseiMin-W3。埋め込まない）にする。
_FONT_CANDIDATES = (
    (r"C:\Windows\Fonts\msmincho.ttc", 0),
    (r"C:\Windows\Fonts\BIZ-UDMinchoM.ttc", 0),
    (r"C:\Windows\Fonts\yumin.ttf", 0),
    ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 0),
    ("/Library/Fonts/Arial Unicode.ttf", 0),
)
_FONT_NAME = "VAMintsMincho"
_registered_font: str | None = None


def _font() -> str:
    """PDF に使うフォントの名前（初めて呼んだときに登録する）。"""
    global _registered_font
    if _registered_font is not None:
        return _registered_font
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont

    for path, index in _FONT_CANDIDATES:
        if not Path(path).exists():
            continue
        try:
            pdfmetrics.registerFont(TTFont(_FONT_NAME, path, subfontIndex=index))
            _registered_font = _FONT_NAME
            return _registered_font
        except Exception:  # noqa: BLE001 - 読めないフォントは、とばして次を試す
            continue
    pdfmetrics.registerFont(UnicodeCIDFont("HeiseiMin-W3"))
    _registered_font = "HeiseiMin-W3"
    return _registered_font


# 書式（VA証拠説明書 Ver120 のテンプレートにそろえる）：A4 縦、余白（上 19.0mm・右 13.9mm・左 20.8mm。
# 下はページ番号の分を足す）、字は 10 ポイント、表の列の幅（twip）は 882・2297・1003・1184・3401・1187。
_COLUMN_TWIPS = (882, 2297, 1003, 1184, 3401, 1187)
_FONT_SIZE = 10
_LEADING = 14
_HEADINGS = ("号証", "標　目", "作成\n年月日", "作成者", "立証趣旨", "備考")


def _para(text: str, style):
    """欄の文。改行は、行ごとの段落にする（<br/> だと、ちょうど欄の幅で折り返した行の後に、
    空の行が入ることがあるため）。空の行は、全角の空白1字の行にして高さを残す。"""
    from reportlab.platypus import Paragraph

    lines = text.split("\n")
    if len(lines) == 1:
        return Paragraph(escape(text), style)
    return [Paragraph(escape(line) or "　", style) for line in lines]


_PAD_X, _PAD_TOP, _PAD_BOTTOM = 3, 2, 3


def _flowables(cell) -> list:
    return cell if isinstance(cell, list) else [cell]


def _row_height(cells, widths) -> float:
    """行の高さ（欄の中身を、その幅で折り返したときの、いちばん高い欄＋上下の余白）。"""
    tallest = 0.0
    for cell, w in zip(cells, widths):
        inner = w - 2 * _PAD_X
        tallest = max(tallest, sum(f.wrap(inner, 1e6)[1] for f in _flowables(cell)))
    return tallest + _PAD_TOP + _PAD_BOTTOM


def _split_cell(cell, width: float, height: float) -> list[list]:
    """欄の中身を、高さ height ごとの組に分ける（段落は、行の切れ目で分ける）。"""
    chunks: list[list] = []
    current: list = []
    room = height
    queue = list(_flowables(cell))
    while queue:
        f = queue.pop(0)
        h = f.wrap(width, 1e6)[1]
        if h <= room:
            current.append(f)
            room -= h
            continue
        parts = f.split(width, room) if room > 0 else []
        if len(parts) >= 2:
            current.append(parts[0])
            queue[:0] = parts[1:]
        elif not current:
            current.append(f)  # 1行も入らない（起こらないはず）：はみ出しても置く
        else:
            queue.insert(0, f)
        chunks.append(current)
        current, room = [], height
    if current or not chunks:
        chunks.append(current)
    return chunks


def _split_row(cells, widths, limit: float) -> list[list]:
    """1ページに収まらない行を、ページの高さごとの行に分ける（収まる行は、そのまま1行）。
    分けた2行目からは、号証の欄を空にする。"""
    if _row_height(cells, widths) <= limit:
        return [cells]
    height = limit - _PAD_TOP - _PAD_BOTTOM
    split = [_split_cell(cell, w - 2 * _PAD_X, height) for cell, w in zip(cells, widths)]
    count = max(len(s) for s in split)
    rows = []
    for k in range(count):
        rows.append([s[k] if k < len(s) else [] for s in split])
    for row in rows[1:]:
        row[0] = []
    return rows


def build_pdf(header: Header, rows: list[Row], out: Path) -> Path:
    """証拠説明書の PDF を out に作る（同じ名前のファイルは置き換える）。"""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle

    font = _font()
    base = ParagraphStyle(
        "va", fontName=font, fontSize=_FONT_SIZE, leading=_LEADING, wordWrap="CJK", alignment=TA_LEFT
    )
    center = ParagraphStyle("va_c", parent=base, alignment=TA_CENTER)
    right = ParagraphStyle("va_r", parent=base, alignment=TA_RIGHT)
    title_style = ParagraphStyle("va_t", parent=center, spaceBefore=12, spaceAfter=12)

    left_m, right_m, top_m, bottom_m = 20.8 * mm, 13.9 * mm, 19.0 * mm, 18.0 * mm
    width = A4[0] - left_m - right_m
    total = sum(_COLUMN_TWIPS)
    col_widths = [width * t / total for t in _COLUMN_TWIPS]

    def _contents():
        story = []

        def add(item) -> None:
            story.extend(item if isinstance(item, list) else [item])

        first = "　".join(s for s in (header.court.strip(), header.case_number.strip()) if s)
        add(_para(first, base))
        number = header.number.strip()
        add(_para("証  拠  説  明  書" + (f"（{number}）" if number else ""), title_style))
        add(_para(header.date.strip(), right))
        party = header.party.strip() or default_party(r.label for r in rows)
        # 提出者の名前の欄は、名前が1行に収まる幅にする（テンプレートの幅 50mm より狭くはしない）。
        name = header.name.strip()
        longest = max((stringWidth(line, font, _FONT_SIZE) for line in name.split("\n")), default=0)
        name_width = min(width - 35 * mm, max(50 * mm, longest + 8))
        who = Table(
            [[_para(party, center), _para(name, base)]],
            colWidths=[35 * mm, name_width], hAlign="RIGHT",
        )
        who.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), font), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                 ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2)]))
        story.append(Spacer(1, 4))
        story.append(who)
        story.append(Spacer(1, 6))
        story.append(_para(NOTE_LINE, base))
        story.append(Spacer(1, 4))

        heading = [_para(h, center) for h in _HEADINGS]
        data = [heading]
        style = [
            ("FONTNAME", (0, 0), (-1, -1), font),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), _PAD_X),
            ("RIGHTPADDING", (0, 0), (-1, -1), _PAD_X),
            ("TOPPADDING", (0, 0), (-1, -1), _PAD_TOP),
            ("BOTTOMPADDING", (0, 0), (-1, -1), _PAD_BOTTOM),
        ]
        # 1行が1ページに収まる高さ（本文の枠の高さから、見出しの行の分を引く）。
        frame_height = A4[1] - top_m - bottom_m - 12  # 12：SimpleDocTemplate の枠の上下の余白
        limit = frame_height - _row_height(heading, col_widths) - 2
        for row in rows:
            title = row.title + (f"\n{row.title_tail}" if row.title_tail else "")
            cells = [
                _para(row.label, center), _para(title, base), _para(row.date, center),
                _para(row.author, base), _para(row.purpose, base), _para(row.remarks_text, base),
            ]
            widths = list(col_widths)
            if row.spans:
                widths[1] = sum(col_widths[1:4])
                cells[2] = cells[3] = []
            # 行の途中では改ページしない。1ページに収まらない行だけ、ページの高さごとに分けた
            # 行にする（reportlab の行の途中の改ページは、同じページに見出しの行を重ねて出すため使わない）。
            for part in _split_row(cells, widths, limit):
                i = len(data)
                if row.spans:
                    style.append(("SPAN", (1, i), (3, i)))
                data.append(part)

        return story, data, style

    def _page_number(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont(font, _FONT_SIZE)
        canvas.drawCentredString(A4[0] / 2, 10 * mm, str(doc.page))
        canvas.restoreState()

    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="desc_", suffix=".pdf", dir=out.parent)
    os.close(fd)
    try:
        story, data, style = _contents()
        table = Table(data, colWidths=col_widths, repeatRows=1)
        table.setStyle(TableStyle(style))
        doc = SimpleDocTemplate(
            tmp, pagesize=A4, leftMargin=left_m, rightMargin=right_m,
            topMargin=top_m, bottomMargin=bottom_m,
            title="", author="", subject="", creator="", producer="",
            initialFontName=font, initialFontSize=_FONT_SIZE,
        )
        # 字はすべて明朝体（埋め込み）で書く。reportlab は、使わない Helvetica をページの資源に
        # 名前だけ載せるが、本文では使わない（2026-11-18 に確かめた）。
        doc.build([*story, table], onFirstPage=_page_number, onLaterPages=_page_number)
        os.replace(tmp, out)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return out
