"""対象フォルダの中のファイルを、その場で更新する（要件定義書 第3章「処理フロー」⑥。Ver200）。

Ver170 までは、処理したファイルを別の出力フォルダへ書き出していた。Ver200 からは、
対象フォルダの中のファイルそのものを更新し、更新する前のファイルを、同じ
フォルダの中の「元のファイル」フォルダに残す（2026-11-16 班長指示）。

「元のファイル」フォルダの中身：
- 更新する前のファイル（対象フォルダからの相対位置のまま置く）。同じ位置に
  同じ名前のファイルが既にあれば、保存しない（最初の更新の前の姿を残すため。
  2回目以降の更新で、途中の姿に置き換えない）。
- 更新記録.json … どのファイルに何をしたか・元の名前は何か、の記録。次のために使う。
  ・読み込み直したとき、証拠番号スタンプを付けたファイルを、スタンプにチェックの
    入った状態で表示する（ファイル名は「甲001…」のままで、付けたかどうかは記録で
    しか分からない。もう一度付けて二重にすることも防ぐ）。
  ・そのチェックを外して更新したとき、スタンプを消す（remove_stamp。更新前の
    ファイルから、スタンプ以外の処理だけをやり直して作り直す）。
  ・［元のファイルに戻す］で、名前を変えたファイル・結合したファイル・「提出用_NN」へ
    移したファイルを、元の名前・元の位置に戻す。
- 更新中 … 更新の途中だけ使うフォルダ（処理結果をここに作ってから、置き換える）。

この処理はファイルの内容を変更するため、CLAUDE.md「絶対に譲れない設計原則」3に
従い、呼び出し側（GUI）で必ず実行前にユーザーの確認を得ること。本モジュール自体は
確認済みの実行を担うのみ。完全ローカル動作（原則1）のとおり、外部通信はしない。
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import shutil
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path

from va_mints.config import EvidenceStampConfig, FilenameConfig
from va_mints.errors import reason
from va_mints.processors.output_writer import PlannedCopy, copy_to_output, make_deduped_name
from va_mints.processors.pages import PageRef, all_pages

BACKUP_DIRNAME = "元のファイル"
MANIFEST_FILENAME = "更新記録.json"
# 更新の途中だけ使うフォルダ。2026-11-17 に「作業中」から改めた（班長指示。何をしている
# フォルダかが名前で分かるように）。前の名前の残りも片付ける。
WORK_DIRNAME = "更新中"
_OLD_WORK_DIRNAMES = ("作業中",)
# ページの挿入（Ver300）で使った、なくならないファイル（左の一覧のほかのファイル・
# フォルダの外のファイル・画像から作った PDF）の写しを置くフォルダ（「元のファイル」の中）。
# 元のファイルではないので、［元のファイルに戻す］では戻さず、消す。
MATERIALS_DIRNAME = "材料"
# 200MB分割の提出回ごとのフォルダ（batch_planner.Batch.folder_name と同じ形）。
_BATCH_DIR_PATTERN = re.compile(r"^提出用_\d{2,}$")

# 更新記録の applied に入れる、処理の名前（ui/review_logic.py の ReviewOption.key と同じ）。
APPLIED_SANITIZE = "sanitize"
APPLIED_STRIP_METADATA = "strip_metadata"
APPLIED_STAMP = "stamp"
APPLIED_MERGE = "merge"
# ページの編集（削除・挿入・並べ替え・回転。Ver300）。更新記録の pages に、更新後の
# ファイルの各ページが、どの元のファイルの何ページ目からできているかを書く。
APPLIED_PAGES = "pages"
# ファイル名の変更（利用者が左のペインで変えたもの。Ver300。要件定義書 4.3）。名前だけを
# 変えるときは、内容に触れないので、控えを作らずに移し（「提出用_NN」への仕分けと同じ）、
# 元の名前を記録する。
APPLIED_RENAME = "rename"
# ファイルの結合（利用者が左の一覧で選んで結合したもの。Ver300。要件定義書 4.10）。
# 枝番ファイル結合（APPLIED_MERGE。枝番ごとのスタンプを押す）とは分けて記録する。
APPLIED_JOIN = "join"
# ファイルの分割（Ver300。要件定義書 4.10）。分ける前のファイルと、分けて作ったファイルの両方に付ける。
APPLIED_SPLIT = "split"
# 圧縮（Ver300。要件定義書 4.13）。①画質を損なわない圧縮、②画像を軽くする（解像度は
# ManifestEntry.reduce_dpi）。
APPLIED_COMPRESS = "compress"
APPLIED_REDUCE = "reduce_images"
# 用紙を A4 にそろえる（4.15）・付箋・コメント・添付ファイルを取り除く（4.16）。Ver300。
# 元のファイルから作り直すとき（ページの編集・スタンプの消去など）にも、やり直す。
APPLIED_FIT_A4 = "fit_a4"
APPLIED_STRIP_HIDDEN = "strip_hidden"
# applied_for が返す集合に足す印：利用者が、このファイルに証拠番号スタンプを
# 付けないと決めた（チェックを外して更新した・付けたスタンプを消した）。
STAMP_DECLINED = "stamp_declined"
# 更新の計画の中だけで使う名前（更新記録の applied には入らない）：証拠番号スタンプの消去。
ACTION_UNSTAMP = "unstamp"
# 同じく：済ませた圧縮のチェックを外した（解像度を変えた）ので、元のファイルから作り直す。
ACTION_REBUILD = "rebuild"
# 左の一覧から削除したファイル（Ver300。2026-11-17 班長指示）。「元のファイル」フォルダへ
# 移し、［元のファイルに戻す］で戻す。記録の current は、REMOVED_DIRNAME の下の、ない位置を
# 指す（同じ名前のファイルを後から置いても、記録がぶつからないように）。
APPLIED_REMOVE = "remove"
REMOVED_DIRNAME = "削除したファイル"
# 左の一覧へ追加したファイル（Ver300。ドラッグアンドドロップ・貼り付け）。対象フォルダに
# 写したもの。［元のファイルに戻す］では、写したファイル（と、その控え）を消す。
APPLIED_ADD = "add"


def backup_dir(root: Path) -> Path:
    return root / BACKUP_DIRNAME


def _fresh_work_dir(backup: Path) -> Path:
    """更新の途中だけ使うフォルダ（「元のファイル\\更新中」）の位置。前回の更新が途中で止まった
    残りと、前の名前（「作業中」）のフォルダの残りは消す。作るのは呼び出し側。"""
    for name in (WORK_DIRNAME, *_OLD_WORK_DIRNAMES):
        if (backup / name).exists():
            shutil.rmtree(backup / name)
    return backup / WORK_DIRNAME


def is_batch_dir_name(name: str) -> bool:
    return bool(_BATCH_DIR_PATTERN.match(name))


def _rel(root: Path, path: Path) -> str:
    """root からの相対位置（区切りは「/」。記録を OS によらない形にする）。"""
    return path.relative_to(root).as_posix()


def _key(rel: str) -> str:
    # Windows のファイル名は大文字小文字を区別しないため、照合は casefold で行う。
    return rel.casefold()


@dataclass
class ManifestEntry:
    """更新したファイル1つ分の記録。"""

    current: str  # いまのファイルの位置（root からの相対位置）
    # 「元のファイル」フォルダに残した、更新前のファイルの位置（元の相対位置と同じ）。
    # 結合したファイルは、結合前のファイルの数だけある。
    originals: list[str] = field(default_factory=list)
    # 内容を変えずに「提出用_NN」へ移しただけのファイルの、元の位置（控えは作らない）。
    moved_from: str | None = None
    applied: list[str] = field(default_factory=list)
    # ページの表（Ver300。ページを編集して作ったとき）：更新後のファイルの各ページが、
    # 「元のファイル」フォルダのどのファイル（originals の相対位置）の何ページ目
    # （0始まり）を、何度回したものか。[相対位置, ページ, 回転] の並び。スタンプは
    # 含まない（スタンプのない姿を作り直すとき・ページを編集し直すときの元になる）。
    pages: list[list] | None = None
    # ページの挿入に使った材料の写し（MATERIALS_DIRNAME の中。「元のファイル」フォルダ
    # からの相対位置）。pages から参照する。［元のファイルに戻す］では戻さずに消す。
    materials: list[str] = field(default_factory=list)
    # 分割で新しく作ったファイル（Ver300）。更新の前にはなかったので、［元のファイルに戻す］
    # では消す（originals は空。pages は材料の写しを指す）。
    created: bool = False
    # ②画像を軽くする（applied に reduce_images があるとき）で使った解像度。ページを
    # 編集し直すとき（元のファイルから作り直す）に、同じ解像度でやり直すために使う。
    reduce_dpi: int | None = None
    # 左の一覧へ追加して、対象フォルダに写したファイル（Ver300）。［元のファイルに戻す］
    # では、いまのファイルと控え（originals）を消す（追加する前には、なかったので）。
    added: bool = False
    # 左の一覧から削除したファイル（Ver300）。いまのファイルはない（current は、ない位置）。
    # ［元のファイルに戻す］では、originals を元の位置に戻す。
    removed: bool = False


class Manifest:
    """更新記録（「元のファイル」フォルダの 更新記録.json）。"""

    def __init__(
        self, entries: list[ManifestEntry] | None = None, stamp_declined: list[str] | None = None
    ) -> None:
        self._entries: dict[str, ManifestEntry] = {_key(e.current): e for e in entries or []}
        # 証拠番号スタンプを付けないと決めたファイル（root からの相対位置）。
        # 証拠番号のあるPDFは、読み込むとスタンプに自動でチェックが付く。チェックを
        # 外して更新したファイル・付けたスタンプを消したファイルに、読み込み直すたびに
        # また付くと、次の更新で意図せずスタンプが入る。それを防ぐために覚えておく
        # （［元のファイルに戻す］ですべて戻すと、記録ごと消え、チェックは再び自動で付く）。
        self._stamp_declined: dict[str, str] = {_key(r): r for r in stamp_declined or []}

    @property
    def entries(self) -> list[ManifestEntry]:
        return list(self._entries.values())

    def __bool__(self) -> bool:
        """更新したファイルの記録があるか（［元のファイルに戻す］で戻せるものがあるか）。"""
        return bool(self._entries)

    @property
    def is_blank(self) -> bool:
        """残すものが何もないか（記録のファイルを消してよいか）。"""
        return not self._entries and not self._stamp_declined

    def set_stamp_declined(self, rel: str, declined: bool) -> None:
        if declined:
            self._stamp_declined[_key(rel)] = rel
        else:
            self._stamp_declined.pop(_key(rel), None)

    def get(self, rel: str) -> ManifestEntry | None:
        return self._entries.get(_key(rel))

    def pop(self, rel: str) -> ManifestEntry | None:
        return self._entries.pop(_key(rel), None)

    def add(self, entry: ManifestEntry) -> None:
        self._entries[_key(entry.current)] = entry

    def applied_for(self, root: Path, path: Path) -> frozenset[str]:
        """このファイルに、これまでの更新で済ませた処理。"""
        try:
            rel = _rel(root, path)
        except ValueError:
            return frozenset()
        entry = self.get(rel)
        applied = set(entry.applied) if entry is not None else set()
        if _key(rel) in self._stamp_declined:
            applied.add(STAMP_DECLINED)
        return frozenset(applied)

    @staticmethod
    def path(root: Path) -> Path:
        return backup_dir(root) / MANIFEST_FILENAME

    @classmethod
    def load(cls, root: Path) -> "Manifest":
        """記録を読む。なければ空。壊れていて読めないときは ValueError。"""
        path = cls.path(root)
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            entries = [
                ManifestEntry(
                    current=str(e["current"]),
                    originals=[str(o) for o in e.get("originals", [])],
                    moved_from=e.get("moved_from"),
                    applied=[str(a) for a in e.get("applied", [])],
                    pages=e.get("pages"),
                    materials=[str(m) for m in e.get("materials", [])],
                    created=bool(e.get("created", False)),
                    reduce_dpi=e.get("reduce_dpi"),
                    added=bool(e.get("added", False)),
                    removed=bool(e.get("removed", False)),
                )
                for e in data["entries"]
            ]
            declined = [str(r) for r in data.get("stamp_declined", [])]
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ValueError(f"更新記録（{path}）を読めません。") from exc
        return cls(entries, declined)

    def save(self, root: Path) -> None:
        path = self.path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "entries": [dataclasses.asdict(e) for e in self.entries],
            "stamp_declined": list(self._stamp_declined.values()),
        }
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        # 書いた直後の記録を、ウイルス対策ソフトや検索の索引づけが開いていると、Windows では
        # 置き換えが「アクセスが拒否されました」（WinError 5）で失敗することがある
        # （2026-11-16、続けて2件を更新したときに発生）。少し待って、やり直す。
        for attempt in range(20):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.1)


def has_restorable_update(root: Path) -> bool:
    """［元のファイルに戻す］で戻せる更新があるか（更新記録に1件でもあるか）。"""
    try:
        return bool(Manifest.load(root))
    except ValueError:
        return False


def _item_sources(item: PlannedCopy) -> list[Path]:
    """この更新でなくなる（「元のファイル」フォルダへ移る）ファイル。"""
    if item.merge_group is not None:
        return [m.record.path for m in item.merge_group.members]
    if item.created:
        return []  # 分割で新しく作るファイル：なくなるファイルはない
    return [item.record.path, *(p for p in item.absorbed if p != item.record.path)]


def _compress_keys(item: PlannedCopy) -> list[str]:
    """圧縮と、ページの並びを変えない PDF の処理（A4 にそろえる・付箋等の除去）の名前。"""
    keys: list[str] = []
    if item.fit_a4 is not None:
        keys.append(APPLIED_FIT_A4)
    if item.strip_hidden:
        keys.append(APPLIED_STRIP_HIDDEN)
    if item.reduce_dpi is not None:
        keys.append(APPLIED_REDUCE)
    if item.compress_lossless:
        keys.append(APPLIED_COMPRESS)
    return keys


def applied_keys(item: PlannedCopy) -> list[str]:
    """この計画で行う処理の名前（ファイルの内容・名前を変えるもの）。なければ空。"""
    keys: list[str] = []
    renamed = (
        item.merge_group is None
        and not item.created  # 新しく作るファイルの名前は、変更ではない
        and not item.filename_sanitized
        and not item.renamed_for_collision
        and item.output_filename != item.record.path.name
    )
    if renamed:
        keys.append(APPLIED_RENAME)
    if item.pages is not None:
        # ページの表から作る（Ver300）。結合・スタンプ・メタデータ除去も、この表と一緒に行う。
        keys.append(APPLIED_PAGES)
        if item.merge_group is not None:
            keys.append(APPLIED_MERGE)
        if item.absorbed:
            keys.append(APPLIED_JOIN)
        if item.split or item.created:
            keys.append(APPLIED_SPLIT)
        if item.stamps:
            keys.append(APPLIED_STAMP)
        if item.strip_metadata:
            keys.append(APPLIED_STRIP_METADATA)
        if item.filename_sanitized:
            keys.append(APPLIED_SANITIZE)
        keys.extend(_compress_keys(item))
        return keys
    if item.rebuild_from is not None:
        keys.append(ACTION_REBUILD)
    keys.extend(_compress_keys(item))
    if item.merge_group is not None:
        keys.append(APPLIED_MERGE)
        if item.merge_stamp:
            keys.append(APPLIED_STAMP)
    elif item.stamp_label is not None:
        keys.append(APPLIED_STAMP)
    if item.strip_metadata:
        keys.append(APPLIED_STRIP_METADATA)
    if item.filename_sanitized:
        keys.append(APPLIED_SANITIZE)
    return keys


def _backup_current(root: Path, manifest: Manifest, path: Path) -> str:
    """いまのファイル（path）を「元のファイル」フォルダに写し、その相対位置を返す（同じものが
    既にあれば写さない）。「提出用_NN」へ移しただけのファイルは、移す前の位置の名前で写す
    （戻すときに、元の位置へ戻るように）。"""
    rel = _rel(root, path)
    entry = manifest.get(rel)
    original_rel = entry.moved_from if entry is not None and entry.moved_from else rel
    target = backup_dir(root) / original_rel
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    return original_rel


def _was_added(root: Path, manifest: Manifest, sources: list[Path]) -> bool:
    """sources に、左の一覧へ追加して写したファイル（記録の added）があるか。更新し直しても、
    追加したファイルであることを記録に引き継ぐ（［元のファイルに戻す］で消すため）。"""
    for source in sources:
        entry = manifest.get(_rel(root, source))
        if entry is not None and entry.added:
            return True
    return False


def _free_name_for_add(wanted: Path, root: Path, filename_config: FilenameConfig | None = None) -> Path:
    """追加するファイルを置く位置。対象フォルダにも、「元のファイル」フォルダの同じ
    位置にもないもの（控えとぶつからないように）。"""
    def _taken(p: Path) -> bool:
        try:
            twin = backup_dir(root) / _rel(root, p)
        except ValueError:
            twin = None
        return p.exists() or (twin is not None and twin.exists())

    if not _taken(wanted):
        return wanted
    count = 2
    while True:
        candidate = wanted.with_name(make_deduped_name(wanted.name, count, filename_config))
        if not _taken(candidate):
            return candidate
        count += 1


def add_file(
    root: Path, source: Path, wanted: Path, manifest: Manifest,
    filename_config: FilenameConfig | None = None,
) -> Path:
    """左の一覧へ追加したファイル（source。対象フォルダの外にあるもの）を、wanted の
    位置に写し、追加したことを記録する。同じ名前のファイルがあれば、連番を付けて避ける。
    元の場所のファイルには触れない。写した位置を返す。"""
    dest = _free_name_for_add(wanted, root, filename_config)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    manifest.add(ManifestEntry(current=_rel(root, dest), applied=[APPLIED_ADD], added=True))
    manifest.save(root)
    return dest


def remove_file(root: Path, path: Path, manifest: Manifest) -> None:
    """左の一覧から削除したファイル（path）を、「元のファイル」フォルダへ移す（消しはしない）。
    ［元のファイルに戻す］で、元の位置・元の姿に戻せるように記録する。

    - 以前に更新したファイル：元のファイルは控えてあるので、いまのファイルは消し、記録を
      「削除した」ものにする（戻すと、更新する前の姿に戻る）。
    - 追加したファイル・分割で作ったファイル：前にはなかったので、消して記録も消す。
    - それ以外：いまのファイルを控え（「提出用_NN」へ移しただけのものは、移す前の位置で）、
      消して、「削除した」記録を作る。
    """
    rel = _rel(root, path)
    backup = backup_dir(root)
    entry = manifest.get(rel)
    if entry is not None and (entry.added or (entry.created and not entry.originals)):
        manifest.pop(rel)
        manifest.save(root)  # 先に記録を消す（途中で止まっても、ないファイルを指さない）
        path.unlink(missing_ok=True)
        for leftover in [*entry.originals, *entry.materials]:
            (backup / leftover).unlink(missing_ok=True)
        _forget_if_empty(root, manifest)
        return
    if entry is not None and entry.originals:
        originals = list(entry.originals)
        materials = list(entry.materials)
    else:
        originals = [_backup_current(root, manifest, path)]
        materials = []
    n = 1
    while manifest.get(f"{BACKUP_DIRNAME}/{REMOVED_DIRNAME}/{n:03d}/{rel}") is not None:
        n += 1
    manifest.pop(rel)
    manifest.add(
        ManifestEntry(
            current=f"{BACKUP_DIRNAME}/{REMOVED_DIRNAME}/{n:03d}/{rel}",
            originals=originals,
            applied=[APPLIED_REMOVE],
            materials=materials,
            removed=True,
        )
    )
    manifest.save(root)
    path.unlink()


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.exists() and b.exists() and os.path.samefile(a, b)
    except OSError:
        return False


def _free_destination(
    dest: Path, sources: list[Path], filename_config: FilenameConfig | None
) -> tuple[Path, bool]:
    """更新後のファイルを置く位置。同じ名前の別のファイルが既にあれば、連番を付けて避ける
    （mints も同名ファイルの同時アップロードを許さない。別紙2エラー#52）。
    更新するファイル自身（sources）と同じ位置は、置き換えるので空きとみなす。
    戻り値は（位置、連番を付けたか）。"""
    def _taken(p: Path) -> bool:
        return p.exists() and not any(_same_file(p, s) for s in sources)

    if not _taken(dest):
        return dest, False
    count = 2
    candidate = dest.with_name(make_deduped_name(dest.name, count, filename_config))
    while _taken(candidate):
        count += 1
        candidate = dest.with_name(make_deduped_name(dest.name, count, filename_config))
    return candidate, True


@dataclass(frozen=True)
class UpdateOutcome:
    path: Path  # 更新後のファイルの位置
    changed: bool  # 内容・名前を変えたか（False は、そのまま、又は移しただけ）
    moved: bool  # 位置（フォルダ）が変わったか
    renamed_for_collision: bool = False


def update_item(
    root: Path,
    item: PlannedCopy,
    dest_dir: Path,
    manifest: Manifest,
    evidence_stamp_config: EvidenceStampConfig | None = None,
    filename_config: FilenameConfig | None = None,
) -> UpdateOutcome:
    """1件（1ファイル又は1枝番ファイル結合グループ）を更新し、記録を保存する。

    手順（途中で止まっても、更新前のファイルが失われない順にする）：
    1. 処理結果を「元のファイル\\更新中」に作る（この時点では、元のファイルに触れない）。
    2. 更新前のファイルを「元のファイル」に写す（同じ位置に既にあれば写さない）。
    3. 更新前のファイルを消し、処理結果を dest_dir に置く。
    4. 更新記録を保存する。

    処理が1つもなく位置も変わらないときは、何もしない。処理がなく位置だけが変わる
    とき（「提出用_NN」への仕分け）は、控えを作らずに移し、元の位置を記録する。
    """
    sources = _item_sources(item)
    keys = applied_keys(item)
    wanted = dest_dir / item.output_filename
    if item.pages is not None:
        return _update_from_pages(
            root, item, sources, keys, wanted, manifest, evidence_stamp_config, filename_config
        )

    if set(keys) <= {APPLIED_RENAME}:
        # 内容を変えない（名前の変更・「提出用_NN」への仕分けだけ）：控えを作らずに移す。
        source = sources[0]
        if _same_file(wanted, source) or wanted == source:
            return UpdateOutcome(path=source, changed=False, moved=False)
        dest, renamed = _free_destination(wanted, sources, filename_config)
        dest_dir.mkdir(parents=True, exist_ok=True)
        source_rel = _rel(root, source)
        os.replace(source, dest)
        entry = manifest.pop(source_rel) or ManifestEntry(current="", moved_from=source_rel)
        entry.current = _rel(root, dest)
        manifest.add(entry)
        manifest.save(root)
        return UpdateOutcome(path=dest, changed=False, moved=True, renamed_for_collision=renamed)

    backup = backup_dir(root)
    work = _fresh_work_dir(backup)
    work.mkdir(parents=True)
    try:
        copy_to_output([item], work, evidence_stamp_config)
        produced = work / item.output_filename

        originals: list[str] = []
        applied: list[str] = []
        created = None  # 分割で作ったファイルの記録（あれば、作ったことと材料を引き継ぐ）
        reduce_dpi = item.reduce_dpi if APPLIED_REDUCE in keys else None
        # 以前にページの表から作ったファイル（Ver300）に、ページの並びを変えない処理（スタンプ・
        # メタデータ除去・圧縮）だけをするときは、ページの表と材料の写しを引き継ぐ（引き継が
        # ないと、ページを編集し直したときに、元のファイルの全ページから作り直してしまう）。
        previous = manifest.get(_rel(root, sources[0])) if len(sources) == 1 else None
        keep_pages = previous is not None and bool(previous.pages)
        added = _was_added(root, manifest, sources)
        for source in sources:
            source_rel = _rel(root, source)
            entry = manifest.get(source_rel)
            if entry is not None and reduce_dpi is None and item.rebuild_from is None:
                reduce_dpi = entry.reduce_dpi
            if entry is not None and entry.created:
                # 分割で作ったファイル：更新の前にはなかったので、控えない（戻すときは消す）。
                created = entry
                applied.extend(a for a in entry.applied if a not in applied)
                continue
            if entry is not None and entry.originals:
                # 以前の更新で控え済み。途中の姿（いまのファイル）は控えない。
                originals.extend(o for o in entry.originals if o not in originals)
                # 元のファイルから作り直すときは、済ませた処理はこの更新の結果（keys）で決まる
                # （名前の整形だけは、名前に残っているので引き継ぐ）。
                applied.extend(
                    a for a in entry.applied
                    if a not in applied and (item.rebuild_from is None or a == APPLIED_SANITIZE)
                )
                continue
            original_rel = _backup_current(root, manifest, source)
            if original_rel not in originals:
                originals.append(original_rel)

        dest, renamed = _free_destination(wanted, sources, filename_config)
        dest_dir.mkdir(parents=True, exist_ok=True)
        for source in sources:
            manifest.pop(_rel(root, source))
            if not _same_file(source, dest):
                source.unlink()
        os.replace(produced, dest)
        applied.extend(k for k in keys if k not in applied and k != ACTION_REBUILD)
        manifest.add(
            ManifestEntry(
                current=_rel(root, dest),
                originals=originals,
                applied=applied,
                pages=previous.pages if keep_pages else None,
                materials=list(previous.materials) if keep_pages else [],
                created=created is not None,
                reduce_dpi=reduce_dpi if APPLIED_REDUCE in applied else None,
                added=added,
            )
        )
        manifest.save(root)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return UpdateOutcome(
        path=dest,
        changed=True,
        moved=dest.parent != sources[0].parent,
        renamed_for_collision=renamed,
    )


def build_without_stamp(root: Path, entry: ManifestEntry, config, out_dir: Path) -> Path:
    """証拠番号スタンプを付けたファイルの、スタンプのない姿を out_dir に作る。

    スタンプはページの内容に重ねて合成してあり、後から取り除くことはできない。
    そこで、「元のファイル」フォルダの更新前のファイルから、これまでに行った処理の
    うちスタンプ以外（メタデータ除去・枝番ファイル結合・A4 にそろえる等。ファイル名は
    いまの名前）だけをやり直して作る。実際の消去（remove_stamp）と、チェックを
    外したときのプレビュー（ui/workspace.py）の両方が、この同じ関数を使う
    （プレビューと更新の結果が食い違わないように）。

    config は va_mints.config.Config。更新前のファイルがないときは FileNotFoundError。
    """
    # scanner.walker はこのモジュールを取り込むため、ここで取り込む（循環を避ける）。
    from va_mints.processors.evidence_merger import find_branch_merge_groups
    from va_mints.processors.output_writer import _synthetic_merge_record
    from va_mints.scanner.walker import scan_file

    backup = backup_dir(root)
    missing = [o for o in entry.originals if not (backup / o).exists()]
    missing += [m for m in entry.materials if not (backup / m).exists()]
    if missing or not (entry.originals or (entry.created and entry.pages)):
        raise FileNotFoundError(
            f"「{BACKUP_DIRNAME}」フォルダに、更新前のファイルがありません：{Path(entry.current).name}"
        )
    name = Path(entry.current).name
    applied = set(entry.applied)
    strip = APPLIED_STRIP_METADATA in applied
    cleanup = dict(
        fit_a4=config.pdf if APPLIED_FIT_A4 in applied else None,
        strip_hidden=APPLIED_STRIP_HIDDEN in applied,
    )
    pages = entry_pages(root, entry, config.pdf)
    if pages is not None:
        # ページの表から作り直す（Ver300。ページを編集したファイルも、Ver200 の更新も同じ。
        # 分割で作ったファイルは、材料の写しから作る）。
        item = PlannedCopy(
            record=scan_file(pages[0].path, config), output_filename=name,
            strip_metadata=strip, pages=tuple(pages), **cleanup,
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        copy_to_output([item], out_dir, config.evidence_stamp)
        return out_dir / name
    records = [scan_file(backup / o, config) for o in entry.originals]
    if APPLIED_MERGE in applied:
        groups = find_branch_merge_groups(records, config.evidence_number)
        if len(groups) != 1 or len(groups[0].members) != len(records):
            raise ValueError(f"結合する前のファイルを特定できません：{name}")
        item = PlannedCopy(
            record=_synthetic_merge_record(groups[0], config.filename),
            output_filename=name,
            strip_metadata=strip,
            merge_group=groups[0],
            merge_stamp=False,
        )
        if cleanup["fit_a4"] is not None or cleanup["strip_hidden"]:
            # 結合したものへの A4 そろえ・除去は、ページの表から作るときだけ行う（output_writer）。
            item = dataclasses.replace(
                item, merge_group=None, merge_stamp=False,
                pages=tuple(ref for r in records for ref in all_pages(r.path)), **cleanup,
            )
    else:
        if len(records) != 1:
            raise ValueError(f"更新前のファイルを特定できません：{name}")
        item = PlannedCopy(
            record=records[0],
            output_filename=name,
            strip_metadata=strip,
            **cleanup,
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    copy_to_output([item], out_dir, config.evidence_stamp)
    return out_dir / name


def _forget_if_empty(root: Path, manifest: Manifest) -> None:
    """残すものが何もなくなったら、記録のファイルと、空になった「元のファイル」フォルダを消す。"""
    if not manifest.is_blank:
        manifest.save(root)
        return
    Manifest.path(root).unlink(missing_ok=True)
    _remove_empty_dirs(backup_dir(root))


def remove_stamp(root: Path, current: Path, manifest: Manifest, config) -> None:
    """証拠番号スタンプを付けたファイル（current）から、スタンプを消す。

    更新前のファイルから作り直したもの（build_without_stamp）で置き換える。
    更新の後に他のアプリで直した内容は失われるため、呼び出し側（GUI）で必ず
    実行前にユーザーの確認を得ること。

    スタンプのほかに何もしていなかったファイルは、更新前のファイルそのものに戻し、
    控えと記録を片付ける（一度も更新していないのと同じ状態になる）。
    """
    entry = manifest.get(_rel(root, current))
    if entry is None or APPLIED_STAMP not in entry.applied:
        raise ValueError(f"証拠番号スタンプを付けた記録がありません：{current.name}")
    backup = backup_dir(root)
    work = _fresh_work_dir(backup)
    try:
        produced = build_without_stamp(root, entry, config, work)
        os.replace(produced, current)
        entry.applied = [a for a in entry.applied if a != APPLIED_STAMP]
        if (
            not entry.applied
            and len(entry.originals) == 1
            and _key(entry.originals[0]) == _key(entry.current)
        ):
            os.replace(backup / entry.originals[0], current)
            manifest.pop(entry.current)
        manifest.set_stamp_declined(_rel(root, current), True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    _forget_if_empty(root, manifest)


def record_stamp_choice(root: Path, path: Path, manifest: Manifest, declined: bool) -> None:
    """更新のときの、証拠番号スタンプの選択を覚える（declined：付けないと決めたか）。

    path は更新後のファイルの位置。付けたファイル・スタンプの項目のないファイルは
    declined=False で呼び、以前の「付けない」の印を消す。保存は呼び出し側が
    まとめて行う（save_choices）。
    """
    manifest.set_stamp_declined(_rel(root, path), declined)


def save_choices(root: Path, manifest: Manifest) -> None:
    """record_stamp_choice で覚えた内容を保存する（残すものがなければ、記録を片付ける）。"""
    _forget_if_empty(root, manifest)


def entry_pages(root: Path, entry: ManifestEntry, pdf_config=None) -> list[PageRef] | None:
    """更新したファイル（entry）の各ページが、「元のファイル」フォルダのどのファイルの
    何ページ目からできているか（スタンプを除いた姿）。分からないときは None。

    ページの表を記録してあればそれを使う（Ver300）。Ver200 の更新（記録なし）は、
    行った処理から組み立てる：結合なら材料の全ページを順に、1つのファイルなら全ページ。
    （右のペインの「向き補正」は 2026-11-17 に廃止した。その記録は見ない。）
    """
    backup = backup_dir(root)
    if entry.pages:
        return [PageRef(backup / rel, int(index), int(rotate)) for rel, index, rotate in entry.pages]
    if not entry.originals:
        return None
    paths = [backup / o for o in entry.originals]
    if any(not p.exists() or p.suffix.lower() != ".pdf" for p in paths):
        return None
    applied = set(entry.applied)
    if APPLIED_MERGE in applied:
        return [ref for p in paths for ref in all_pages(p)]
    if len(paths) != 1:
        return None
    refs = all_pages(paths[0])
    return refs


def compose_pages(
    root: Path, manifest: Manifest, refs: list[PageRef], pdf_config=None
) -> list[PageRef]:
    """ページの表（いまのファイルを指すもの）を、「元のファイル」フォルダの元のファイルを
    指すものに置き換える。

    以前に更新したファイル（記録があり、元のファイルが控えてあるもの）のページは、
    その元のファイルの、対応するページを指すようにする（回転は足し合わせる）。
    こうしておくと、更新したファイルのページを編集し直したときも、元のファイルから
    作り直すことになり、以前に押したスタンプが残ったり二重になったりしない。
    """
    cache: dict[Path, list[PageRef] | None] = {}
    result: list[PageRef] = []
    for ref in refs:
        if ref.path not in cache:
            base = None
            try:
                entry = manifest.get(_rel(root, ref.path))
            except ValueError:
                entry = None
            if entry is not None and (entry.originals or entry.created):
                base = entry_pages(root, entry, pdf_config)
            cache[ref.path] = base
        base = cache[ref.path]
        if base is not None and 0 <= ref.index < len(base):
            result.append(base[ref.index].rotated(ref.rotate))
        else:
            result.append(ref)
    return result


def _update_from_pages(
    root: Path,
    item: PlannedCopy,
    sources: list[Path],
    keys: list[str],
    wanted: Path,
    manifest: Manifest,
    evidence_stamp_config: EvidenceStampConfig | None,
    filename_config: FilenameConfig | None,
) -> UpdateOutcome:
    """ページの表から作る更新（Ver300）。手順は update_item と同じ（作る → 控える →
    置き換える → 記録する）。

    item.pages の各行は、「元のファイル」フォルダのファイルか、まだ控えていない
    いまのファイル（sources のもの、又はほかの PDF）を指す（compose_pages を通しておく
    こと）。いまのファイルを指す行は、控えた後の元のファイルを指すものとして記録する。
    """
    backup = backup_dir(root)
    work = _fresh_work_dir(backup)
    work.mkdir(parents=True)
    try:
        copy_to_output([item], work, evidence_stamp_config)
        produced = work / item.output_filename

        originals: list[str] = []
        carried: list[str] = []
        original_of: dict[Path, str] = {}  # いまのファイル → 控えた元のファイルの相対位置

        materials: list[str] = []
        # 分割で新しく作るファイル（Ver300）と、以前に分割で作ったファイルを作り直すときは、
        # 「作ったファイル」として記録する（［元のファイルに戻す］で消す）。
        created = item.created
        added = _was_added(root, manifest, sources[:1])
        for index, source in enumerate(sources):
            entry = manifest.get(_rel(root, source))
            if entry is not None and entry.created:
                # 以前に分割で作ったファイル：控えない（更新の前にはなかった）。ページは
                # 材料の写しを指している（compose_pages）。
                if index == 0:
                    created = True
                carried.extend(a for a in entry.applied if a == APPLIED_SPLIT and a not in carried)
                materials.extend(m for m in entry.materials if m not in materials)
                continue
            if entry is not None:
                # 内容に関わらない記録（ファイル名の整形・結合したこと）は引き継ぐ。スタンプ・
                # メタデータ除去は、この更新の結果（keys）で決まる。
                carried.extend(
                    a for a in entry.applied
                    if a in (APPLIED_SANITIZE, APPLIED_MERGE, APPLIED_JOIN) and a not in carried
                )
                materials.extend(m for m in entry.materials if m not in materials)
            if entry is not None and entry.originals:
                originals.extend(o for o in entry.originals if o not in originals)
            else:
                original_of[source] = _backup_current(root, manifest, source)
        # なくなるファイルは、ページを1枚も使わなくても（全ページを削除しても）控えて戻せるようにする。
        originals.extend(o for o in original_of.values() if o not in originals)
        own = set(originals)

        # ページの挿入の材料（なくならないファイル）は、写しを「材料」フォルダに置き、
        # 元のファイルとは分けて記録する（［元のファイルに戻す］で、いまのファイルと
        # ぶつからないように）。ほかの更新の元のファイルを指すページも、写しを使う。
        material_of: dict[Path, str] = {}

        def _material(path: Path) -> str:
            if path not in material_of:
                (backup / MATERIALS_DIRNAME).mkdir(parents=True, exist_ok=True)
                n = 1
                while (backup / MATERIALS_DIRNAME / f"{n:03d}_{path.name}").exists():
                    n += 1
                rel = f"{MATERIALS_DIRNAME}/{n:03d}_{path.name}"
                shutil.copy2(path, backup / rel)
                material_of[path] = rel
            return material_of[path]

        stored: list[list] = []
        for ref in item.pages:
            try:
                rel = ref.path.relative_to(backup).as_posix()
            except ValueError:
                rel = original_of[ref.path] if ref.path in original_of else _material(ref.path)
            else:
                if rel not in own and not rel.startswith(MATERIALS_DIRNAME + "/"):
                    rel = _material(ref.path)
            if rel.startswith(MATERIALS_DIRNAME + "/"):
                if rel not in materials:
                    materials.append(rel)
            elif rel not in originals:
                originals.append(rel)
            stored.append([rel, ref.index, ref.rotate % 360])
        # 使わなくなった材料の写しは、記録を保存した後で消す（材料の写しは、この記録だけのもの）。
        used = {row[0] for row in stored}
        unused = [m for m in materials if m not in used]
        materials = [m for m in materials if m in used]

        dest, renamed = _free_destination(wanted, sources, filename_config)
        dest_dir = wanted.parent
        dest_dir.mkdir(parents=True, exist_ok=True)
        for source in sources:
            manifest.pop(_rel(root, source))
            if not _same_file(source, dest):
                source.unlink()
        os.replace(produced, dest)
        applied = list(keys) + [a for a in carried if a not in keys]
        manifest.add(
            ManifestEntry(
                current=_rel(root, dest), originals=originals, applied=applied, pages=stored,
                materials=materials, created=created,
                reduce_dpi=item.reduce_dpi if APPLIED_REDUCE in keys else None,
                added=added,
            )
        )
        manifest.save(root)
        for rel in unused:
            (backup / rel).unlink(missing_ok=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    moved = bool(sources) and dest.parent != sources[0].parent
    return UpdateOutcome(path=dest, changed=True, moved=moved, renamed_for_collision=renamed)


def isolate_shared_pages(root: Path, items: list[PlannedCopy]) -> list[PlannedCopy]:
    """まとめて更新する前に、各計画のページの表のうち、ほかのファイル（その計画で
    なくならないファイル）を指すページを、「材料」フォルダの写しを指すものに替える。

    1回の更新で、あるファイルのページをほかのファイルにも使う場合（分割：分ける前の
    ファイルの後ろのページを、新しいファイルに使う。挿入：左の一覧のほかのファイルの
    ページを入れる）、先にそのファイルを更新すると、後の計画が、更新後の中身を読んで
    しまう。そこで、どれを更新するよりも前に、いまの中身の写しを取っておく。
    写しは、計画ごとに別に取る（材料の写しは、1つの記録だけのもの）。使われなかった
    写し（更新を中止したときなど）は discard_unused_materials で消す。
    """
    backup = backup_dir(root)
    result: list[PlannedCopy] = []
    for item in items:
        if item.pages is None:
            result.append(item)
            continue
        own = set(_item_sources(item))
        copies: dict[Path, Path] = {}
        refs: list[PageRef] = []
        for ref in item.pages:
            path = ref.path
            try:
                path.relative_to(backup)
                inside_backup = True
            except ValueError:
                inside_backup = False
            if inside_backup or path in own:
                refs.append(ref)
                continue
            if path not in copies:
                folder = backup / MATERIALS_DIRNAME
                folder.mkdir(parents=True, exist_ok=True)
                n = 1
                while (folder / f"{n:03d}_{path.name}").exists():
                    n += 1
                copies[path] = folder / f"{n:03d}_{path.name}"
                shutil.copy2(path, copies[path])
            refs.append(PageRef(copies[path], ref.index, ref.rotate))
        result.append(dataclasses.replace(item, pages=tuple(refs)))
    return result


def discard_unused_materials(root: Path, manifest: Manifest) -> None:
    """「材料」フォルダの写しのうち、どの記録からも使われていないものを消す。"""
    folder = backup_dir(root) / MATERIALS_DIRNAME
    if not folder.is_dir():
        return
    used = {_key(m) for e in manifest.entries for m in e.materials}
    for path in folder.iterdir():
        if path.is_file() and _key(f"{MATERIALS_DIRNAME}/{path.name}") not in used:
            path.unlink(missing_ok=True)
    try:
        folder.rmdir()  # 空になったら消す
    except OSError:
        pass


@dataclass
class RestoreResult:
    restored: int = 0  # 元に戻したファイルの数（結合前のファイルは1つずつ数える）
    failures: list[str] = field(default_factory=list)  # 「名前（理由）」
    errors: list[BaseException] = field(default_factory=list)  # 戻せなかった例外（画面が診断情報に記録する）


def _remove_empty_dirs(top: Path) -> None:
    """top の下の空のフォルダを、下から消す（top 自身も、空なら消す）。"""
    if not top.is_dir():
        return
    for current, _dirs, _files in os.walk(top, topdown=False):
        try:
            os.rmdir(current)
        except PermissionError:
            # OneDrive の下のフォルダには「読み取り専用」の印が付いていることがあり、Windows は、
            # 空でも、その印のあるフォルダを消さない（2026-11-16、［元のファイルに戻す］の後に、
            # 空の「元のファイル\材料」が残った）。空のときだけ、印を外して消す。
            try:
                if not os.listdir(current):
                    os.chmod(current, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
                    os.rmdir(current)
            except OSError:
                pass
        except OSError:
            pass  # 空でない


def restore_all(root: Path) -> RestoreResult:
    """更新記録にあるすべてのファイルを、更新前の姿（名前・位置・内容）に戻す。

    いまのファイル（更新後のもの）は消す。更新の後に他のアプリで直した内容も
    失われるため、呼び出し側（GUI）で必ず実行前にユーザーの確認を得ること。

    戻す先に、記録にない同じ名前のファイルがある場合（更新の後に利用者が置いたもの）は、
    上書きせず、その1件を戻さずに残す（failures に入れる。控えと記録も残る）。
    すべて戻せたら、「元のファイル」フォルダ（記録を含む）と、空になった
    「提出用_NN」フォルダを消す。
    """
    manifest = Manifest.load(root)
    backup = backup_dir(root)
    result = RestoreResult()
    remaining: list[ManifestEntry] = []

    # 1) 控えのそろっているものだけ、いまのファイルを消す（戻す先を空けるため、先にまとめて行う）。
    ready: list[ManifestEntry] = []
    for entry in manifest.entries:
        if entry.added:
            # 追加して写したファイル（Ver300）：追加する前にはなかったので、消す（控えと材料の
            # 写しも、2) で消す）。元の場所のファイルは、そもそも触れていない。
            try:
                (root / entry.current).unlink(missing_ok=True)
            except OSError as exc:
                result.failures.append(f"{Path(entry.current).name}（消せません。{reason(exc)}）")
                result.errors.append(exc)
                remaining.append(entry)
                continue
        elif entry.created and not entry.originals:
            # 分割で作ったファイル：更新の前にはなかったので、消す（材料の写しも、2) で消す）。
            try:
                (root / entry.current).unlink(missing_ok=True)
            except OSError as exc:
                result.failures.append(f"{Path(entry.current).name}（消せません。{reason(exc)}）")
                result.errors.append(exc)
                remaining.append(entry)
                continue
        elif entry.originals:
            missing = [o for o in entry.originals if not (backup / o).exists()]
            if missing:
                result.failures.append(
                    f"{Path(entry.current).name}（「{BACKUP_DIRNAME}」フォルダに控えがありません）"
                )
                remaining.append(entry)
                continue
            try:
                (root / entry.current).unlink(missing_ok=True)
            except OSError as exc:
                result.failures.append(f"{Path(entry.current).name}（消せません。{reason(exc)}）")
                result.errors.append(exc)
                remaining.append(entry)
                continue
        ready.append(entry)

    # 2) 元の位置へ戻す。
    for entry in ready:
        left: list[str] = []
        if entry.added:
            for leftover in [*entry.originals, *entry.materials]:
                (backup / leftover).unlink(missing_ok=True)
        elif entry.created and not entry.originals:
            for material in entry.materials:
                (backup / material).unlink(missing_ok=True)
        elif entry.originals:
            for original in entry.originals:
                target = root / original
                if target.exists():
                    result.failures.append(f"{Path(original).name}（同じ名前のファイルが既にあります）")
                    left.append(original)
                    continue
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(backup / original, target)
                    result.restored += 1
                except OSError as exc:
                    result.failures.append(f"{Path(original).name}（戻せません。{reason(exc)}）")
                    result.errors.append(exc)
                    left.append(original)
            if left:
                # いまのファイルは消してあるので、current は残った控えの位置を指しておく
                # （次に戻すとき、消すものがなくても差し支えない）。
                remaining.append(dataclasses.replace(entry, originals=left))
            else:
                # ページの挿入の材料の写しは、元のファイルではないので戻さずに消す。
                for material in entry.materials:
                    (backup / material).unlink(missing_ok=True)
        elif entry.moved_from:
            current, target = root / entry.current, root / entry.moved_from
            if not current.exists():
                continue  # 利用者が消した・移した。戻すものがない
            if target.exists():
                result.failures.append(f"{target.name}（同じ名前のファイルが既にあります）")
                remaining.append(entry)
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(current, target)
                result.restored += 1
            except OSError as exc:
                result.failures.append(f"{target.name}（戻せません。{reason(exc)}）")
                result.errors.append(exc)
                remaining.append(entry)

    if remaining:
        Manifest(remaining).save(root)
    else:
        Manifest.path(root).unlink(missing_ok=True)
        for name in (WORK_DIRNAME, *_OLD_WORK_DIRNAMES):
            shutil.rmtree(backup / name, ignore_errors=True)
        _remove_empty_dirs(backup)

    for child in root.iterdir():
        if child.is_dir() and is_batch_dir_name(child.name):
            _remove_empty_dirs(child)
    return result
