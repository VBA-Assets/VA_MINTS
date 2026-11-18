"""証拠説明書を作る（要件定義書 4.17。2026-11-18 班長決定）。"""

from pathlib import Path

import pdfplumber
import pytest

from fixtures.generate_fixtures import make_multipage_pdf
from mints_prep.processors.evidence_description import (
    CATEGORY_COPY,
    CATEGORY_DATA,
    CATEGORY_DIGITIZED,
    CATEGORY_NONE,
    CATEGORY_ORIGINAL,
    DATA_DIRNAME,
    DescriptionStore,
    Header,
    Row,
    build_pdf,
    data_path,
    split_title,
)
from mints_prep.processors.updater import Manifest, restore_all
from mints_prep.processors.evidence_description import rekey_after_restore
from mints_prep.scanner.walker import scan_folder
from mints_prep.ui.workspace import Workspace, apply_all_updates, describe_update_plan


def _ws(root: Path, config) -> Workspace:
    return Workspace(scan_folder(root, config), config, Manifest.load(root))


def _apply(root: Path, ws: Workspace, config) -> None:
    apply_all_updates(root, ws.plan_update(), config, ws)


def _text(path: Path) -> str:
    with pdfplumber.open(path) as pdf:
        return "".join((p.extract_text() or "") for p in pdf.pages).replace(" ", "").replace("\n", "")


def _row(ws: Workspace, label: str):
    return next(r for r in ws.description_rows() if r.label == label)


# ---------------------------------------------------------------------- 標目
def test_split_title_takes_original_but_not_copy_unless_chosen():
    assert split_title("売買契約書原本", CATEGORY_NONE) == ("売買契約書", CATEGORY_ORIGINAL)
    # 「住民票の写し」は標目の一部のことがあるので、③ を選んでいなければ写しとみなさない。
    assert split_title("住民票の写し", CATEGORY_NONE) == ("住民票の写し", CATEGORY_NONE)
    assert split_title("領収書写し", CATEGORY_COPY) == ("領収書", CATEGORY_COPY)
    # ① をファイル名から外していれば、入力が ① でも「選んでいない」。
    assert split_title("契約書", CATEGORY_ORIGINAL) == ("契約書", CATEGORY_NONE)
    assert split_title("振込記録", CATEGORY_DATA) == ("振込記録", CATEGORY_DATA)


def test_rows_follow_evidence_numbers_and_branches(tmp_path: Path, config):
    make_multipage_pdf(tmp_path / "甲002 振込記録.pdf", 1)
    make_multipage_pdf(tmp_path / "甲001 売買契約書原本.pdf", 1)
    make_multipage_pdf(tmp_path / "甲003-1~3 領収書.pdf", 3)
    make_multipage_pdf(tmp_path / "原告準備書面1.pdf", 1)  # 証拠番号のないファイルは行にしない
    ws = _ws(tmp_path, config)
    rows = ws.description_rows()
    assert [r.label for r in rows] == ["甲001", "甲002", "甲003-1", "甲003-2", "甲003-3"]
    first = rows[0]
    assert (first.title, first.category, first.linked) == ("売買契約書", CATEGORY_ORIGINAL, True)
    assert all(not r.linked and r.title == "領収書" for r in rows[2:])
    assert ws.description_header().party == "原告"


