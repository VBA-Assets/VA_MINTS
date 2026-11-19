"""墨消し完全性の検証（要件定義書 4.6「墨消し完全性の検証」）。

根拠：準備の手引39頁「文書等にマスキングをする際の留意点について」
「PDF編集ソフトでマーカー機能を用いるのみでは文字情報が完全に削除されない
ことから、墨消し機能を用いるなどして完全に文字情報を削除したうえで、
削除できていることを提出前に確認する必要がある」。

ページ内の被覆オブジェクト（暗色の塗りつぶし矩形＝黒塗り、ハイライト注釈＝
マーカー）の座標範囲と、下層のテキスト文字の座標とを照合し、被覆下に文字情報
（コピー・検索で読み取れる文字）が残存していないかを判定する。

塗りつぶし矩形は「暗色（黒塗りに使われる色）」のもののみを被覆物とみなす。
色を問わず全ての塗り矩形を対象にすると、Word→PDF変換の表セル網掛け・
見出しの背景色など、ごく普通の文書装飾（塗り矩形の上に文字を描く構造）が
軒並み「墨消し下に文字残存」と誤検出されるため。

【既知の限界】次の形態は、上の照合では検出できない：(a) 画像として貼り付けられた黒帯、
(b) rect以外のパス（曲線・線分の組み合わせ）で描かれた塗りつぶし（いずれも
pdfplumberのpage.rectsに現れないため）。(a)(b) は、2026-11-17 から、ページを画像にして
文字の枠が真っ黒な所を探す検査（_raster_hidden_text。スキャンに OCR をかけた後、画像の上で
黒く塗った場合の、見えない OCR の文字の残り）で補う。(c) 白・薄色の塗り矩形で文字を隠す
失敗墨消し（上記の暗色限定の裏返し。明色まで対象にすると通常の文書装飾が
軒並み誤検出になるため、意図的に対象外とする）。本検証は「検出できたものを
警告する」補助であり、墨消しの完全性を保証するものではない。

検証（検出・警告）のみを行う。除去（選択的除去・ラスタライズ等の防止処理の
実行）はチェック専念方針により廃止した（要件定義書4.6・第8章参照）。本モジュール
はファイルの内容を一切変更しない読み取り専用処理であり、CLAUDE.md「絶対に
譲れない設計原則」3の確認UI原則の対象外である。
"""

from __future__ import annotations

from pathlib import Path

import pdfplumber

from va_mints.models import RedactionFinding

_SNIPPET_MAX_LENGTH = 30


def _center_within(char: dict, x0: float, top: float, x1: float, bottom: float) -> bool:
    cx = (char["x0"] + char["x1"]) / 2
    cy = (char["top"] + char["bottom"]) / 2
    return x0 <= cx <= x1 and top <= cy <= bottom


# 「黒塗り」とみなす塗り色の明度上限（0.0=黒〜1.0=白）。表セルの網掛け
# （薄灰）や白背景を誤って被覆物と判定しないための閾値。濃灰〜黒・濃紺等の
# 「下の文字を隠す意図が明確な暗色」だけを対象にする。
_DARK_FILL_MAX_LUMINANCE = 0.35


def _is_dark_fill(color: object) -> bool:
    """non_stroking_color（塗り色）が黒塗りに使われる暗色かを判定する。

    pdfplumberの色表現はグレースケール（float又は要素1のタプル）・RGB
    （要素3）・CMYK（要素4）が混在する。色情報が取れない場合は、誤検出の
    山を築くよりも安全側（検出しない）に倒す——検出できない形態がある旨は
    モジュールdocstringの【既知の限界】として明示済み。
    """
    if color is None:
        return False
    if isinstance(color, (int, float)):
        return float(color) <= _DARK_FILL_MAX_LUMINANCE
    if isinstance(color, (tuple, list)):
        values = [float(v) for v in color if isinstance(v, (int, float))]
        if len(values) == 1:
            return values[0] <= _DARK_FILL_MAX_LUMINANCE
        if len(values) == 3:
            r, g, b = values
            luminance = 0.299 * r + 0.587 * g + 0.114 * b
            return luminance <= _DARK_FILL_MAX_LUMINANCE
        if len(values) == 4:
            c, m, y, k = values
            # CMYK→簡易輝度（黒版が濃いほど暗い）
            luminance = (1 - k) * (1 - 0.299 * c - 0.587 * m - 0.114 * y)
            return luminance <= _DARK_FILL_MAX_LUMINANCE
    return False


