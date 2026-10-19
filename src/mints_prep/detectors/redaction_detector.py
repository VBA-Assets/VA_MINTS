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

【既知の限界】次の形態は検出できない：(a) 画像として貼り付けられた黒帯、
(b) rect以外のパス（曲線・線分の組み合わせ）で描かれた塗りつぶし（いずれも
pdfplumberのpage.rectsに現れないため）、(c) 白・薄色の塗り矩形で文字を隠す
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

from mints_prep.models import RedactionFinding

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