def test_title_and_category_rename_the_file(tmp_path: Path, config):
    """1行のファイルでは、標目とファイル名は1つのもの（準備の手引29頁）。"""
    make_multipage_pdf(tmp_path / "甲001 契約書.pdf", 1)
    make_multipage_pdf(tmp_path / "甲002 住民票の写し.pdf", 1)
    ws = _ws(tmp_path, config)
    record = ws.eligible[0] if ws.eligible[0].path.name.startswith("甲001") else ws.eligible[1]

    ws.set_row_title(_row(ws, "甲001"), "売買契約書")
    assert ws.name_of(record) == "甲001 売買契約書.pdf"
    ws.set_row_category(_row(ws, "甲001"), CATEGORY_ORIGINAL)
    assert ws.name_of(record) == "甲001 売買契約書原本.pdf"
    row = _row(ws, "甲001")
    assert (row.title, row.category) == ("売買契約書", CATEGORY_ORIGINAL)
    # 標目を直しても、原本の語は残る。
    ws.set_row_title(row, "土地売買契約書")
    assert ws.name_of(record) == "甲001 土地売買契約書原本.pdf"
    # ② に変えると、原本の語を外す（備考に「紙を電子化」）。
    ws.set_row_category(_row(ws, "甲001"), CATEGORY_DIGITIZED)
    assert ws.name_of(record) == "甲001 土地売買契約書.pdf"
    assert _row(ws, "甲001").category == CATEGORY_DIGITIZED

    # ③：標目が既に「写し」で終わっていれば、名前は変えずに、2行目に移す。
    copy = _row(ws, "甲002")
    ws.set_row_category(copy, CATEGORY_COPY)
    copy = _row(ws, "甲002")
    assert ws.name_of(copy.record) == "甲002 住民票の写し.pdf"
    assert (copy.title, copy.category) == ("住民票の", CATEGORY_COPY)

    with pytest.raises(ValueError):
        ws.set_row_title(_row(ws, "甲001"), "  ")


def test_branch_row_title_does_not_rename(tmp_path: Path, config):
    make_multipage_pdf(tmp_path / "甲003-1~2 領収書.pdf", 2)
    ws = _ws(tmp_path, config)
    ws.set_row_title(_row(ws, "甲003-2"), "領収書（4月分）")
    assert ws.name_of(ws.eligible[0]) == "甲003-1~2 領収書.pdf"
    assert _row(ws, "甲003-2").title == "領収書（4月分）"
    assert _row(ws, "甲003-1").title == "領収書"


def test_input_is_saved_at_once_and_read_back(tmp_path: Path, config):
    make_multipage_pdf(tmp_path / "甲001 契約書.pdf", 1)
    ws = _ws(tmp_path, config)
    assert not data_path(tmp_path).exists()
    ws.set_description_header("court", "東京地方裁判所")
    ws.set_row_field(_row(ws, "甲001"), "purpose", "契約を結んだ事実")
    assert data_path(tmp_path).exists()

    again = _ws(tmp_path, config)
    assert again.description.header.court == "東京地方裁判所"
    assert _row(again, "甲001").data.purpose == "契約を結んだ事実"
    # 「証拠説明書」フォルダは、読み込みの対象にしない。
    # 入力を置いた「証拠説明書データ」フォルダは、読み込みの対象にしない。
    assert all(DATA_DIRNAME not in r.path.parts for r in again.records)


def test_no_pdf_until_something_is_entered(tmp_path: Path, config):
    make_multipage_pdf(tmp_path / "甲001 契約書.pdf", 1)
    ws = _ws(tmp_path, config)
    assert ws.plan_update().description is None


def test_update_makes_pdf_and_rows_follow_renamed_file(tmp_path: Path, config):
    make_multipage_pdf(tmp_path / "甲001 契約書.pdf", 1)
    ws = _ws(tmp_path, config)
    ws.set_description_header("number", "2")
    ws.set_row_field(_row(ws, "甲001"), "purpose", "契約を結んだ事実")
    ws.set_row_category(_row(ws, "甲001"), CATEGORY_ORIGINAL)  # 名前に「原本」が付く（下書き）

    plan = ws.plan_update()
    assert plan.description is not None
    text = describe_update_plan(plan, 200)
    assert "証拠説明書の作成：1 件" in text
    assert "作成年月日・作成者・立証趣旨のどれかが空の行：1 行" in text
    _apply(tmp_path, ws, config)

    assert (tmp_path / "甲001 契約書原本.pdf").exists()
    pdf = tmp_path / "証拠説明書（2）.pdf"  # 対象フォルダの直下
    body = _text(pdf)
    assert "甲001" in body and "契約書" in body and "原本" in body and "契約を結んだ事実" in body

    # 読み込み直すと、名前の変わったファイルに、入力が付いている。
    again = _ws(tmp_path, config)
    assert _row(again, "甲001").data.purpose == "契約を結んだ事実"
    # 何も変えていなければ、作り直さない。
    assert again.plan_update().description is None
    assert not again.plan_update().has_work

    # 作った証拠説明書は、左の一覧に出る（選んで見た目を確かめるため）。更新の対象・行にはしない。
    listed = [r for r in again.records if r.path == pdf]
    assert len(listed) == 1 and again.is_description_file(listed[0])
    assert pdf not in {r.path for r in again.eligible}
    assert again.remove_error(listed[0])
    assert all(r.record.path != pdf for r in again.description_rows())
    assert all(DATA_DIRNAME not in r.path.parts for r in again.records)