def _covering_boxes(page: "pdfplumber.page.Page") -> list[tuple[float, float, float, float, str]]:
    boxes: list[tuple[float, float, float, float, str]] = []

    for rect in page.rects:
        if rect.get("fill") and _is_dark_fill(rect.get("non_stroking_color")):
            boxes.append((rect["x0"], rect["top"], rect["x1"], rect["bottom"], "塗りつぶし矩形（黒塗り等）"))

    for annot in page.annots:
        subtype = annot.get("data", {}).get("Subtype")
        name = getattr(subtype, "name", None)
        if name == "Highlight":
            boxes.append((annot["x0"], annot["top"], annot["x1"], annot["bottom"], "マーカー（ハイライト注釈）"))

    return boxes


# 画像の上の黒塗り（Ver300。2026-11-17 班長指示「隠した所に OCR の文字が残っていないか」の検査）。
# スキャンして OCR（文字の読み取り）をかけた後に、画像の上で黒く塗って隠すと、塗った下の文字が、
# 見えない OCR の文字として残る（準備の手引39頁の注意と同じ種類の問題）。上の塗りつぶし矩形の
# 照合では、画像に描き込んだ黒は見つけられない（【既知の限界】(a)）。そこで、ページを画像に
# して（ファイルは変えない）、文字の枠の中がほとんど真っ黒な文字が続いている所を探す。
# ふつうの黒い文字は、枠（字の高さいっぱいの枠）の中に白い所があるので、当たらない。
_RASTER_DPI = 72  # 画像にするときの細かさ（36 では、小さい字の白い所がつぶれて当たった）。
_MIN_BOX_PX = 4  # 字の枠がこれより小さい（大きさのない字・ごく小さい字）ものは見ない。
_DARK_PIXEL = 64  # この明るさ（0〜255）より暗い画素を「黒」とみなす。
_DARK_RATIO = 0.95  # 枠の中の「黒」の割合がこれ以上なら、黒く塗られた所の下の文字とみなす。
_MIN_RUN = 3  # 続けてこの字数以上のときだけ知らせる（1・2字の偶然の当たりを避ける）。
# 伏せ字として、わざと書いた記号（見えている文字。下に隠れたものはない）。
_MASK_GLYPHS = set("■□▪▫█▇▆▅▄▃▂▁●○◆◇▲△▼▽★☆※＊*×")


