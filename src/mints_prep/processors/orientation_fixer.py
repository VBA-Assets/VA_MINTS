"""向き補正（要件定義書4.6の向き不一致検出に対する処理系機能。2026-07-25追加）。

基本形（A4=縦置き・A3=横置き。準備の手引・操作マニュアル別紙4）と不一致の
ページを、PDFの回転属性（/Rotate）の変更のみで補正する。

【原本忠実性（CLAUDE.md設計原則4）】この処理はページの内容（コンテンツ
ストリーム・テキストレイヤー・画像）には一切触れない。/Rotateはスキャナ・
複合機自身が向き指定に使うのと同じ表示属性であり、変更は無損失かつ可逆
（再度逆回転すれば完全に元に戻る）である。ラスタライズ・再エンコードは
行わない。この性質により、チェック専念方針で廃止した「用紙サイズ補正
（拡大縮小配置＝内容の再配置を伴う変換）」とは異なり、処理系機能として
実装できると判断した（docs/03_requirements.md改訂履歴参照）。

この処理はファイルの表示のされ方を変更するため、CLAUDE.md「絶対に譲れない
設計原則」3に従い、呼び出し側（GUI）で必ず実行前にユーザーの確認を得ること
（逐次レビュー画面のチェックボックス＋回転後プレビューによる目視確認）。
内容の向き（文字がどちらを向いているか）はツールには解析できないため、
回転方向（右90°／左90°）の判断はプレビューを見たユーザーに委ねる。
"""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfWriter


def rotate_pdf_pages(
    src: Path, dest: Path, pages: tuple[int, ...], degrees: int
) -> None:
    """指定ページ（1始まり）の/Rotate属性をdegrees（時計回り・90の倍数）だけ
    加算したコピーをdestへ書き出す。元ファイルは変更しない。

    pages: 回転対象のページ番号（1始まり）。向き不一致として検出された
    ページのみを渡す想定（それ以外のページは変更しない）。
    """
    if degrees % 90 != 0:
        raise ValueError(f"回転角は90の倍数である必要があります：{degrees}")
    writer = PdfWriter(clone_from=str(src))
    page_set = set(pages)
    for index, page in enumerate(writer.pages, start=1):
        if index in page_set:
            page.rotate(degrees)
    with open(dest, "wb") as f:
        writer.write(f)