def test_merge_group_rows_become_branch_rows(tmp_path: Path, config):
    """枝番ファイル結合：n 番目のファイルの行が、結合したファイルの n 行目になる（標目も残る）。"""
    make_multipage_pdf(tmp_path / "甲004-1 領収書（4月分）.pdf", 1)
    make_multipage_pdf(tmp_path / "甲004-2 領収書（5月分）.pdf", 1)
    ws = _ws(tmp_path, config)
    assert [r.label for r in ws.description_rows()] == ["甲004-1", "甲004-2"]
    ws.set_row_field(_row(ws, "甲004-2"), "date", "R6.5.31")
    _apply(tmp_path, ws, config)

    again = _ws(tmp_path, config)
    rows = again.description_rows()
    assert [r.label for r in rows] == ["甲004-1", "甲004-2"]
    assert len({r.record.path for r in rows}) == 1  # 1つのファイルになった
    assert rows[1].data.date == "R6.5.31"
    assert [r.title for r in rows] == ["領収書（4月分）", "領収書（5月分）"]


def test_restore_moves_rows_back(tmp_path: Path, config):
    make_multipage_pdf(tmp_path / "甲001 契約書.pdf", 1)
    ws = _ws(tmp_path, config)
    ws.set_row_field(_row(ws, "甲001"), "author", "原告・被告")
    ws.rename(ws.eligible[0], "甲002 契約書.pdf")  # 証拠番号を変える
    _apply(tmp_path, ws, config)
    assert _row(_ws(tmp_path, config), "甲002").data.author == "原告・被告"

    entries = Manifest.load(tmp_path).entries
    restore_all(tmp_path)
    rekey_after_restore(tmp_path, entries)
    assert _row(_ws(tmp_path, config), "甲001").data.author == "原告・被告"


def test_rows_reattach_by_label_when_renamed_outside(tmp_path: Path, config):
    make_multipage_pdf(tmp_path / "甲001 契約書.pdf", 1)
    ws = _ws(tmp_path, config)
    ws.set_row_field(_row(ws, "甲001"), "date", "R5.10.1")
    (tmp_path / "甲001 契約書.pdf").rename(tmp_path / "甲001 売買契約書.pdf")  # 他のアプリで改名
    assert _row(_ws(tmp_path, config), "甲001").data.date == "R5.10.1"


def test_broken_input_is_not_overwritten(tmp_path: Path, config):
    make_multipage_pdf(tmp_path / "甲001 契約書.pdf", 1)
    data_path(tmp_path).parent.mkdir()
    data_path(tmp_path).write_text("{壊れた", encoding="utf-8")
    ws = _ws(tmp_path, config)
    assert ws.description_error
    with pytest.raises(ValueError):
        ws.set_description_header("court", "東京地方裁判所")
    assert data_path(tmp_path).read_text(encoding="utf-8") == "{壊れた"


