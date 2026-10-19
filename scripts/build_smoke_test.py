"""PyInstallerビルド後の実行ファイルが、reportlab（CIDフォント埋め込み）・
pikepdf（qpdf DLL）・pdfplumber等のリソース依存機能を正しく実行できるかを
検証するためのハーネス。VA_mints.exe自体はGUIのため自動検証が難しいので、
同じ依存関係を持つ最小限のコンソール専用exeとして別途ビルドし、実際に
PDFスタンプ・メタデータ除去・プレビュー生成を実行する。

使い方：
    .venv\\Scripts\\pyinstaller.exe --onefile --name mints_prep_smoketest ^
        --paths src scripts\\build_smoke_test.py
    dist\\mints_prep_smoketest.exe を実行し、末尾に
    "ALL CHECKS PASSED" と表示されることを確認する。
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def main() -> int:
    try:
        import pikepdf
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas

        from mints_prep.config import EvidenceStampConfig
        from mints_prep.processors.evidence_stamp import stamp_pdf
        from mints_prep.processors.metadata_remover import strip_pdf_metadata
        from mints_prep.ui.preview import render_preview
        from mints_prep.models import Classification, FileRecord, FilenameFinding, FormatStatus

        work_dir = Path(sys.executable).resolve().parent / "_smoke_work"
        work_dir.mkdir(exist_ok=True)

        src_pdf = work_dir / "src.pdf"
        c = canvas.Canvas(str(src_pdf), pagesize=A4)
        c.setAuthor("テスト太郎")
        c.drawString(72, 700, "VA mints build smoke test")
        c.showPage()
        c.save()
        print("[1/4] reportlab: generated source PDF ->", src_pdf, "OK")

        stamped_pdf = work_dir / "stamped.pdf"
        cfg = EvidenceStampConfig(font_size_pt=14, margin_top_mm=8, margin_right_mm=8, style="red")
        stamp_pdf(src_pdf, stamped_pdf, "甲001", cfg)
        print("[2/4] evidence_stamp.stamp_pdf (reportlab CID font + pypdf): OK ->", stamped_pdf)

        stripped_pdf = work_dir / "stripped.pdf"
        strip_pdf_metadata(src_pdf, stripped_pdf)
        with pikepdf.open(stripped_pdf) as pdf:
            keys = [str(k) for k in pdf.docinfo.keys()] if pdf.docinfo else []
        print("[3/4] metadata_remover.strip_pdf_metadata (pikepdf/qpdf): OK, remaining keys=", keys)

        record = FileRecord(
            path=stamped_pdf,
            size_bytes=stamped_pdf.stat().st_size,
            format_status=FormatStatus.RECORD_READY,
            detected_format="PDF",
            classification=Classification.A_COMPLIANT,
            filename_finding=FilenameFinding(
                length=len(stamped_pdf.name), exceeds_max_length=False, exceeds_warning_length=False
            ),
        )
        image = render_preview(record)
        print("[4/4] ui.preview.render_preview (pdfplumber rasterize): OK, size=", image.size if image else None)

        print("\nALL CHECKS PASSED")
        return 0
    except Exception:
        print("SMOKE TEST FAILED")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
