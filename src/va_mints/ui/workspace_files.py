"""左の一覧のファイルの削除と追加（Ver300。2026-11-17 班長指示）。

画面の選択と更新の計画（ui/workspace.py の Workspace）の一部を、役目ごとに分けたもの。
状態（_added・_removed など）は Workspace.__init__ が持ち、このクラスは Workspace に
まぜて（多重継承で）使う。単独では使わない。

どちらも下書きとして持ち、［ファイルを更新］の確認を経てから、フォルダを書き換える
（設計原則3）。更新の前なら、元に戻す（Ctrl+Z）で戻せる。
- 削除：更新すると、ファイルを「元のファイル」フォルダへ移す（updater.remove_file）。
- 追加：エクスプローラーからのドラッグアンドドロップと貼り付け。更新すると、読み込んだ
  フォルダに写す（updater.add_file）。元の場所のファイルには触れない。
"""

from __future__ import annotations

import os
from pathlib import Path

from va_mints.errors import reason
from va_mints.models import FileRecord
from va_mints.processors.output_writer import select_eligible_records
from va_mints.processors.updater import BACKUP_DIRNAME
from va_mints.scanner.walker import is_system_file, scan_file
from va_mints.ui.review_logic import ReviewItem, build_review_options
from va_mints.ui.selection import Selection


def _same(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


class FilesMixin:
    # ------------------------------------------------------------------ 状態
    def is_added(self, record: FileRecord) -> bool:
        """左の一覧へ追加した（まだ対象フォルダにない）ファイルか。"""
        return record.path in self._added

    def is_removed(self, record: FileRecord) -> bool:
        return record.path in self._removed

    @property
    def removed_records(self) -> list[FileRecord]:
        """削除する（更新すると「元のファイル」フォルダへ移す）ファイル。"""
        return [r for r in self._base_records if r.path in self._removed]

    def _folder_of(self, record: FileRecord) -> Path:
        """更新の後にこのファイルが置かれるフォルダ（追加したファイルは、対象フォルダ）。"""
        return self.root if self.is_added(record) else record.path.parent

    # ------------------------------------------------------------------ 削除
    def remove_error(self, record: FileRecord) -> str | None:
        name = record.path.name
        if self.is_description_file(record):
            # 証拠説明書は、下書きにせず、その場でフォルダごとごみ箱に移す（画面の［削除］が、
            # 証拠説明書だけを選んでいるときに行う。workspace_description.delete_description）。
            return (
                f"証拠説明書（{name}）は、ほかのファイルと一緒には削除できません。"
                "証拠説明書だけを選んでから、削除してください。"
            )
        if self.is_created(record):
            return (
                f"分けて作るファイル（まだ更新していないもの）は、削除できません：{name}"
                "（分けるのをやめるときは、元に戻す〔Ctrl+Z〕を使ってください）"
            )
        if self.is_absorbed(record) or self.absorbed_into(record):
            return f"結合するファイルは、削除できません：{name}（結合を元に戻してから削除してください）"
        if self.split_children(record):
            return f"分割したファイルは、削除できません：{name}（分割を元に戻してから削除してください）"
        gs = self.group_for(record)
        if gs is not None and gs.merge:
            return (
                f"枝番ファイル結合をするファイルは、削除できません：{name}（右のペインの"
                "「枝番ファイル結合」のチェックを外してから削除してください）"
            )
        return None

    def remove(self, records: list[FileRecord]) -> None:
        """records を左の一覧から削除する（下書き。1つの手順として、元に戻せる）。
        削除できないファイルがあれば、どれも削除せずに ValueError。"""
        for record in records:
            error = self.remove_error(record)
            if error:
                raise ValueError(error)
        parts: list[tuple] = []
        for record in records:
            if self.is_added(record):
                parts.append(("add", record.path, True, False))  # 追加をやめる
            elif record.path not in self._removed:
                parts.append(("remove", record.path, False, True))
        self._push(parts)

    # ------------------------------------------------------------------ 追加
    def expand_dropped(self, paths: list[Path]) -> list[Path]:
        """ドロップ・貼り付けされたもの（ファイル・フォルダ）を、追加するファイルの並びにする。
        フォルダは、その直下のファイル（名前の順）にする（中のフォルダは読まない）。"""
        files: list[Path] = []
        for path in paths:
            if path.is_dir():
                try:
                    children = sorted(p for p in path.iterdir() if p.is_file())
                except OSError:
                    continue
                files.extend(p for p in children if not is_system_file(p))
            elif path.is_file() and not is_system_file(path):
                files.append(path)
        return files

    def add_error(self, path: Path) -> str | None:
        name = path.name
        backup = self.root / BACKUP_DIRNAME
        if path == backup or backup in path.parents:
            return f"「{BACKUP_DIRNAME}」フォルダのファイルは、追加できません：{name}"
        for record in self.records:
            if record.path == path or (not self.is_created(record) and _same(record.path, path)):
                return f"もう一覧にあります：{name}"
        for path_removed in self._removed:
            if path_removed == path or _same(path_removed, path):
                return f"削除したファイルです：{name}（戻すときは、元に戻す〔Ctrl+Z〕を使ってください）"
        return None

    def add_files(self, paths: list[Path]) -> tuple[list[FileRecord], list[str]]:
        """paths（ファイル）を、左の一覧の末尾に追加する（下書き。1つの手順として、元に戻せる）。
        追加したファイルと、追加できなかったものの理由を返す。"""
        added: list[FileRecord] = []
        errors: list[str] = []
        parts: list[tuple] = []
        for path in paths:
            path = Path(os.path.abspath(path))
            error = self.add_error(path)
            if error:
                errors.append(error)
                continue
            if path not in self._added:
                try:
                    record = scan_file(path, self.config)
                except Exception as exc:  # noqa: BLE001 - 読めないファイルは追加しない
                    errors.append(f"このファイルを読めません：{path.name}（{reason(exc)}）")
                    continue
                self._register_added(record)
            parts.append(("add", path, False, True))
            added.append(self._added[path])
        self._push(parts)
        return added, errors

    def _register_added(self, record: FileRecord) -> None:
        """追加したファイルの選択（右のペインの項目）を作る。読み込んだファイルと同じ既定。"""
        self._added[record.path] = record
        if not select_eligible_records([record]):
            return  # 更新の対象にならない形式（一覧には出す）
        item = ReviewItem(
            preliminary=self._individual_preliminary(record),
            preview_record=record,
            batch_dir=self.root,
        )
        options = build_review_options(item, self.config.filename, frozenset(), self.config.compress)
        self._files[record.path] = Selection(
            item=item,
            options=options,
            choices={o.key: o.default_checked for o in options if o.enabled},
        )
        self._added_eligible.add(record.path)