# ---------------------------------------------------------------------- PDF
def test_pdf_layout_and_fonts(tmp_path: Path):
    header = Header(court="東京地方裁判所", case_number="令和8年（ワ）第123号", number="1",
                    date="令和8年11月18日", name="原告訴訟代理人弁護士　甲野太郎")
    rows = [
        Row("甲001", "売買契約書", CATEGORY_ORIGINAL, "R5.10.1", "原告・被告", "契約を結んだ事実"),
        Row("甲002", "領収書", CATEGORY_DIGITIZED, purpose="受領した事実", remarks="蛍光ペンは提出者"),
        Row("甲003", "文献", CATEGORY_DATA, span=True, purpose="長い立証趣旨。" * 600),
        Row("甲004", "最後の証拠"),
    ]
    out = build_pdf(header, rows, tmp_path / "証拠説明書（1）.pdf")
    body = _text(out)
    for word in ("東京地方裁判所", "令和8年（ワ）第123号", "原告", "甲野太郎", "民事訴訟法",
                 "甲001", "原本", "紙を電子化", "蛍光ペンは提出者", "最後の証拠"):
        assert word in body, word
    with pdfplumber.open(out) as pdf:
        assert len(pdf.pages) >= 3  # 1ページに収まらない行は、ページごとに分ける
        for page in pdf.pages:
            assert round(page.width) == 595 and round(page.height) == 842  # A4 縦
            # 見出しの行は、各ページに1回だけ。
            assert (page.extract_text() or "").replace(" ", "").count("立証趣旨") >= 1
            assert (page.extract_text() or "").replace(" ", "").count("号証") == 1
    from pypdf import PdfReader

    reader = PdfReader(out)
    used = set()
    import re

    for page in reader.pages:
        content = page.get_contents().get_data().decode("latin1")
        used |= set(re.findall(r"/(F\d+(?:\+\d+)?) [\d.]+ Tf", content))
        fonts = page["/Resources"]["/Font"]
        for name in used:
            font = fonts["/" + name].get_object()
            if "/DescendantFonts" in font:
                font = font["/DescendantFonts"][0].get_object()
            assert "/FontFile2" in font["/FontDescriptor"].get_object()  # 使う字は、すべて埋め込む


def test_changing_number_replaces_the_pdf(tmp_path: Path, config):
    """証拠説明書の番号を変えると PDF の名前が変わる。前に作った PDF は消して、作り直す。"""
    make_multipage_pdf(tmp_path / "甲001 契約書.pdf", 1)
    ws = _ws(tmp_path, config)
    ws.set_row_field(_row(ws, "甲001"), "purpose", "契約の事実")
    _apply(tmp_path, ws, config)
    first = tmp_path / "証拠説明書.pdf"
    assert first.exists()

    again = _ws(tmp_path, config)
    again.set_description_header("number", "2")
    plan = again.plan_update()
    assert "前に作った「証拠説明書.pdf」は消して" in describe_update_plan(plan, 200)
    _apply(tmp_path, again, config)
    assert not first.exists() and (tmp_path / "証拠説明書（2）.pdf").exists()


def test_click_on_preview_finds_the_row(tmp_path: Path):
    """証拠説明書の PDF のプレビューでクリックした位置から、その行の号証を探す（2026-11-18 班長指示）。"""
    from mints_prep.ui.description_sync import label_positions, row_label_at

    rows = [Row("甲001", "契約書", purpose="長い立証趣旨。" * 30), Row("甲002", "領収書"), Row("甲003", "写真")]
    out = build_pdf(Header(), rows, tmp_path / "証拠説明書.pdf")
    width, found, _heading = label_positions(out, 0)
    assert [label for _top, label in found] == ["甲001", "甲002", "甲003"]
    tops = {label: top for top, label in found}
    shown = width / 2  # プレビューの画像が、ページの半分の大きさで描かれているとき
    cache: dict = {}
    at = lambda top: row_label_at(cache, out, 0, 100, top / 2, shown)  # noqa: E731
    assert at(tops["甲001"] + 2) == "甲001"
    assert at(tops["甲001"] + 40) == "甲001"  # 行の途中（立証趣旨の2行目あたり）
    assert at(tops["甲002"] + 1) == "甲002"
    assert at(tops["甲003"] + 1) == "甲003"
    assert at(tops["甲001"] - 60) is None  # 表の上（頭書・見出し）


