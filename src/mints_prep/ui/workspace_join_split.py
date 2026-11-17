"""ページの挿入・ファイルの結合・ファイルの分割（Ver300。要件定義書 4.10・4.12）。

画面の選択と更新の計画（ui/workspace.py の Workspace）の一部を、役目ごとに分けたもの。
状態（_drafts・_undo・_files など）は Workspace.__init__ が持ち、このクラスは Workspace に
まぜて（多重継承で）使う。単独では使わない。
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from mints_prep.detectors.filename_detector import check_filename
from mints_prep.models import FileRecord
from mints_prep.naming.evidence_number import match_evidence_number
from mints_prep.processors.images import IMAGE_SUFFIXES, image_to_pdf
from mints_prep.processors.pages import PageRef, all_pages
from mints_prep.ui.review_logic import ReviewItem, build_review_options
from mints_prep.ui.selection import Selection


class JoinSplitMixin:
    # ------------------------------------------------------------------ 挿入と結合（Ver300。4.10・4.12）
    def _material_pdf(self, path: Path) -> Path:
        """挿入・結合の材料の PDF。PDF はそのまま、画像は1ページの PDF にしたもの（一時フォルダ）。
        以前の更新でスタンプなどを付けた画像は、「元のファイル」フォルダの元の画像から作る。"""
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            return path
        if path not in self._image_pdfs:
            source = self._single_original(path) or path
            if self._material_dir is None:
                self._material_dir = self._temp_dir("va_mints_material_")
            dst = self._material_dir / f"{len(self._image_pdfs) + 1:03d}_{path.stem}.pdf"
            image_to_pdf(source, dst)
            self._image_pdfs[path] = dst
        return self._image_pdfs[path]

    def material_pages(self, path: Path) -> list[PageRef]:
        """ファイル（PDF・JPEG・PNG）の全ページを、挿入・結合の材料として返す。
        使えないファイルは ValueError（理由の文）。"""
        suffix = path.suffix.lower()
        if suffix != ".pdf" and suffix not in IMAGE_SUFFIXES:
            raise ValueError(f"PDF と画像（JPEG・PNG）だけを使えます：{path.name}")
        if not path.exists():
            raise ValueError(f"ファイルが見つかりません：{path.name}")
        pdf = self._material_pdf(path)
        try:
            from pypdf import PdfReader

            if PdfReader(str(pdf)).is_encrypted:
                raise ValueError(f"パスワード付きの PDF は使えません：{path.name}")
            refs = all_pages(pdf)
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001 - 壊れた PDF など
            raise ValueError(f"このファイルを読めません：{path.name}") from exc
        if not refs:
            raise ValueError(f"ページがありません：{path.name}")
        return refs

    def insert_pages(self, record: FileRecord, at: int, path: Path) -> list[int]:
        """ファイル path の全ページを、下書きの at の位置（そのページの前。末尾はページ数）に
        入れる。入れたページの位置を返す。入れられなければ ValueError。"""
        if not self.can_edit_pages(record):
            raise ValueError("ページを挿入できるのは、PDF（パスワードのないもの）です。")
        refs = self.material_pages(path)
        pages = self.pages_of(record)
        at = max(0, min(len(pages), at))
        self._set_pages(record, pages[:at] + refs + pages[at:])
        return list(range(at, at + len(refs)))

    def is_absorbed(self, record: FileRecord) -> bool:
        """ほかのファイルに結合して、更新すると消えるファイルか。"""
        return record.path in self._absorbed

    def absorbed_into(self, record: FileRecord) -> list[FileRecord]:
        """このファイルに結合するファイル（読み込みの並び順）。"""
        paths = {p for p, target in self._absorbed.items() if target == record.path}
        return [r for r in self.records if r.path in paths]

    def join_error(self, records: list[FileRecord]) -> str | None:
        if len(records) < 2:
            return "結合するファイルを、左の一覧で2つ以上選んでください（Ctrl・Shift を押しながらクリック）。"
        for r in records:
            name = self.name_of(r) if self.is_eligible(r) else r.path.name
            if not self.is_eligible(r) or self.is_absorbed(r):
                return f"このファイルは更新の対象にならないため、結合できません：{name}"
            if self.is_created(r):
                return (
                    f"分けて作るファイル（まだ更新していないもの）は、結合できません：{name}"
                    "（ページを戻すときは、元に戻す〔Ctrl+Z〕を使ってください）"
                )
            if r.detected_format not in ("PDF", "JPEG", "PNG"):
                return f"PDF と画像（JPEG・PNG）だけを結合できます：{name}"
            if r.detected_format == "PDF" and not self.can_edit_pages(r):
                return f"パスワード付きの PDF は結合できません：{name}"
            gs = self.group_for(r)
            if gs is not None and gs.merge:
                return (
                    f"枝番ファイル結合をするファイルは、ほかのファイルと結合できません：{name}"
                    "（結合する場合は、右のペインの「枝番ファイル結合」のチェックを外してください）"
                )
        if not any(r.detected_format == "PDF" for r in records):
            return "結合するファイルに、PDF を1つ以上入れてください（結合後のファイルは、先頭の PDF の名前になります）。"
        target = self._join_target(records)
        for r in records:
            if r is not target and self.has_divisions(r):
                return f"枝番の区切りのあるファイルは、ほかのファイルに結合できません：{self.name_of(r)}（区切りを外してください）"
        return None

    def _join_target(self, records: list[FileRecord]) -> FileRecord:
        ordered = sorted(records, key=lambda r: self._order.get(r.path, 0))
        return next(r for r in ordered if r.detected_format == "PDF")

    def join(self, records: list[FileRecord]) -> FileRecord:
        """records を、左の一覧の並び順に1つのファイルにする（下書き）。結合先（先頭の PDF）を
        返す。結合先の名前は変えない。結合できなければ ValueError。"""
        error = self.join_error(records)
        if error:
            raise ValueError(error)
        ordered = sorted(records, key=lambda r: self._order.get(r.path, 0))
        target = self._join_target(records)
        pages: list[PageRef] = []
        for r in ordered:
            pages.extend(self.pages_of(r) if r.detected_format == "PDF" else self.material_pages(r.path))
        parts: list[tuple] = [("pages", target.path, self.pages_of(target), pages)]
        for r in ordered:
            if r is target:
                continue
            # r に結合していたファイルも、新しい結合先へ移す。
            for inner in self.absorbed_into(r):
                parts.append(("absorb", inner.path, r.path, target.path))
            parts.append(("absorb", r.path, self._absorbed.get(r.path), target.path))
        self._push(parts)
        return target

    def join_warning(self, record: FileRecord) -> str | None:
        """結合先のファイルに出す注意：別々の証拠を1つのファイルにしようとしているとき
        （準備の手引29頁「枝番がある場合を除いて、1つの証拠について1ファイル」。止めはしない）。"""
        absorbed = self.absorbed_into(record)
        if not absorbed:
            return None
        bases: list[str] = []
        for name in [self.name_of(record)] + [r.path.name for r in absorbed]:
            match = match_evidence_number(name, self.config.evidence_number)
            base = f"{match.symbol}{match.subletter}{match.number}" if match is not None else ""
            if base and base not in bases:
                bases.append(base)
        if len(bases) <= 1:
            return None
        return (
            f"別々の証拠（{'・'.join(bases)}）を1つのファイルにしようとしています。mints では、"
            "枝番がある場合を除いて、1つの証拠を1つのファイルで出します（準備の手引29頁）。"
            "枝番を付けて出すときは、名前の証拠番号を1つにして、［枝番の区切り］で区切ってください。"
        )

    # ------------------------------------------------------------------ 分割（Ver300。4.10）
    def is_created(self, record: FileRecord) -> bool:
        """分割で新しく作る（まだない）ファイルか。"""
        return record.path in self._virtual

    def split_source(self, record: FileRecord) -> FileRecord | None:
        """分割で新しく作るファイルの、分ける前のファイル。"""
        parent = self._split_parent.get(record.path)
        return next((r for r in self.records if r.path == parent), None)

    def split_children(self, record: FileRecord) -> list[FileRecord]:
        """このファイルから分けて新しく作るファイル（左の一覧の並び順）。"""
        return [
            r for r in self.records
            if r.path in self._split_active and self._split_parent.get(r.path) == record.path
        ]

    def split_error(self, record: FileRecord, index: int) -> str | None:
        if not self.can_edit_pages(record):
            return "ファイルを分けられるのは、PDF（パスワードのないもの）です。"
        gs = self.group_for(record)
        if gs is not None and gs.merge:
            return (
                "枝番ファイル結合をするファイルは、分けられません（分ける場合は、右のペインの"
                "「枝番ファイル結合」のチェックを外してください）。"
            )
        if self.has_divisions(record):
            return "枝番の区切りのあるファイルは、分けられません（先に区切りを外してください）。"
        if index <= 0:
            return "1ページ目の前では分けられません。分ける位置の、後ろ側の最初のページを選んでください。"
        if index >= self.page_count(record):
            return "そのページはありません。"
        return None

    def _split_name(self, record: FileRecord) -> str:
        """分けて作るファイルの名前の既定：元の名前に「_2」「_3」…を足したもの（同じ
        フォルダのファイル・下書きの名前と重ならないもの）。"""
        top = record
        while top.path in self._split_parent:
            parent = self.split_source(top)
            if parent is None:
                break
            top = parent
        base = Path(self.name_of(top))
        folder = record.path.parent
        taken = {self.name_of(r).casefold() for r in self.records if r.path.parent == folder}
        taken |= {r.path.name.casefold() for r in self.records if r.path.parent == folder}
        try:
            taken |= {p.name.casefold() for p in folder.iterdir()}
        except OSError:
            pass
        n = 2
        while f"{base.stem}_{n}{base.suffix}".casefold() in taken:
            n += 1
        return f"{base.stem}_{n}{base.suffix}"

    def split(self, record: FileRecord, index: int) -> FileRecord:
        """下書きの index ページ目（0始まり）から後ろを、新しいファイルに分ける。新しい
        ファイル（左の一覧で、このファイルのすぐ後ろに出る）を返す。分けられなければ ValueError。"""
        error = self.split_error(record, index)
        if error:
            raise ValueError(error)
        pages = self.pages_of(record)
        name = self._split_name(record)
        path = record.path.parent / name
        finding = record.pdf_finding
        if finding is not None:
            # 向き不一致のページの番号は、元のファイルのもの。新しいファイルでは使わない。
            finding = dataclasses.replace(finding, orientation_mismatch_pages=())
        new = dataclasses.replace(
            record,
            path=path,
            size_bytes=record.size_bytes * (len(pages) - index) // max(1, len(pages)),
            filename_finding=check_filename(name, self.config.filename),
            pdf_finding=finding,
            exceeds_single_file_limit=False,
            notes=[],
        )
        self._virtual[path] = new
        self._split_parent[path] = record.path
        self._identities[path] = []
        self._drafts.pop(path, None)
        self._divisions.pop(path, None)
        self._eligible_paths.add(path)
        item = ReviewItem(
            preliminary=self._individual_preliminary(new), preview_record=new, batch_dir=path.parent
        )
        options = build_review_options(item, self.config.filename, frozenset(), self.config.compress)
        self._files[path] = Selection(
            item=item,
            options=options,
            choices={o.key: o.default_checked for o in options if o.enabled},
        )
        self._push([
            ("pages", record.path, pages, pages[:index]),
            ("split", path, False, True),
            ("pages", path, [], pages[index:]),
        ])
        return new

    def _rebuild_records(self) -> None:
        """左の一覧の並び（読み込んだファイルと、分けて作るファイルと、追加したファイル）を
        作り直す。分けて作るファイルは、分ける前のファイルのすぐ後ろに置く（後から分けたもの
        ほど前。ページの順）。追加したファイルは、末尾に追加した順に置く。削除したファイルは
        除く（workspace_files.py）。"""
        children: dict[Path, list[Path]] = {}
        for path in self._virtual:
            if path in self._split_active:
                children.setdefault(self._split_parent[path], []).insert(0, path)

        def emit(record: FileRecord, out: list[FileRecord]) -> None:
            out.append(record)
            for child in children.get(record.path, []):
                emit(self._virtual[child], out)

        records: list[FileRecord] = []
        for record in self._base_records:
            if record.path not in self._removed:
                emit(record, records)
        records.extend(self._added[p] for p in self._added_active)
        self.records = records
        base_eligible = {r.path for r in self._base_eligible}
        self.eligible = [
            r for r in records
            if r.path in base_eligible or r.path in self._virtual or r.path in self._added_eligible
        ]
        self._eligible_paths = {r.path for r in self.eligible}
        self._order = {r.path: i for i, r in enumerate(records)}
