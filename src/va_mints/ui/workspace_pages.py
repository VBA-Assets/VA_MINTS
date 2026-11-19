"""ページの下書き（ページの表）と、元に戻す・やり直す（Ver300。要件定義書 4.0・4.10）。

画面の選択と更新の計画（ui/workspace.py の Workspace）の一部を、役目ごとに分けたもの。
状態（_drafts・_undo・_files など）は Workspace.__init__ が持ち、このクラスは Workspace に
まぜて（多重継承で）使う。単独では使わない。
"""

from __future__ import annotations

from pathlib import Path

from va_mints.models import FileRecord
from va_mints.processors.pages import PageRef, all_pages


class PagesMixin:
    # ------------------------------------------------------------------ ページの下書き（Ver300）
    def can_edit_pages(self, record: FileRecord) -> bool:
        """ページを編集できるファイルか（更新の対象の PDF で、パスワードのないもの）。"""
        return (
            self.is_eligible(record)
            and record.detected_format == "PDF"
            and not (record.pdf_finding is not None and record.pdf_finding.has_password)
        )

    def _identity(self, record: FileRecord) -> list[PageRef]:
        if record.path not in self._identities:
            try:
                self._identities[record.path] = all_pages(record.path)
            except Exception:  # noqa: BLE001 - 読めない PDF は編集できないだけ
                self._identities[record.path] = []
        return self._identities[record.path]

    def pages_of(self, record: FileRecord) -> list[PageRef]:
        """いまの下書きのページの表（編集していなければ、全ページをそのまま）。"""
        if record.path in self._drafts:
            return list(self._drafts[record.path])
        return list(self._identity(record)) if self.can_edit_pages(record) else []

    def page_count(self, record: FileRecord) -> int:
        return len(self.pages_of(record))

    def orientation_pages(self, record: FileRecord) -> list[int]:
        """向きの注意（4.6）を出すページ：下書きの何ページ目（0始まり）か。読み込んだときに
        A4 の横向き・A3 の縦向きだったページのうち、まだ 90° 回していないもの（180° は、
        縦横が変わらないので数える）。利用者が回せば、注意は消える（2026-11-17 班長決定。
        右のペインの「向き補正」をやめ、ページの回転にまとめた）。"""
        pf = record.pdf_finding
        if pf is None or not pf.orientation_mismatch_pages or not self.can_edit_pages(record):
            return []
        mismatch = set(pf.orientation_mismatch_pages)
        return [
            i for i, ref in enumerate(self.pages_of(record))
            if ref.path == record.path and ref.index + 1 in mismatch and ref.rotate % 180 == 0
        ]

    def is_edited(self, record: FileRecord) -> bool:
        return record.path in self._drafts and self._drafts[record.path] != self._identity(record)

    @property
    def has_page_edits(self) -> bool:
        return any(self.is_edited(r) for r in self.eligible)

    def _set_pages(self, record: FileRecord, new: list[PageRef]) -> None:
        before = self.pages_of(record)
        if new == before:
            return
        parts = [("pages", record.path, before, list(new))]
        if record.path in self._divisions:
            # 区切りのあるファイル：区切りのページを消したら枝番の数が変わるので、名前の
            # 枝番の範囲も同じ手順の中で直す。
            self._store(record.path, new)
            count = len(self.branch_starts(record))
            self._store(record.path, before)
            new_name = self._name_with_branches(self.name_of(record), count)
            try:
                rename = self._rename_entry(record, new_name)
            except ValueError:
                rename = None
            if rename is not None:
                parts.append(rename)
        self._push(parts)

    def _store(self, path: Path, pages: list[PageRef]) -> None:
        if pages == self._identities.get(path):
            self._drafts.pop(path, None)
        else:
            self._drafts[path] = list(pages)

    def delete_pages(self, record: FileRecord, indices: list[int]) -> None:
        """indices（0始まり）のページを除く。すべてのページは除けない（ValueError）。"""
        pages = self.pages_of(record)
        drop = {i for i in indices if 0 <= i < len(pages)}
        if not drop:
            return
        if len(drop) >= len(pages):
            raise ValueError("すべてのページを削除することはできません。")
        self._set_pages(record, [p for i, p in enumerate(pages) if i not in drop])

    def move_pages(self, record: FileRecord, indices: list[int], target: int) -> list[int]:
        """indices のページを、target（いまの表の位置。そのページの前に入れる。末尾は
        ページ数）へ動かす。動かした後のページの位置を返す。"""
        pages = self.pages_of(record)
        chosen = sorted({i for i in indices if 0 <= i < len(pages)})
        if not chosen:
            return []
        target = max(0, min(len(pages), target))
        moving = [pages[i] for i in chosen]
        rest = [p for i, p in enumerate(pages) if i not in set(chosen)]
        at = target - sum(1 for i in chosen if i < target)
        self._set_pages(record, rest[:at] + moving + rest[at:])
        return list(range(at, at + len(moving)))

    def rotate_pages(self, record: FileRecord, indices: list[int], degrees: int) -> None:
        """indices のページを、degrees 度（時計回り。90・180・270）回す。"""
        pages = self.pages_of(record)
        chosen = {i for i in indices if 0 <= i < len(pages)}
        if not chosen or degrees % 360 == 0:
            return
        self._set_pages(
            record, [p.rotated(degrees) if i in chosen else p for i, p in enumerate(pages)]
        )

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> Path | None:
        """直前のページの編集を取り消す。取り消したファイルの位置を返す（なければ None）。"""
        if not self._undo:
            return None
        entry = self._undo.pop()
        self._redo.append(entry)
        for kind, path, before, _after in reversed(self._parts(entry)):
            self._restore_state(kind, path, before)
        return self._parts(entry)[0][1]

    def redo(self) -> Path | None:
        if not self._redo:
            return None
        entry = self._redo.pop()
        self._undo.append(entry)
        for kind, path, _before, after in self._parts(entry):
            self._restore_state(kind, path, after)
        return self._parts(entry)[0][1]

    @staticmethod
    def _parts(entry) -> list[tuple]:
        """履歴の1手順を、変更の並びにする（"multi" は、いくつかの変更をまとめた1手順）。"""
        return list(entry[1]) if entry[0] == "multi" else [entry]

    def _restore_state(self, kind: str, path: Path, value) -> None:
        if kind == "pages":
            self._store(path, value)
        elif kind == "divisions":
            self._set_divisions(path, value)
        elif kind == "absorb":
            if value is None:
                self._absorbed.pop(path, None)
            else:
                self._absorbed[path] = value
        elif kind == "split":
            if value:
                self._split_active.add(path)
            else:
                self._split_active.discard(path)
            self._rebuild_records()
        elif kind == "add":
            # 左の一覧へのファイルの追加（workspace_files.py）。
            if value and path not in self._added_active:
                self._added_active.append(path)
            elif not value and path in self._added_active:
                self._added_active.remove(path)
            self._rebuild_records()
        elif kind == "remove":
            # 左の一覧からのファイルの削除（workspace_files.py）。
            if value:
                self._removed.add(path)
            else:
                self._removed.discard(path)
            self._rebuild_records()
        else:
            self._apply_name(path, value)

    def _push(self, parts: list[tuple]) -> None:
        """変更を行い、1つの手順として履歴に積む。"""
        if not parts:
            return
        for kind, path, _before, after in parts:
            self._restore_state(kind, path, after)
        self._undo.append(parts[0] if len(parts) == 1 else ("multi", tuple(parts)))
        self._redo.clear()