def test_write_pdf_without_update(tmp_path: Path, config):
    """［ファイルを更新］を押さなくても、証拠説明書の PDF を作り、一覧に出す（2026-11-18 班長指示）。
    中身が同じなら作らない。番号を変えて名前が変わったら、前の PDF を消して、一覧も替える。"""
    make_multipage_pdf(tmp_path / "甲001 契約書.pdf", 1)
    ws = _ws(tmp_path, config)
    first = ws.write_description_pdf()  # 入力のないフォルダでも作る（［作成］を押したとき）
    assert first == tmp_path / "証拠説明書.pdf" and first.exists()
    assert data_path(tmp_path).exists()
    listed = [r for r in ws.records if ws.is_description_file(r)]
    assert [r.path for r in listed] == [first]
    assert first not in {r.path for r in ws.eligible}
    assert ws.write_description_pdf() is None  # 変わっていなければ作らない

    ws.set_row_field(_row(ws, "甲001"), "purpose", "契約の事実")
    assert ws.write_description_pdf() == first
    assert "契約の事実" in _text(first)
    # ファイル名の下書き（標目の直し）も、すぐに PDF に入る（ファイル名は［ファイルを更新］まで変わらない）。
    ws.set_row_title(_row(ws, "甲001"), "売買契約書")
    ws.write_description_pdf()
    assert "売買契約書" in _text(first) and (tmp_path / "甲001 契約書.pdf").exists()

    ws.set_description_header("number", "3")
    second = ws.write_description_pdf()
    assert second == tmp_path / "証拠説明書（3）.pdf" and not first.exists()
    assert [r.path for r in ws.records if ws.is_description_file(r)] == [second]
    # 更新すると、ファイル名と PDF がそろう（PDF は作り直す）。
    _apply(tmp_path, ws, config)
    assert (tmp_path / "甲001 売買契約書.pdf").exists() and second.exists()
    assert _ws(tmp_path, config).plan_update().has_work is False


def test_delete_description_folder(tmp_path: Path, config):
    """一覧の［削除］：「証拠説明書」フォルダ（PDF と入力）ごと消す。消した後は、入力を変えても作らない
    （［作成］を押すまで）。ほかのファイルと一緒には消せない（2026-11-18 班長決定）。"""
    import shutil

    make_multipage_pdf(tmp_path / "甲001 契約書.pdf", 1)
    ws = _ws(tmp_path, config)
    ws.set_row_field(_row(ws, "甲001"), "purpose", "契約の事実")
    ws.write_description_pdf()
    pdf_record = next(r for r in ws.records if ws.is_description_file(r))
    evidence = next(r for r in ws.records if not ws.is_description_file(r))
    assert "一緒には削除できません" in ws.remove_error(pdf_record)
    with pytest.raises(ValueError):
        ws.remove([pdf_record, evidence])

    ws.delete_description(lambda path: shutil.rmtree(path) if path.is_dir() else path.unlink())
    assert not (tmp_path / DATA_DIRNAME).exists() and not (tmp_path / "証拠説明書.pdf").exists()
    assert not any(ws.is_description_file(r) for r in ws.records)
    assert not ws.description.exists
    assert ws.plan_update().description is None  # 更新しても作らない
    again = _ws(tmp_path, config)
    assert _row(again, "甲001").data.purpose == ""  # 入力も消えた


def test_user_file_with_same_name_is_not_overwritten(tmp_path: Path, config):
    """対象フォルダに、たまたま「証拠説明書.pdf」という名前の利用者のファイルがあれば、上書きしない。
    そのファイルは、このツールで作った証拠説明書とは見なさない（ほかのファイルと同じに扱う）。"""
    make_multipage_pdf(tmp_path / "甲001 契約書.pdf", 1)
    mine = make_multipage_pdf(tmp_path / "証拠説明書.pdf", 2)
    before = mine.read_bytes()
    ws = _ws(tmp_path, config)
    user = next(r for r in ws.records if r.path == mine)
    assert not ws.is_description_file(user)
    with pytest.raises(ValueError, match="同じ名前のファイル"):
        ws.write_description_pdf()
    assert mine.read_bytes() == before
    ws.set_description_header("number", "2")  # 番号を入れれば、別の名前で作れる
    made = ws.write_description_pdf()
    assert made == tmp_path / "証拠説明書（2）.pdf" and mine.read_bytes() == before


