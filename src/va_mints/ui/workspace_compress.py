"""圧縮の選択・大きさの見込み・②のプレビュー（Ver300。要件定義書 4.13）。

画面の選択と更新の計画（ui/workspace.py の Workspace）の一部を、役目ごとに分けたもの。
状態（_drafts・_undo・_files など）は Workspace.__init__ が持ち、このクラスは Workspace に
まぜて（多重継承で）使う。単独では使わない。
"""

from __future__ import annotations

from pathlib import Path

from va_mints.models import FileRecord
from va_mints.processors.compress import reduce_image_file, reduce_pdf_images
from va_mints.ui.selection import Selection


class CompressMixin:
    # ------------------------------------------------------------------ 圧縮（Ver300。4.13）
    def reduce_dpi_of(self, sel: Selection) -> int:
        """②画像を軽くするときの解像度（選んだもの。なければ設定ファイルの先頭）。"""
        return sel.reduce_dpi or self.config.compress.reduce_dpi[0]

    def _compress_args(self, sel: Selection) -> dict:
        # pdf_config：用紙を A4 にそろえる（4.15）ときの寸法（apply_review_choices に一緒に渡す）。
        return {
            "reduce_dpi": self.reduce_dpi_of(sel),
            "jpeg_quality": self.config.compress.jpeg_quality,
            "pdf_config": self.config.pdf,
        }

    def compress_choice(self, record: FileRecord) -> tuple[bool, int | None]:
        """いまの選択で、これから行う圧縮：（①をするか、②の解像度又は None）。
        以前の更新で済ませたもの（チェックを入れたまま・同じ解像度）は、行わないので含めない。"""
        sel = self.pane_view(record).selection
        if sel is None:
            return False, None
        lossless = sel.choices.get("compress", False) and not sel.compressed
        reduce = self.reduce_dpi_of(sel) if sel.choices.get("reduce", False) else None
        if reduce is not None and reduce == sel.reduced_dpi:
            reduce = None
        return lossless, reduce

    def _compression_undone(self, sel: Selection) -> bool:
        """以前の更新で済ませた圧縮のチェックを外した（又は②の解像度を変えた）か。そのときは、
        元のファイルから作り直す（いまのファイルの画像は、もう粗くなっているため）。"""
        if sel.compressed and not sel.choices.get("compress", False):
            return True
        if sel.reduced_dpi is not None:
            if not sel.choices.get("reduce", False) or self.reduce_dpi_of(sel) != sel.reduced_dpi:
                return True
        return False

    def estimate_key(self, record: FileRecord) -> tuple | None:
        """大きさの見込みを計算する対象（ファイル、①、②の解像度）。圧縮しないときは None。
        見込みは、いまのファイルに、選んだ圧縮だけを行った場合のもの（ページの編集・
        スタンプなどは含めない）。"""
        lossless, reduce = self.compress_choice(record)
        if not (lossless or reduce) or self.is_created(record) or self.pane_view(record).is_group:
            return None
        sel = self.pane_view(record).selection
        if sel.compressed or sel.reduced_dpi is not None:
            # 済ませた圧縮を変えると元のファイルから作り直すので、いまのファイルからの見込みは
            # 当てにならない。出さない。
            return None
        return (record.path, record.detected_format, lossless, reduce, self.config.compress.jpeg_quality)

    def estimate(self, record: FileRecord) -> int | None:
        key = self.estimate_key(record)
        return self._estimates.get(key) if key is not None else None

    def set_estimate(self, key: tuple, size: int) -> None:
        self._estimates[key] = size

    def reduced_preview(self, path: Path, index: int, fmt: str, dpi: int) -> tuple[Path, int] | None:
        """②画像を軽くした姿のプレビュー用に、path の index ページ目（画像はそのもの）を
        軽くした一時ファイル（ファイル、そのページ）。作れなければ None。"""
        key = (path, index, fmt, dpi, self.config.compress.jpeg_quality)
        if key not in self._reduced_previews:
            result = None
            try:
                folder = self._temp_dir("va_mints_reduce_")
                if fmt == "PDF":
                    from pypdf import PdfReader, PdfWriter

                    writer = PdfWriter()
                    writer.add_page(PdfReader(str(path)).pages[index])
                    one = folder / "page.pdf"
                    with open(one, "wb") as f:
                        writer.write(f)
                    out = folder / "reduced.pdf"
                    reduce_pdf_images(one, out, dpi, self.config.compress.jpeg_quality)
                    result = (out, 0)
                else:
                    out = folder / f"reduced{path.suffix}"
                    reduce_image_file(path, out, fmt, dpi, self.config.compress.jpeg_quality)
                    result = (out, 0)
            except Exception:  # noqa: BLE001 - プレビューを出せないだけ（元の姿を描く）
                result = None
            self._reduced_previews[key] = result
        return self._reduced_previews[key]

    def _image_original(self, sel: Selection) -> Path | None:
        """画像（JPEG・PNG）の、「元のファイル」フォルダの元の画像（なければ None）。"""
        record = sel.item.preliminary.record
        if record.detected_format not in ("JPEG", "PNG"):
            return None
        return self._single_original(record.path)
