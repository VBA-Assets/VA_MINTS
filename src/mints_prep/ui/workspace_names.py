"""ファイル名の変更と、1つのファイルの中の枝番の区切り（Ver300。要件定義書 4.3・4.11）。

画面の選択と更新の計画（ui/workspace.py の Workspace）の一部を、役目ごとに分けたもの。
状態（_drafts・_undo・_files など）は Workspace.__init__ が持ち、このクラスは Workspace に
まぜて（多重継承で）使う。単独では使わない。
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from mints_prep.detectors.filename_detector import check_filename
from mints_prep.models import FileRecord
from mints_prep.naming.evidence_number import match_evidence_number, name_with_evidence
from mints_prep.ui.review_logic import build_review_options


class NamesMixin:
    # ------------------------------------------------------------------ 名前の変更（Ver300）
    def name_of(self, record: FileRecord) -> str:
        """いまの下書きでの名前（変えていなければ、いまのファイル名）。"""
        sel = self._files.get(record.path)
        return sel.renamed if sel is not None and sel.renamed else record.path.name

    def is_renamed(self, record: FileRecord) -> bool:
        sel = self._files.get(record.path)
        return sel is not None and sel.renamed is not None

    def can_rename(self, record: FileRecord) -> bool:
        return self.is_eligible(record)

    def _name_for_divisions(self, record: FileRecord, new_name: str) -> str:
        """枝番の区切りがあるファイルでは、名前の証拠番号の後ろに、区切りで決まる枝番の
        範囲（-1~n）を付け直す（名前を変えて範囲が消えたり、ずれたりしないように）。"""
        name = new_name.strip()
        count = len(self.branch_starts(record))
        return self._name_with_branches(name, count) if count > 1 else name

    def _name_with_branches(self, name: str, count: int) -> str:
        """name の先頭の証拠番号の後ろを、枝番の数 count に合う範囲（例：甲005-1~3。1つなら
        枝番を付けない）にした名前。証拠番号のない名前は、そのまま。"""
        match = match_evidence_number(name, self.config.evidence_number)
        if match is None:
            return name
        base = f"{match.symbol}{match.subletter}{match.number}"
        label = f"{base}-1~{count}" if count > 1 else base
        return name_with_evidence(name, label, self.config.evidence_number)

    def rename_error(self, record: FileRecord, new_name: str, adjust: bool = True) -> str | None:
        """new_name に変えられないときは、その理由（変えられるときは None）。
        adjust：枝番の区切りがあるファイルで、範囲を付け直した名前で調べる（rename と同じ）。"""
        name = new_name.strip()
        if adjust and self.has_divisions(record):
            if match_evidence_number(name, self.config.evidence_number) is None:
                return (
                    "枝番の区切りがあるファイルの名前には、先頭に証拠番号（例：甲005）が要ります。"
                    "証拠番号を外すときは、先に区切りを外してください。"
                )
            name = self._name_for_divisions(record, name)
        if not self.can_rename(record):
            return "このファイルは更新の対象にならないため、名前を変えられません。"
        gs = self.group_for(record)
        if gs is not None and gs.merge:
            return (
                "枝番ファイル結合をするファイルは、名前を変えられません。結合しない場合は、"
                "右のペインの「枝番ファイル結合」のチェックを外してから変えてください。"
            )
        suffix = record.path.suffix
        if not name.lower().endswith(suffix.lower()) or len(name) <= len(suffix):
            return f"名前を入れてください。拡張子（{suffix}）は変えられません。"
        finding = check_filename(name, self.config.filename)
        if finding.forbidden_chars_found:
            chars = " ".join(finding.forbidden_chars_found)
            return f"ファイル名に使えない文字（{chars}）が入っています。"
        if finding.exceeds_max_length:
            return (
                f"ファイル名は {self.config.filename.max_length} 文字以内にしてください"
                f"（いま {finding.length} 文字）。"
            )
        key = name.casefold()
        if key != record.path.name.casefold():
            # 更新の後に置かれるフォルダで調べる（追加したファイルは、対象フォルダ）。
            folder = self._folder_of(record)
            taken = {
                self.name_of(r).casefold()
                for r in self.records
                if r.path != record.path and self._folder_of(r) == folder
            }
            try:
                taken |= {
                    p.name.casefold() for p in folder.iterdir() if p != record.path
                }
            except OSError:
                pass
            if key in taken:
                return "同じフォルダに、同じ名前のファイルがあります。"
        return None

    def rename_warning(self, new_name: str) -> str | None:
        """変えられるが、気をつけたほうがよいこと（JIS X 0213 外の文字の疑い）。"""
        finding = check_filename(new_name.strip(), self.config.filename)
        if finding.non_jis_x0213_chars:
            return (
                f"mints で使えない文字かもしれません（{''.join(finding.non_jis_x0213_chars)}）。"
            )
        return None

    def rename(self, record: FileRecord, new_name: str) -> None:
        """名前を変える（下書き）。変えられないときは ValueError。
        枝番の区切りがあるファイルは、区切りで決まる枝番の範囲を名前に付け直す。"""
        error = self.rename_error(record, new_name)
        if error:
            raise ValueError(error)
        entry = self._rename_entry(record, self._name_for_divisions(record, new_name))
        if entry is None:
            return
        self._undo.append(entry)
        self._redo.clear()
        self._apply_name(record.path, entry[3])

    def _rename_entry(self, record: FileRecord, new_name: str) -> tuple | None:
        """名前を変える履歴の1行（変わらなければ None）。変えられなければ ValueError。
        new_name は、枝番の範囲まで決めた名前（呼び出し側で決める）。"""
        error = self.rename_error(record, new_name, adjust=False)
        if error:
            raise ValueError(error)
        name = new_name.strip()
        before = self._files[record.path].renamed
        after = None if name == record.path.name else name
        if before == after:
            return None
        return ("name", record.path, before, after)

    # ------------------------------------------------------------------ 証拠番号と枝番（Ver300）
    def _evidence_base(self, record: FileRecord) -> str:
        """いまの名前の先頭の証拠番号の、枝番を除いた部分（例：甲005。なければ空）。"""
        match = match_evidence_number(self.name_of(record), self.config.evidence_number)
        return f"{match.symbol}{match.subletter}{match.number}" if match is not None else ""

    def branch_starts(self, record: FileRecord) -> list[int]:
        """枝番が始まるページの位置（いまの下書きの0始まり）。区切りがなければ [0]。"""
        marks = self._divisions.get(record.path)
        if not marks:
            return [0]
        pages = self.pages_of(record)
        return [0] + [i for i, ref in enumerate(pages) if i > 0 and (ref.path, ref.index) in marks]

    def has_divisions(self, record: FileRecord) -> bool:
        return len(self.branch_starts(record)) > 1

    def branch_labels(self, record: FileRecord) -> dict[int, str]:
        """区切りがあるとき、各枝番の最初のページの位置 → 枝番のラベル（例：甲005-2）。"""
        starts = self.branch_starts(record)
        base = self._evidence_base(record)
        if len(starts) <= 1 or not base:
            return {}
        return {pos: f"{base}-{k}" for k, pos in enumerate(starts, start=1)}

    def division_error(self, record: FileRecord, index: int) -> str | None:
        if not self.can_edit_pages(record):
            return "枝番の区切りは、PDF（パスワードのないもの）にだけ付けられます。"
        gs = self.group_for(record)
        if gs is not None and gs.merge:
            return "枝番ファイル結合をするファイルには、区切りを付けられません。"
        if not self._evidence_base(record):
            return (
                "先に、左の一覧でファイル名の先頭に証拠番号（例：甲005）を付けてください"
                "（名前をダブルクリック又は F2 キー）。"
            )
        if index <= 0:
            return "1ページ目は、いつも最初の枝番の始まりです。"
        if index >= self.page_count(record):
            return "そのページはありません。"
        return None

    def toggle_division(self, record: FileRecord, index: int) -> None:
        """index ページ目から次の枝番にする（すでに区切りがあれば外す）。ファイル名の
        枝番の範囲も直す。付けられなければ ValueError。"""
        error = self.division_error(record, index)
        if error:
            raise ValueError(error)
        ref = self.pages_of(record)[index]
        key = (ref.path, ref.index)
        before = self._divisions.get(record.path, frozenset())
        after = before - {key} if key in before else before | {key}
        # 名前（枝番の範囲）を、区切りを変えた後の数で作る。
        self._set_divisions(record.path, after)
        count = len(self.branch_starts(record))
        self._set_divisions(record.path, before)
        new_name = self._name_with_branches(self.name_of(record), count)
        parts = [("divisions", record.path, before, after)]
        rename = self._rename_entry(record, new_name)
        if rename is not None:
            parts.append(rename)
        self._push(parts)

    def _set_divisions(self, path: Path, marks: frozenset) -> None:
        if marks:
            self._divisions[path] = frozenset(marks)
        else:
            self._divisions.pop(path, None)
        self._refresh_stamp_option(path)

    def _refresh_stamp_option(self, path: Path) -> None:
        """区切りがあるときは、スタンプの項目の文を、枝番ごとに押すことが分かる形にする。"""
        sel = self._files.get(path)
        if sel is None:
            return
        record = sel.item.preview_record
        labels = self.branch_labels(record)
        for i, option in enumerate(sel.options):
            if option.key != "stamp":
                continue
            if labels:
                first, last = labels[min(labels)], labels[max(labels)]
                text = f"証拠番号スタンプ：各枝番の最初のページの右上に、その枝番を焼き込み（{first}〜{last}）"
            else:
                text = f"証拠番号スタンプ：1ページ目の右上に「{sel.item.preliminary.stamp_label}」を焼き込み"
            sel.options[i] = dataclasses.replace(option, label=text)

    def _apply_name(self, path: Path, name: str | None) -> None:
        """名前の下書きを入れ替え、その名前に合わせて処理の項目を組み直す。

        名前を変えたファイルは、利用者が名前を決めたので、ファイル名整形の項目は出さない。
        証拠番号スタンプは、新しい名前の先頭の証拠番号で押す（なくなれば項目を出さない）。
        """
        sel = self._files[path]
        record = sel.item.preview_record
        sel.renamed = name
        effective = name or record.path.name
        prelim = dataclasses.replace(
            self._individual_preliminary(record),
            stamp_label=self._stamp_label(record, effective),
        )
        if name is not None:
            prelim = dataclasses.replace(prelim, output_filename=name, filename_sanitized=False)
        sel.item = dataclasses.replace(sel.item, preliminary=prelim)
        applied = self.manifest.applied_for(self.root, path)
        options = build_review_options(sel.item, self.config.filename, applied, self.config.compress)
        if name is not None:
            options = [o for o in options if o.key != "sanitize"]
        sel.options = options
        sel.choices = {
            o.key: sel.choices.get(o.key, o.default_checked) for o in options if o.enabled
        }
        self._refresh_stamp_option(path)