def test_split_puts_pdf_into_first_batch_and_restore_brings_it_back(tmp_path: Path, config):
    """200MB を超えて「提出用_NN」に分けるときは、証拠説明書を「提出用_01」に入れる。
    ［元のファイルに戻す］で、直下に戻す。"""
    import dataclasses

    small = dataclasses.replace(
        config, upload=dataclasses.replace(config.upload, max_total_mb=0.004, safety_margin_ratio=1.0)
    )
    make_multipage_pdf(tmp_path / "甲001 契約書.pdf", 3)
    make_multipage_pdf(tmp_path / "甲002 領収書.pdf", 3)
    ws = _ws(tmp_path, small)
    ws.write_description_pdf()
    assert (tmp_path / "証拠説明書.pdf").exists()
    plan = ws.plan_update()
    assert len(plan.batches) >= 2 and plan.description is not None
    assert "「提出用_01」フォルダの「証拠説明書.pdf」" in describe_update_plan(plan, 0.004)
    apply_all_updates(tmp_path, plan, small, ws)
    assert (tmp_path / "提出用_01" / "証拠説明書.pdf").exists()
    assert not (tmp_path / "証拠説明書.pdf").exists()
    again = _ws(tmp_path, small)
    assert again.is_description_file(next(r for r in again.records if r.path.name == "証拠説明書.pdf"))

    entries = Manifest.load(tmp_path).entries
    restore_all(tmp_path)
    rekey_after_restore(tmp_path, entries)
    assert (tmp_path / "証拠説明書.pdf").exists() and not (tmp_path / "提出用_01").exists()
    assert _ws(tmp_path, config).is_description_file(
        next(r for r in _ws(tmp_path, config).records if r.path.name == "証拠説明書.pdf")
    )


def test_description_pdf_is_listed_first(tmp_path: Path, config):
    """証拠説明書の PDF は、一覧のいちばん上に出す（2026-11-18 班長指示。200MB で分けるときの
    「提出用_01」〔先頭〕に合わせる）。作ったとき・読み込み直したときの、どちらも。"""
    make_multipage_pdf(tmp_path / "甲001 契約書.pdf", 1)
    make_multipage_pdf(tmp_path / "あいう.pdf", 1)
    ws = _ws(tmp_path, config)
    pdf = ws.write_description_pdf()
    assert ws.records[0].path == pdf
    again = _ws(tmp_path, config)
    assert again.records[0].path == pdf
    again.set_description_header("number", "2")
    second = again.write_description_pdf()
    assert again.records[0].path == second and pdf not in {r.path for r in again.records}


def test_rename_from_title_follows_the_guide(tmp_path: Path, config):
    """標目から作るファイル名は、準備の手引29頁の形にそろえる（2026-11-18 班長指示）：英数字は半角、
    証拠番号と標目の間は半角の空白1つ、証拠番号は「甲001」の形。"""
    make_multipage_pdf(tmp_path / "甲００２_領収書.pdf", 1)
    make_multipage_pdf(tmp_path / "甲003_写真.pdf", 1)
    ws = _ws(tmp_path, config)
    two = _row(ws, "甲002")
    ws.set_row_title(two, "領収書（令和５年１０月分）ＡＢＣ")
    assert ws.name_of(two.record) == "甲002 領収書（令和5年10月分）ABC.pdf"
    assert _row(ws, "甲002").title == "領収書（令和5年10月分）ABC"  # 証拠説明書の標目も半角
    three = _row(ws, "甲003")
    ws.set_row_category(three, CATEGORY_ORIGINAL)  # 原本・写しの別を変えたときも、区切りをそろえる
    assert ws.name_of(three.record) == "甲003 写真原本.pdf"


def test_click_on_continued_row_finds_it(tmp_path: Path):
    """1ページに収まらずに分けた行の、次のページの続きをクリックしても、その行の号証を返す
    （2026-11-18。続きの部分には号証の字がないため）。見出しの行の上は None。"""
    from mints_prep.ui.description_sync import label_positions, row_label_at

    rows = [Row("甲001", "文献", purpose="長い立証趣旨。" * 600), Row("甲002", "領収書")]
    out = build_pdf(Header(), rows, tmp_path / "証拠説明書.pdf")
    with pdfplumber.open(out) as pdf:
        pages = len(pdf.pages)
    assert pages >= 2
    width, found, heading_bottom = label_positions(out, 1)
    assert heading_bottom is not None
    shown = width  # ページと同じ大きさで描かれているとき
    cache: dict = {}
    assert row_label_at(cache, out, 1, 100, heading_bottom + 20, shown) == "甲001"
    assert row_label_at(cache, out, 1, 100, heading_bottom - 25, shown) is None  # 見出しの行
    last, *_ = label_positions(out, pages - 1)[1][-1:] or [(0, "")]
    assert row_label_at(cache, out, pages - 1, 100, last[0] + 2, shown) == "甲002"

