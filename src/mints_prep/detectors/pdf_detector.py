"""PDF固有の検出（パスワード有無・操作制限・用紙寸法・向き）。

docs/03_requirements.md 4.2・4.6：
- パスワード有無：ユーザーパスワードが必須で開けないPDFを検出
  （報告書第4章1節・確定・一次）
- 操作制限（印刷・コピー制限）PDFの検出【新規・2026-07-20】：ユーザー
  パスワードなしで開けるが、オーナーパスワードによる権限制限（印刷・
  コピー等）が掛かっているPDFを検出する。上記のパスワード有無とは別の
  フィールドで判定・区別する（要件定義書4.6節参照）。
- 用紙寸法：全ページを実測し、A4/A3/それ以外を判定（許容誤差はパラメータ化）
- 向き：ページの縦横比から向きを判定し、内容との整合を確認
  （自動判定は困難なため警告表示にとどめる。要件定義書4.2の記載どおり、
  ここでは「A4は縦置き・A3は横置きが基本形」という公式の向き規律
  〔準備の手引・操作マニュアル別紙4〕からの単純な逸脱検出にとどめ、
  ページ内容自体の向き解析〔OCR等〕は行わない）。

パスワード付きで内容が読めない場合、操作制限・寸法・向きの判定はスキップ
する（暗号化の有無の検出自体は要件を満たしており、無理に復号を試みない）。
"""

from __future__ import annotations

from pathlib import Path

import pikepdf

from mints_prep.config import PdfConfig
from mints_prep.models import PdfFinding

_PT_TO_MM = 25.4 / 72.0


def _judge_size(width_mm: float, height_mm: float, config: PdfConfig) -> str:
    """縦横どちらの向きでもA4/A3寸法と一致するかを、許容誤差付きで判定する。"""
    tol = config.size_tolerance_mm
    candidates = [
        ("A4", config.a4_mm),
        ("A3", config.a3_mm),
    ]
    dims = sorted([width_mm, height_mm])
    for label, size in candidates:
        target = sorted(size)
        if abs(dims[0] - target[0]) <= tol and abs(dims[1] - target[1]) <= tol:
            return label
    return "非対応"


def _judge_orientation(width_mm: float, height_mm: float) -> str:
    return "縦" if height_mm >= width_mm else "横"


def detect_pdf(path: Path, config: PdfConfig) -> PdfFinding:
    try:
        pdf = pikepdf.open(path)
    except pikepdf.PasswordError:
        return PdfFinding(has_password=True)

    try:
        # ここに到達した時点でユーザーパスワードなしに開けている
        # （PasswordErrorはexcept節で別途処理済み）。したがってhas_passwordは
        # 常にFalseとなる。暗号化辞書自体は存在するがユーザーパスワードは
        # 不要という状態は、オーナーパスワードによる権限制限（印刷・コピー等）
        # のみのPDFに該当する（要件定義書4.6「操作制限PDFの検出」）。
        # 要件定義書4.6の定義どおり「ユーザーパスワードなしで開けるが暗号化
        # 辞書が存在する場合」をすべて該当とする（印刷・抽出フラグの制限有無で
        # 絞ると、編集のみ制限のPDFや全権限許可の暗号化PDFを見逃す。mintsの
        # パスワード付き拒否〔エラー#45〕は暗号化の有無で発動しうるため、
        # 暗号化辞書の存在自体を検出対象とする）。
        has_password = False
        has_permission_restriction = bool(pdf.is_encrypted)

        page_sizes_mm: list[tuple[float, float]] = []
        size_judgments: list[str] = []
        orientations: list[str] = []
        mismatch_pages: list[int] = []

        for index, page in enumerate(pdf.pages, start=1):
            mediabox = page.mediabox
            width_pt = float(mediabox[2]) - float(mediabox[0])
            height_pt = float(mediabox[3]) - float(mediabox[1])
            width_mm = abs(width_pt) * _PT_TO_MM
            height_mm = abs(height_pt) * _PT_TO_MM

            # ページの回転属性（/Rotate）が90/270度の場合、閲覧時の見た目は
            # MediaBoxの幅高が入れ替わった状態になる。スキャナ・複合機は
            # 「縦のMediaBox＋Rotate 90」で横向きPDFを生成することが多く、
            # 生の幅高だけで判定すると向き（縦横）を系統的に誤る。寸法判定
            # （_judge_size）はsortにより回転へ頑健だが、向きは補正が必須。
            # pikepdfのpage.rotationはページツリーからの継承も解決した実効値。
            rotate = int(page.rotation) % 360
            if rotate in (90, 270):
                width_mm, height_mm = height_mm, width_mm

            page_sizes_mm.append((width_mm, height_mm))
            judgment = _judge_size(width_mm, height_mm, config)
            size_judgments.append(judgment)
            orientation = _judge_orientation(width_mm, height_mm)
            orientations.append(orientation)

            # 公式の向き規律（A4=縦置き・A3=横置きが基本形）からの逸脱を検出する。
            # 内容そのものの向きは解析しないため、あくまで規律との不一致の警告にとどまる。
            if judgment == "A4" and orientation == "横":
                mismatch_pages.append(index)
            elif judgment == "A3" and orientation == "縦":
                mismatch_pages.append(index)

        return PdfFinding(
            has_password=has_password,
            has_permission_restriction=has_permission_restriction,
            page_sizes_mm=tuple(page_sizes_mm),
            page_size_judgments=tuple(size_judgments),
            page_orientations=tuple(orientations),
            orientation_mismatch_pages=tuple(mismatch_pages),
        )
    finally:
        pdf.close()