def _raster_hidden_text(path: Path, skip_pages: set[int]) -> list[tuple[int, str]]:
    """画像にしたページで、黒く塗られた所の下にある文字の並び（ページ、文字の例）。"""
    import pypdfium2 as pdfium

    found: list[tuple[int, str]] = []
    scale = _RASTER_DPI / 72
    pdf = pdfium.PdfDocument(str(path))
    try:
        for index in range(len(pdf)):
            if index + 1 in skip_pages:
                continue
            page = pdf[index]
            textpage = page.get_textpage()
            try:
                count = textpage.count_chars()
                if count <= 0:
                    continue
                # 回転の属性は、手元の写しだけで外す（文字の座標と画像の向きをそろえる）。
                if page.get_rotation():
                    page.set_rotation(0)
                left, _bottom, _right, top = page.get_cropbox()
                image = page.render(scale=scale).to_pil().convert("L")
                width, height = image.size
                runs: list[str] = []
                run: list[str] = []

                def flush() -> None:
                    text = "".join(run).strip()
                    if len(text) >= _MIN_RUN:
                        runs.append(text)
                    run.clear()

                for i in range(count):
                    char = textpage.get_text_range(i, 1)
                    if not char.strip() or char in _MASK_GLYPHS:
                        if char.strip():
                            flush()
                        elif run:
                            run.append(" ")
                        continue
                    l, b, r, t = textpage.get_charbox(i, loose=True)
                    x0, x1 = int((l - left) * scale), int((r - left) * scale + 0.999)
                    y0, y1 = int((top - t) * scale), int((top - b) * scale + 0.999)
                    x0, y0 = max(0, x0), max(0, y0)
                    x1, y1 = min(width, x1), min(height, y1)
                    if x1 - x0 < _MIN_BOX_PX or y1 - y0 < _MIN_BOX_PX:
                        flush()
                        continue
                    histogram = image.crop((x0, y0, x1, y1)).histogram()
                    total = sum(histogram)
                    if total and sum(histogram[:_DARK_PIXEL]) / total >= _DARK_RATIO:
                        run.append(char)
                    else:
                        flush()
                flush()
                for text in runs:
                    found.append((index + 1, text))
            finally:
                textpage.close()
                page.close()
    finally:
        pdf.close()
    return found


def detect_redaction_residue(path: Path) -> RedactionFinding | None:
    """墨消し・マーカーの下に文字情報が残存していないかを検証する。

    検証不可（暗号化・破損等で解析に失敗）の場合は None を返す。
    """
    try:
        pages_with_residual: list[int] = []
        details: list[str] = []

        with pdfplumber.open(path) as pdf:
            for page_index, page in enumerate(pdf.pages, start=1):
                boxes = _covering_boxes(page)
                if not boxes:
                    continue

                page_flagged = False
                for x0, top, x1, bottom, kind in boxes:
                    # 単語間のスペースもスニペットの可読性のため保持する
                    # （残存有無の判定自体は空白のみの場合を除外するためstrip後に判定）。
                    covered_text = "".join(
                        ch["text"] for ch in page.chars if _center_within(ch, x0, top, x1, bottom)
                    )
                    covered_text = covered_text.strip()
                    if covered_text:
                        page_flagged = True
                        snippet = covered_text
                        if len(snippet) > _SNIPPET_MAX_LENGTH:
                            snippet = snippet[:_SNIPPET_MAX_LENGTH] + "…"
                        details.append(
                            f"{page_index}ページ目：{kind}の下に文字情報が残存している可能性"
                            f"（例：「{snippet}」）"
                        )

                if page_flagged:
                    pages_with_residual.append(page_index)

        # 画像の上の黒塗りの下の文字（OCR の文字など）。上の照合で見つけたページは、二重に出さない。
        raster_pages: list[int] = []
        try:
            raster = _raster_hidden_text(path, set(pages_with_residual))
        except Exception:  # noqa: BLE001 - 画像にできないときは、この検査だけをとばす
            raster = []
        for page_index, text in raster:
            snippet = text if len(text) <= _SNIPPET_MAX_LENGTH else text[:_SNIPPET_MAX_LENGTH] + "…"
            details.append(
                f"{page_index}ページ目：黒く塗られた所の下に文字情報が残存している可能性"
                f"（スキャンの読み取り〔OCR〕の見えない文字など。例：「{snippet}」）"
            )
            if page_index not in raster_pages:
                raster_pages.append(page_index)
        pages_with_residual = sorted(set(pages_with_residual) | set(raster_pages))

        return RedactionFinding(
            has_residual_text=bool(pages_with_residual),
            pages_with_residual_text=tuple(pages_with_residual),
            details=tuple(details),
        )
    except Exception:
        # パスワード付き・破損PDF等、解析自体に失敗した場合は検証不可として扱う
        # （パスワード検出自体はpdf_detector側で別途警告するため、ここでは
        # 検出不可を示すにとどめる）。
        return None
