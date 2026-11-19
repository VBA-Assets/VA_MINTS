"""証拠説明書（要件定義書 4.17）：行の組み立てと、標目・原本写しの別とファイル名の連動。

画面の選択と更新の計画（ui/workspace.py の Workspace）の一部を、役目ごとに分けたもの。
状態（self.description など）は Workspace.__init__ が持ち、このクラスは Workspace に
まぜて（多重継承で）使う。単独では使わない。

- 行は、対象フォルダの中の、ファイル名の先頭に証拠番号のあるファイル1つにつき1行。枝番の範囲の
  あるファイル（「甲005-1~3」。枝番の区切り〔4.11〕を付けたファイルも、名前に範囲が付く）は、
  枝番ごとに1行（2026-11-18 班長決定）。証拠番号の順に並べる。
- 号証・標目は、更新した後のファイル名（いまの下書きの名前）から決める。1行のファイルでは、
  標目とファイル名は1つのもの（準備の手引29頁）：標目を直すと名前の変更（下書き）になる。
  原本・写しの別の ① ③ も、ファイル名の末尾の「原本」「写し」とそろえる。
- 枝番ごとの行の標目は、既定でファイル名の標目とし、行ごとに変えられる（ファイル名は変えない）。
- 入力は、変えるたびに保存する（証拠のファイルを変えないため、確認は要らない）。
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path

from va_mints.models import FileRecord
from va_mints.naming.evidence_number import (
    evidence_base_label,
    evidence_sort_key,
    match_evidence_number,
)
from va_mints.processors.evidence_description import (
    CATEGORIES,
    CATEGORY_COPY,
    CATEGORY_NONE,
    CATEGORY_ORIGINAL,
    DATA_DIRNAME,
    DATA_NAME,
    ROW_TEXT_FIELDS,
    DescriptionStore,
    Header,
    Row,
    RowData,
    default_party,
    digest,
    file_key,
    pdf_name,
    halfwidth_alnum,
    split_title,
    title_with_tail,
)


@dataclass(frozen=True)
class DescriptionRow:
    """証拠説明書の1行（画面とPDFに出すもの）。"""

    record: FileRecord
    position: int  # そのファイルの何行目（1始まり。枝番の何番目）
    count: int  # そのファイルの行の数
    label: str  # 号証（例：甲001・甲005-2）
    title: str  # 標目（原本・写しの語を除いたもの）
    category: str
    data: RowData

    @property
    def linked(self) -> bool:
        """標目と原本・写しの別が、ファイル名とつながっているか（1行のファイル）。"""
        return self.count == 1

    def to_row(self) -> Row:
        d = self.data
        return Row(
            label=self.label, title=self.title, category=self.category, date=d.date,
            author=d.author, purpose=d.purpose, remarks=d.remarks, span=d.span,
        )


@dataclass
class DescriptionJob:
    """［ファイルを更新］で作る証拠説明書（更新の計画の一部）。"""

    header: Header
    rows: list[Row]
    target: Path
    digest: str
    unchosen: int = 0  # 原本・写しの別を選んでいない行の数
    blank: int = 0  # 作成年月日・作成者・立証趣旨のどれかが空の行の数
    # 前に作った PDF（名前・置き場所が変わるので消すもの。番号を変えたとき・「提出用_01」との間で
    # 移すとき）。なければ None。
    replaces: Path | None = None
    # 作れないわけ（対象フォルダに、同じ名前の別のファイルがあるとき）。なければ空。
    conflict: str = ""


class DescriptionMixin:
    # ------------------------------------------------------------------ 読み込み
    def _init_description(self) -> None:
        self.description_error: str | None = None
        # 読めなかったときの元の例外（画面が、型と場所を診断情報に記録する）。
        self.description_exc: BaseException | None = None
        try:
            self.description = DescriptionStore.load(self.root)
        except ValueError as exc:
            # 壊れた入力を、空の入力で上書きしないよう、保存できなくする。
            self.description = DescriptionStore()
            self.description_error = str(exc)  # このツールが書いた日本語の文（部品の文は入らない）
            self.description_exc = exc.__cause__ or exc

    def description_pdf(self) -> Path | None:
        """このツールで最後に作った証拠説明書の PDF の位置（まだ作っていなければ None）。"""
        last = self.description.last_pdf
        return self.root / last if last else None

    def is_description_file(self, record: FileRecord) -> bool:
        """このツールで作った証拠説明書の PDF か（入力に覚えている、最後に作った PDF の位置で見分ける。
        たまたま同じ名前の、利用者のファイルとは区別する）。"""
        return record.path == self.description_pdf()

    def _description_conflict(self, target: Path) -> str:
        """target に作れないわけ（同じ名前の、このツールで作ったのではないファイルがあるとき）。"""
        if target.exists() and target != self.description_pdf():
            return (
                f"対象フォルダに、同じ名前のファイル（{target.name}）があるため、証拠説明書を作れません。"
                "そのファイルの名前を変えるか、証拠説明書の番号を変えてください。"
            )
        return ""

    # ------------------------------------------------------------------ 行
    def _description_name(self, record: FileRecord) -> str:
        """号証・標目を決める名前（更新した後の名前）。枝番ファイル結合をするグループのファイルは、
        そのファイル自身の名前（結合した後のファイルの、その枝番の行になる）。"""
        gs = self.group_for(record)
        if gs is not None and gs.merge:
            return record.path.name
        return self.output_name(record) or self.name_of(record)

    def _row_key(self, record: FileRecord, position: int) -> tuple[str, int]:
        return (file_key(self.root, record.path), position)

    def description_rows(self) -> list[DescriptionRow]:
        """証拠説明書の行（証拠番号の順）。"""
        config = self.config.evidence_number
        found: list[tuple[tuple, DescriptionRow]] = []
        files = [r for r in self.eligible if not self.is_absorbed(r)]
        current_keys = {file_key(self.root, r.path) for r in files}
        for record in files:
            name = self._description_name(record)
            match = match_evidence_number(name, config)
            if match is None:
                continue
            base = evidence_base_label(match)
            if match.branch_from is None:
                labels = [base]
            else:
                first = int(match.branch_from)
                last = int(match.branch_to) if match.branch_to is not None else first
                labels = [f"{base}-{n}" for n in range(first, max(first, last) + 1)]
            for position, label in enumerate(labels, start=1):
                data = self._row_data(record, position, label, current_keys)
                if len(labels) == 1:
                    title, category = split_title(match.title, data.category)
                else:
                    default, guessed = split_title(match.title, CATEGORY_NONE)
                    title = data.title if data.title is not None else default
                    category = data.category or guessed
                row = DescriptionRow(record, position, len(labels), label, title, category, data)
                # 枝番の順：範囲のあるファイルは、範囲の初めから数える。
                key = (*evidence_sort_key(match, config)[:3],
                       (int(match.branch_from) if match.branch_from else 0) + position - 1)
                found.append((key, row))
        found.sort(key=lambda item: item[0])
        return [row for _key, row in found]

    def _row_data(
        self, record: FileRecord, position: int, label: str, current_keys: set[str]
    ) -> RowData:
        """その行の入力（なければ、同じ証拠番号の、いまはないファイルの行を付け直す。それもなければ空）。"""
        rows = self.description.rows
        key = self._row_key(record, position)
        data = rows.get(key)
        if data is None:
            orphan = next(
                (k for k, v in rows.items() if v.label == label and k[0] not in current_keys), None
            )
            data = rows.pop(orphan) if orphan is not None else RowData()
            rows[key] = data
        data.label = label
        return data

    # ------------------------------------------------------------------ 入力
    def _save_description(self) -> None:
        if self.description_error:
            raise ValueError(
                f"証拠説明書のデータを保存できません（{self.description_error}）。"
                f"対象フォルダの中の「{DATA_DIRNAME}」フォルダの {DATA_NAME} を確かめてください。"
            )
        self.description.save(self.root)

    def description_header(self) -> Header:
        """表題の欄（提出者の別が空なら、証拠番号の符号から決めたもの）。"""
        header = dataclasses.replace(self.description.header)
        if not header.party.strip():
            header.party = default_party(r.label for r in self.description_rows())
        return header

    def set_description_header(self, field_name: str, value: str) -> None:
        if field_name not in Header.__dataclass_fields__:
            raise KeyError(field_name)
        if getattr(self.description.header, field_name) == value:
            return
        setattr(self.description.header, field_name, value)
        self._save_description()

    def set_row_field(self, row: DescriptionRow, field_name: str, value) -> None:
        """作成年月日・作成者・立証趣旨・備考・標目をまたがせるか（span）を入れる。"""
        if field_name not in (*ROW_TEXT_FIELDS, "span"):
            raise KeyError(field_name)
        if getattr(row.data, field_name) == value:
            return
        setattr(row.data, field_name, value)
        self._save_description()

    def row_rename(self, row: DescriptionRow, title: str, category: str) -> str | None:
        """1行のファイルで、標目を title・原本写しの別を category にしたときの名前（変わらなければ
        None）。標目が空なら ValueError。

        名前は、準備の手引29頁の形にそろえる（2026-11-18 班長指示）：「証拠番号（半角三桁）＋半角の空白
        ＋標目」で、英数字は半角（「ファイル名：証拠番号（半角三桁）に加えて証拠説明書に記載する証拠の
        標目のとおり記載（英数字は半角）」。例「甲001 売買契約書原本」）。証拠番号は「甲001」「乙A001」、
        枝番は「甲005-2」「甲005-1~3」の形にする。標目はファイル名から決まるので、証拠説明書の標目も
        英数字が半角になり、両者は食い違わない。"""
        title = halfwidth_alnum(title.strip())
        if not title:
            raise ValueError("標目を入れてください。")
        name = self._description_name(row.record)
        match = match_evidence_number(name, self.config.evidence_number)
        if match is None:
            return None
        label = evidence_base_label(match)
        if match.branch_from is not None:
            label += f"-{int(halfwidth_alnum(match.branch_from))}"
            if match.branch_to is not None:
                label += f"~{int(halfwidth_alnum(match.branch_to))}"
        suffix = Path(name).suffix
        new = f"{label} {title_with_tail(title, category)}{suffix}"
        return None if new == name else new

    def set_row_title(self, row: DescriptionRow, title: str) -> None:
        """標目を直す。1行のファイルは、ファイル名を変える（下書き。変えられなければ ValueError）。
        枝番の行は、その行の標目だけを変える（ファイル名は変えない）。"""
        if row.linked:
            new = self.row_rename(row, title, row.category)
            if new is not None:
                self.rename(row.record, new)
            return
        title = title.strip()
        if not title:
            raise ValueError("標目を入れてください。")
        name = self._description_name(row.record)
        match = match_evidence_number(name, self.config.evidence_number)
        default = split_title(match.title, CATEGORY_NONE)[0] if match is not None else ""
        value = None if title == default else title
        if row.data.title != value:
            row.data.title = value
            self._save_description()

    def set_row_category(self, row: DescriptionRow, category: str) -> None:
        """原本・写しの別を選ぶ。1行のファイルで ① ③ を選ぶ・外すときは、ファイル名の末尾の
        「原本」「写し」も直す（下書き。変えられなければ ValueError）。"""
        if category not in CATEGORIES:
            raise KeyError(category)
        if row.linked:
            title = row.title
            if category == CATEGORY_COPY and row.category != CATEGORY_COPY:
                # 標目が既に「写し」で終わっていれば、その「写し」を2行目に移す（重ねない）。
                title = split_title(title, CATEGORY_COPY)[0]
            new = self.row_rename(row, title, category)
            if new is not None:
                self.rename(row.record, new)
        if row.data.category != category:
            row.data.category = category
            self._save_description()

    # ------------------------------------------------------------------ 更新
    def description_job(self, other_work: bool, batch_dir: Path | None = None) -> DescriptionJob | None:
        """［ファイルを更新］で作る証拠説明書（作らないときは None）。

        入力を保存したことのあるフォルダだけで作る。ファイルの更新があるときは、号証・標目が
        変わるかもしれないので作り直す。ないときは、中身が前に作ったものと同じで、PDF が
        残っていれば作らない。batch_dir：200MB を超えて分けるときの「提出用_01」（PDF をそこに置く。
        最初の回の証拠と一緒に出す）。分けないときは None（対象フォルダの直下に置く）。
        """
        if not self.description.exists or self.description_error:
            return None
        views = self.description_rows()
        header = self.description_header()
        rows = [v.to_row() for v in views]
        target = (batch_dir or self.root) / pdf_name(header)
        value = digest(header, rows)
        current = self.description_pdf()
        if (
            not other_work and value == self.description.last_digest
            and target == current and target.exists()
        ):
            return None
        return DescriptionJob(
            replaces=current if current is not None and current != target and current.exists() else None,
            conflict=self._description_conflict(target),
            header=header,
            rows=rows,
            target=target,
            digest=value,
            unchosen=sum(1 for r in rows if r.category == CATEGORY_NONE),
            blank=sum(1 for r in rows if not (r.date.strip() and r.author.strip() and r.purpose.strip())),
        )

    def description_mapping(self, results) -> tuple[dict, set[str]]:
        """更新の結果（(PlannedUpdate, 更新後の位置) の並び）から、行の付け替えを作る。

        枝番ファイル結合をしたグループは、n 番目のファイルの行を、結合したファイルの n 行目へ
        （そのファイルの標目が、結合したファイルの名前の標目と違えば、その行の標目として残す）。
        ほかのファイルと結合して消えたファイルの行は、捨てる。
        """
        config = self.config.evidence_number
        mapping: dict[tuple[str, int], tuple[str, int]] = {}
        dropped: set[str] = set()
        rows = self.description.rows
        for update, new_path in results:
            item = update.item
            new_key = file_key(self.root, new_path)
            if item.merge_group is not None:
                merged = match_evidence_number(new_path.name, config)
                merged_title = split_title(merged.title, CATEGORY_NONE)[0] if merged else ""
                for i, member in enumerate(item.merge_group.members, start=1):
                    old = (file_key(self.root, member.record.path), 1)
                    mapping[old] = (new_key, i)
                    data = rows.setdefault(old, RowData())
                    title, guessed = split_title(member.match.title, data.category)
                    if data.title is None and title != merged_title:
                        data.title = title
                    if not data.category and guessed == CATEGORY_ORIGINAL:
                        data.category = guessed
                continue
            old_key = file_key(self.root, item.record.path)
            for (key, position) in list(rows):
                if key == old_key:
                    mapping[(key, position)] = (new_key, position)
            dropped.update(file_key(self.root, p) for p in item.absorbed)
        return mapping, dropped

    def rekey_description(self, results) -> None:
        """更新の後：行を、更新した後のファイルに付け替えて保存する。"""
        mapping, dropped = self.description_mapping(results)
        self.description.rekey(mapping, dropped)
        self._save_description()

    def _place_description(self, header: Header, rows: list[Row], target: Path, value: str) -> Path:
        """PDF を target に作り、前に作った PDF（名前・置き場所が違うもの）を消し、覚え直す。"""
        from va_mints.processors.evidence_description import build_pdf

        conflict = self._description_conflict(target)
        if conflict:
            raise ValueError(conflict)
        previous = self.description_pdf()
        path = build_pdf(header, rows, target)
        if previous is not None and previous != target:
            previous.unlink(missing_ok=True)
            try:
                if previous.parent != self.root:
                    previous.parent.rmdir()  # 空になった「提出用_NN」
            except OSError:
                pass
        self.description.last_digest = value
        self.description.last_pdf = file_key(self.root, path)
        self._save_description()
        self.sync_description_files(previous)
        return path

    def finish_description(self, job: DescriptionJob, results) -> Path:
        """更新の後：行を、更新した後のファイルに付け替えて保存し、PDF を作る。"""
        self.rekey_description(results)
        if job.conflict:
            raise ValueError(job.conflict)
        return self._place_description(job.header, job.rows, job.target, job.digest)

    # ------------------------------------------------------------------ 入力に合わせて作り直す
    def write_description_pdf(self) -> Path | None:
        """いまの入力（とファイル名の下書き）で、証拠説明書の PDF を作る（2026-11-18 班長指示。最初に
        ［作成］を押したときと、入力を変えるたびに作り、［ファイルを更新］を押さなくても、一覧で
        選べば今の姿が見られるようにする）。置き場所は、対象フォルダの直下（前に作った PDF が
        「提出用_01」にあれば、そこ）。中身が前に作ったものと同じで、PDF が残っていれば作らない（None）。
        同じ名前の、このツールで作ったのではないファイルがあれば ValueError。

        証拠のファイルは変えないので、確認は要らない（入力の保存と同じ）。ファイル名の下書きは
        ［ファイルを更新］まで実際のファイルに入らないので、mints に出すのは更新した後の PDF とする
        （更新のときにも作り直す。description_job）。"""
        if self.description_error:
            raise ValueError(f"証拠説明書のデータを読めないため、作れません（{self.description_error}）。")
        header = self.description_header()
        rows = [v.to_row() for v in self.description_rows()]
        current = self.description_pdf()
        folder = current.parent if current is not None and current.exists() else self.root
        target = folder / pdf_name(header)
        value = digest(header, rows)
        if self.description.exists and value == self.description.last_digest and target == current \
                and target.exists():
            return None
        return self._place_description(header, rows, target, value)

    def delete_description(self, trash) -> None:
        """証拠説明書の PDF と入力（「証拠説明書データ」フォルダ）を、ごみ箱に移す（2026-11-18 班長決定
        「［削除］で消す」）。trash：移す手順（画面は platform_support.send_to_recycle_bin）。消した後は、
        もう一度［作成］を押すまで、証拠説明書を作らない（入力がないため）。"""
        pdf = self.description_pdf()
        if pdf is not None and pdf.exists():
            trash(pdf)
        data = self.root / DATA_DIRNAME
        if data.exists():
            trash(data)
        self.description = DescriptionStore()
        self.description_error = None
        self.sync_description_files(pdf)

    def sync_description_files(self, previous: Path | None = None) -> None:
        """一覧のファイルの並びを、作った証拠説明書に合わせる：前に作った PDF（previous）がなくなって
        いれば外し、いまの PDF は読み直して入れる（なければ足す）。読み込み直しはしない（ほかのファイルの
        下書きを捨てないため）。"""
        from va_mints.scanner.walker import scan_file

        current = self.description_pdf()
        records = [
            r for r in self._base_records
            if not (previous is not None and r.path == previous and not previous.exists())
        ]
        if current is not None and current.exists():
            try:
                fresh = scan_file(current, self.config)
            except Exception:  # noqa: BLE001 - 一覧に出せないだけ（作ったファイルはある）
                fresh = None
            if fresh is not None:
                for i, r in enumerate(records):
                    if r.path == current:
                        records[i] = fresh
                        break
                else:
                    records.insert(0, fresh)  # 一覧のいちばん上（Workspace.__init__ と同じ）
        self._base_records = records
        self._rebuild_records()
