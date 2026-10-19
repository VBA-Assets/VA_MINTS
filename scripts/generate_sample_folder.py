"""手動テスト用のサンプルフォルダ（samples/）を再生成するスクリプト。

tests/fixtures/generate_fixtures.py はpytest実行のたびに一時フォルダへ
使い捨てで生成する仕組みだが、GUIを手動で試す際に毎回一時フォルダを
探すのは不便なため、A〜D全分類・主要な検出パターンを網羅したサンプルを
プロジェクト直下の samples/ に恒久的に用意する。

実行方法：
    .venv\\Scripts\\python.exe scripts\\generate_sample_folder.py

samples/ 内のファイルはいずれも本スクリプトが合成生成したダミーデータであり、
実際の証拠・準備書面等の内容は一切含まない。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from fixtures.generate_fixtures import (  # noqa: E402
    make_a3_landscape_pdf,
    make_a3_pdf,
    make_a4_pdf,
    make_a4_pdf_no_metadata,
    make_docx_with_metadata,
    make_extension_mismatch_file,
    make_image_with_metadata,
    make_image_without_metadata,
    make_letter_size_pdf,
    make_long_filename_pdf,
    make_password_pdf,
    make_pdf_with_black_box_redaction_failure,
    make_pdf_with_text,
    make_permission_restricted_pdf,
    make_unsupported_file,
    strip_pdf_metadata,
)

SAMPLES_DIR = ROOT / "samples"


def build() -> None:
    # OneDrive同期下ではフォルダ自体の削除がロックされることがあるため、
    # フォルダは残したまま中身のファイルのみ削除する。README.mdは本スクリプトが
    # 生成しない手動管理ファイルのため、削除対象から除外する。
    if SAMPLES_DIR.exists():
        for p in SAMPLES_DIR.iterdir():
            if p.name == "README.md":
                continue
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
    else:
        SAMPLES_DIR.mkdir(parents=True)

    # A：適合済み
    make_a4_pdf_no_metadata(SAMPLES_DIR / "甲001 適合済みPDF.pdf")
    strip_pdf_metadata(make_a3_landscape_pdf(SAMPLES_DIR / "丙001 A3横置きPDF（適合済み）.pdf"))
    make_image_without_metadata(SAMPLES_DIR / "甲005 メタデータなし画像.png")

    # A：証拠番号処理（4.7）の動作確認用。枝番違いの複数ファイル
    # （準備の手引29頁「甲007-1〜3」形式）→ 枝番ファイル結合の確認ダイアログが
    # 表示される。単独の「甲008」→ 右上スタンプのみの確認ダイアログが
    # 表示される。
    make_a4_pdf_no_metadata(SAMPLES_DIR / "甲007-1 領収書.pdf")
    make_a4_pdf_no_metadata(SAMPLES_DIR / "甲007-2 領収書.pdf")
    make_a4_pdf_no_metadata(SAMPLES_DIR / "甲007-3 領収書.pdf")
    make_a4_pdf_no_metadata(SAMPLES_DIR / "甲008 契約書.pdf")

    # B：要修正
    make_password_pdf(SAMPLES_DIR / "甲002 パスワード付きPDF.pdf")
    make_letter_size_pdf(SAMPLES_DIR / "乙001 レターサイズPDF.pdf")
    make_a3_pdf(SAMPLES_DIR / "乙002 A3縦置きPDF（向き不一致）.pdf")
    make_a4_pdf(SAMPLES_DIR / "甲003 メタデータ付きPDF.pdf", with_metadata=True)
    make_image_with_metadata(SAMPLES_DIR / "甲004 メタデータ付き画像.jpg")
    make_long_filename_pdf(SAMPLES_DIR, extra_chars=15)
    make_pdf_with_black_box_redaction_failure(SAMPLES_DIR / "甲006 墨消し不完全PDF.pdf")

    # B：操作制限（印刷・コピー制限）PDFの検出（要件定義書4.6・新規）。
    # 登記情報提供サービスのPDF等を想定（ユーザーパスワードなしで開けるが
    # オーナーパスワードによる権限制限あり）。パスワード検出（has_password）
    # とは別のフィールドで判定されることを確認する。
    make_permission_restricted_pdf(SAMPLES_DIR / "甲009 登記情報PDF（操作制限）.pdf")

    # B：個人情報パターンの検出（要件定義書4.9・新規）。マイナンバー・電話番号・
    # メールアドレス・郵便番号＋住所・銀行口座番号・クレジットカード番号
    # （Luhnチェック用に有効な番号）を1ファイルに集約して確認できるようにする。
    make_pdf_with_text(
        SAMPLES_DIR / "陳述書（個人情報含む）.pdf",
        [
            "陳述書",
            "氏名：山田太郎",
            "マイナンバー：123456789012",
            "電話番号：090-1234-5678",
            "メールアドレス：taro.yamada@example.com",
            "住所：150-0001 東京都渋谷区神宮前1-1-1",
            "振込先口座番号：1234567",
            "カード番号：4111 1111 1111 1111",
        ],
    )

    # A：号証の連番・欠番チェック（要件定義書4.7・新規）の動作確認用。
    # 「乙001」を重複させ、「甲011」で甲010を欠番にする（甲series：001〜009・011。
    # 甲010が欠番として検出される）。いずれも個々のファイルとしては適合済みで
    # あり、classification自体には影響しない（mints提出前レポートの
    # 【号証の連番・欠番チェック】欄でのみ確認できる、書き出し実行後の結果）。
    make_a4_pdf_no_metadata(SAMPLES_DIR / "乙001 追加証拠（重複）.pdf")
    make_a4_pdf_no_metadata(SAMPLES_DIR / "甲011 契約書.pdf")

    # C：要変換
    make_docx_with_metadata(SAMPLES_DIR / "準備書面（案）.docx")

    # D：処理対象外
    make_extension_mismatch_file(SAMPLES_DIR / "拡張子不一致ファイル.pdf")
    make_unsupported_file(SAMPLES_DIR / "非対応形式ファイル.zip")

    print(f"生成完了：{SAMPLES_DIR}")
    for p in sorted(SAMPLES_DIR.iterdir()):
        print(f"  {p.name}")


if __name__ == "__main__":
    build()
