"""入力フォルダの中のファイルを、その場で更新する（要件定義書 第3章「処理フロー」⑥。Ver200）。

Ver170 までは、処理したファイルを別の出力フォルダへ書き出していた。Ver200 からは、
読み込んだフォルダの中のファイルそのものを更新し、更新する前のファイルを、同じ
フォルダの中の「元のファイル」フォルダに残す（2026-11-16 班長指示）。

「元のファイル」フォルダの中身：
- 更新する前のファイル（読み込んだフォルダからの相対位置のまま置く）。同じ位置に
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
- 作業中 … 更新の途中だけ使うフォルダ（処理結果をここに作ってから、置き換える）。

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
from dataclasses import dataclass, field
from pathlib import Path

from mints_prep.config import EvidenceStampConfig, FilenameConfig
from mints_prep.processors.output_writer import PlannedCopy, copy_to_output, make_deduped_name

BACKUP_DIRNAME = "元のファイル"
MANIFEST_FILENAME = "更新記録.json"
WORK_DIRNAME = "作業中"
# 200MB分割の提出回ごとのフォルダ（batch_planner.Batch.folder_name と同じ形）。
_BATCH_DIR_PATTERN = re.compile(r"^提出用_\d{2,}$")

# 更新記録の applied に入れる、処理の名前（ui/review_logic.py の ReviewOption.key と同じ）。
APPLIED_SANITIZE = "sanitize"
APPLIED_STRIP_METADATA = "strip_metadata"
APPLIED_STAMP = "stamp"
APPLIED_ROTATE = "rotate"
APPLIED_MERGE = "merge"
# applied_for が返す集合に足す印：利用者が、このファイルに証拠番号スタンプを
# 付けないと決めた（チェックを外して更新した・付けたスタンプを消した）。
STAMP_DECLINED = "stamp_declined"
# 更新の計画の中だけで使う名前（更新記録の applied には入らない）：証拠番号スタンプの消去。
ACTION_UNSTAMP = "unstamp"


def backup_dir(root: Path) -> Path:
    return root / BACKUP_DIRNAME


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
    # 向き補正（applied に rotate があるとき）で回した角度。スタンプを消すために、
    # 更新前のファイルから作り直すときに使う。
    rotate_degrees: int | None = None


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
                    rotate_degrees=e.get("rotate_degrees"),
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
        os.replace(tmp, path)


def has_restorable_update(root: Path) -> bool:
    """［元のファイルに戻す］で戻せる更新があるか（更新記録に1件でもあるか）。"""
    try:
        return bool(Manifest.load(root))
    except ValueError:
        return False


def _item_sources(item: PlannedCopy) -> list[Path]:
    if item.merge_group is not None:
        return [m.record.path for m in item.merge_group.members]
    return [item.record.path]


def applied_keys(item: PlannedCopy) -> list[str]:
    """この計画で行う処理の名前（ファイルの内容・名前を変えるもの）。なければ空。"""
    keys: list[str] = []
    if item.merge_group is not None:
        keys.append(APPLIED_MERGE)
        if item.merge_stamp:
            keys.append(APPLIED_STAMP)
    elif item.stamp_label is not None:
        keys.append(APPLIED_STAMP)
    if item.rotate_degrees is not None and item.merge_group is None:
        keys.append(APPLIED_ROTATE)
    if item.strip_metadata:
        keys.append(APPLIED_STRIP_METADATA)
    if item.filename_sanitized:
        keys.append(APPLIED_SANITIZE)
    return keys


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
    1. 処理結果を「元のファイル\\作業中」に作る（この時点では、元のファイルに触れない）。
    2. 更新前のファイルを「元のファイル」に写す（同じ位置に既にあれば写さない）。
    3. 更新前のファイルを消し、処理結果を dest_dir に置く。
    4. 更新記録を保存する。

    処理が1つもなく位置も変わらないときは、何もしない。処理がなく位置だけが変わる
    とき（「提出用_NN」への仕分け）は、控えを作らずに移し、元の位置を記録する。
    """
    sources = _item_sources(item)
    keys = applied_keys(item)
    wanted = dest_dir / item.output_filename

    if not keys:
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
    work = backup / WORK_DIRNAME
    if work.exists():
        shutil.rmtree(work)  # 前回の更新が途中で止まった残り
    work.mkdir(parents=True)
    try:
        copy_to_output([item], work, evidence_stamp_config)
        produced = work / item.output_filename

        originals: list[str] = []
        applied: list[str] = []
        rotate_degrees = item.rotate_degrees if APPLIED_ROTATE in keys else None
        for source in sources:
            source_rel = _rel(root, source)
            entry = manifest.get(source_rel)
            if entry is not None and rotate_degrees is None:
                rotate_degrees = entry.rotate_degrees
            if entry is not None and entry.originals:
                # 以前の更新で控え済み。途中の姿（いまのファイル）は控えない。
                originals.extend(o for o in entry.originals if o not in originals)
                applied.extend(a for a in entry.applied if a not in applied)
                continue
            # 移しただけのファイルは、元の位置の名前で控える（戻すときに元の位置へ戻る）。
            original_rel = entry.moved_from if entry is not None and entry.moved_from else source_rel
            backup_path = backup / original_rel
            if not backup_path.exists():
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, backup_path)
            if original_rel not in originals:
                originals.append(original_rel)

        dest, renamed = _free_destination(wanted, sources, filename_config)
        dest_dir.mkdir(parents=True, exist_ok=True)
        for source in sources:
            manifest.pop(_rel(root, source))
            if not _same_file(source, dest):
                source.unlink()
        os.replace(produced, dest)
        applied.extend(k for k in keys if k not in applied)
        manifest.add(
            ManifestEntry(
                current=_rel(root, dest),
                originals=originals,
                applied=applied,
                rotate_degrees=rotate_degrees if APPLIED_ROTATE in applied else None,
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
    うちスタンプ以外（メタデータ除去・向き補正・枝番ファイル結合。ファイル名は
    いまの名前）だけをやり直して作る。実際の消去（remove_stamp）と、チェックを
    外したときのプレビュー（ui/workspace.py）の両方が、この同じ関数を使う
    （プレビューと更新の結果が食い違わないように）。

    config は mints_prep.config.Config。更新前のファイルがないときは FileNotFoundError。
    """
    # scanner.walker はこのモジュールを取り込むため、ここで取り込む（循環を避ける）。
    from mints_prep.processors.evidence_merger import find_branch_merge_groups
    from mints_prep.processors.output_writer import _synthetic_merge_record
    from mints_prep.scanner.walker import scan_file

    backup = backup_dir(root)
    missing = [o for o in entry.originals if not (backup / o).exists()]
    if missing or not entry.originals:
        raise FileNotFoundError(
            f"「{BACKUP_DIRNAME}」フォルダに、更新前のファイルがありません：{Path(entry.current).name}"
        )
    records = [scan_file(backup / o, config) for o in entry.originals]
    name = Path(entry.current).name
    applied = set(entry.applied)
    strip = APPLIED_STRIP_METADATA in applied
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
    else:
        if len(records) != 1:
            raise ValueError(f"更新前のファイルを特定できません：{name}")
        item = PlannedCopy(
            record=records[0],
            output_filename=name,
            strip_metadata=strip,
            rotate_degrees=entry.rotate_degrees if APPLIED_ROTATE in applied else None,
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
    work = backup / WORK_DIRNAME
    if work.exists():
        shutil.rmtree(work)
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


@dataclass
class RestoreResult:
    restored: int = 0  # 元に戻したファイルの数（結合前のファイルは1つずつ数える）
    failures: list[str] = field(default_factory=list)  # 「名前（理由）」


def _remove_empty_dirs(top: Path) -> None:
    """top の下の空のフォルダを、下から消す（top 自身も、空なら消す）。"""
    if not top.is_dir():
        return
    for current, _dirs, _files in os.walk(top, topdown=False):
        try:
            os.rmdir(current)
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
        if entry.originals:
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
                result.failures.append(f"{Path(entry.current).name}（消せません：{type(exc).__name__}）")
                remaining.append(entry)
                continue
        ready.append(entry)

    # 2) 元の位置へ戻す。
    for entry in ready:
        left: list[str] = []
        if entry.originals:
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
                    result.failures.append(f"{Path(original).name}（戻せません：{type(exc).__name__}）")
                    left.append(original)
            if left:
                # いまのファイルは消してあるので、current は残った控えの位置を指しておく
                # （次に戻すとき、消すものがなくても差し支えない）。
                remaining.append(dataclasses.replace(entry, originals=left))
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
                result.failures.append(f"{target.name}（戻せません：{type(exc).__name__}）")
                remaining.append(entry)

    if remaining:
        Manifest(remaining).save(root)
    else:
        Manifest.path(root).unlink(missing_ok=True)
        shutil.rmtree(backup / WORK_DIRNAME, ignore_errors=True)
        _remove_empty_dirs(backup)

    for child in root.iterdir():
        if child.is_dir() and is_batch_dir_name(child.name):
            _remove_empty_dirs(child)
    return result
