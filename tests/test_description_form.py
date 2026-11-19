"""証拠説明書の画面（4.17。ui/description_form.py・ui/description_sync.py）：［作成］、入力の保存と PDF の
作り直し、標目でのファイル名の変更、F5、プレビューのクリック、［削除］。"""

from pathlib import Path

import pytest

from fixtures.generate_fixtures import make_multipage_pdf


def _load(app, folder: Path) -> None:
    """［ファイルを読み込み］と同じ状態にする（裏の読み込みを待たずに）。"""
    from va_mints.processors.updater import Manifest
    from va_mints.scanner.walker import scan_folder
    from va_mints.ui.workspace import Workspace

    result = scan_folder(folder, app.config_obj)
    app._scan_result = result
    app._workspace = Workspace(result, app.config_obj, Manifest.load(folder))
    app._populate_file_list(result)
    app._sync_description_button()
    app.update()


def _select(app, name: str) -> None:
    iid = next(i for i, r in app._row_records.items() if r.path.name == name)
    app._tree.selection_set(iid)
    app._tree.focus(iid)
    app._on_select_file()
    app.update()


def test_create_shows_form_and_saves_input(tmp_path: Path, monkeypatch, app):
    folder = tmp_path / "証拠"
    folder.mkdir()
    make_multipage_pdf(folder / "甲001 契約書.pdf", 1)
    make_multipage_pdf(folder / "原告準備書面1.pdf", 1)
    try:
        app.withdraw()
        _load(app, folder)
        _select(app, "甲001 契約書.pdf")
        assert "disabled" not in app._desc_button.state()  # まだ作っていないので押せる
        app._create_description()
        app.update()
        # 作った後は、いつも入力欄を出し、［作成］は押せない（2026-11-18 班長指示）。
        assert app._description_active and "disabled" in app._desc_button.state()
        assert app._desc_button.cget("text") == "作成"
        # ふだんの項目（処理の項目）は残し、その下に証拠説明書の入力欄を足す（2026-11-18 班長指示）。
        texts = _texts(app)
        assert "立証趣旨" in texts
        # 1行のファイルでは、号証の見出しと説明の文は出さない（2026-11-18 班長指示）。
        assert "甲001" not in texts and not any(t.startswith("入力は") for t in texts)
        # 中央は、選んだファイルのプレビューのまま（証拠を見ながら入力する。2026-11-18 班長指示）。
        assert app._strip.winfo_manager() == "pack"
        assert app._current.path.name == "甲001 契約書.pdf"

        ws = app._workspace
        row = ws.description_rows()[0]
        app._queue_description_input("x", lambda: app._save_row_field(row, "purpose", "契約の事実"))
        app._flush_description_inputs()  # 待っている保存は、画面を替える前にすぐ行う
        assert (folder / "証拠説明書データ" / "証拠説明書.json").exists()

        # 標目を直すと、ファイル名が変わる（下書き）。
        import tkinter as tk

        var = tk.StringVar(master=app, value="売買契約書")
        app._commit_title(ws.description_rows()[0], var.get, var.set)
        assert any(ws.name_of(r) == "甲001 売買契約書.pdf" for r in ws.eligible)

        # 「標目をまたがせる」にチェックを入れると、標目の欄が立証趣旨と同じ高さになる（2026-11-18 班長指示）。
        def tall_texts() -> int:
            def walk(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from walk(child)

            return sum(
                1 for w in walk(app._options_frame)
                if w.winfo_class() == "Text" and int(w.cget("height")) == 5
            )

        assert tall_texts() == 1  # 立証趣旨だけ
        app._save_row_field(ws.description_rows()[0], "span", True)
        app._rebuild_options()
        assert tall_texts() == 2  # 標目も

        # 証拠番号のないファイルを選んでも、落ちない（ファイルのプレビューのまま）。
        _select(app, "原告準備書面1.pdf")
        assert app._current.path.name == "原告準備書面1.pdf"

        # 入力欄を隠す機能はない。読み込み直しても、入力欄はいつも出る。
        _load(app, folder)
        _select(app, "甲001 契約書.pdf")  # 名前の変更は下書きだったので、読み込み直すと元の名前
        assert app._description_active and "立証趣旨" in _texts(app)
    finally:
        app.withdraw()


def _texts(app) -> list[str]:
    """右のペインに出ている文（ラベル）。"""
    def walk(widget):
        for child in widget.winfo_children():
            yield child
            yield from walk(child)

    return [w.cget("text") for w in walk(app._options_frame) if w.winfo_class() in ("TLabel", "Label")]


def test_header_is_entered_on_the_made_pdf(tmp_path: Path, monkeypatch, app):
    """頭書（裁判所・事件番号など）は、作った証拠説明書の PDF を選んだときに入れる（各証拠の入力欄には
    出さない。2026-11-18 班長指示）。"""
    from va_mints.ui.workspace import apply_all_updates

    folder = tmp_path / "証拠"
    folder.mkdir()
    make_multipage_pdf(folder / "甲001 契約書.pdf", 1)
    try:
        app.withdraw()
        _load(app, folder)
        _select(app, "甲001 契約書.pdf")
        app._create_description()
        app.update()
        assert "頭書" not in _texts(app) and "裁判所" not in _texts(app)
        ws = app._workspace
        app._save_row_field(ws.description_rows()[0], "purpose", "契約の事実")
        apply_all_updates(folder, ws.plan_update(), app.config_obj, ws)

        _load(app, folder)  # 読み込み直すと、作った PDF が一覧に出る
        _select(app, "証拠説明書.pdf")
        assert "頭書" in _texts(app) and "裁判所" in _texts(app)
        assert "甲001" in _texts(app) and "立証趣旨" in _texts(app)  # 全部の行の入力欄も並べる
        app._save_header("court", "東京地方裁判所")
        assert app._workspace.description.header.court == "東京地方裁判所"
    finally:
        app.withdraw()


def test_wheel_over_text_scrolls_the_pane(tmp_path: Path, monkeypatch, app):
    """複数行の欄の上のホイール：欄の中に隠れた行がなければ、右ペインを送る（2026-11-18。欄の上で
    右ペインが動かなかった）。隠れた行があれば、欄の中を送る。"""
    import tkinter as tk
    from types import SimpleNamespace

    try:
        app.withdraw()
        sent = []
        monkeypatch.setattr(app, "_options_wheel", lambda event: sent.append(event.delta) or "break")
        text = tk.Text(app._options_frame, height=2)
        text.pack()
        app._bind_wheel(app._options_frame)
        app.update()

        # 短い文（隠れた行がない）→ 右ペインを送る
        text.insert("1.0", "短い立証趣旨")
        app.update()
        assert app._text_wheel(SimpleNamespace(widget=text, delta=-120)) == "break"
        assert sent == [-120]

        # 長い文（隠れた行がある）→ 下へは欄の中を送り、上の端では右ペインを送る
        text.delete("1.0", "end")
        text.insert("1.0", "\n".join(f"{i}行目" for i in range(30)))
        text.yview_moveto(0)
        app.update()
        app._text_wheel(SimpleNamespace(widget=text, delta=-120))
        assert sent == [-120] and text.yview()[0] > 0
        text.yview_moveto(0)
        app.update()
        app._text_wheel(SimpleNamespace(widget=text, delta=120))
        assert sent == [-120, 120]
    finally:
        app.withdraw()


def test_create_makes_the_pdf_and_lists_it(tmp_path: Path, monkeypatch, app):
    """［作成］を押すと、証拠説明書の PDF を作り、左の一覧に出す。入力を変えると、
    少し待ってから作り直す（2026-11-18 班長指示）。"""
    folder = tmp_path / "証拠"
    folder.mkdir()
    make_multipage_pdf(folder / "甲001 契約書.pdf", 1)
    try:
        app.withdraw()
        _load(app, folder)
        pdf = folder / "証拠説明書.pdf"  # 対象フォルダの直下
        assert not pdf.exists()
        app._create_description()
        app.update()
        assert pdf.exists()
        assert any(r.path == pdf for r in app._row_records.values())  # 左の一覧に出る
        first = app._tree.get_children()[0]
        assert app._row_records[first].path == pdf  # いちばん上に（2026-11-18 班長指示）

        _select(app, "証拠説明書.pdf")
        before = pdf.stat().st_mtime_ns
        app._save_header("court", "東京地方裁判所")
        app._sync_description()  # 待たずに作り直す（ふだんは少し待ってから）
        app.update()
        assert pdf.stat().st_mtime_ns != before
        assert app._current.path == pdf  # 見ていた PDF を選んだまま
    finally:
        app.withdraw()


def test_click_on_header_scrolls_to_top(tmp_path: Path, monkeypatch, app):
    """証拠説明書の PDF のプレビューで、頭書（1ページ目の表より上）をクリックすると、右のペインを先頭へ、
    行をクリックすると、その行の入力欄へ送る（2026-11-18 班長指示）。"""
    from types import SimpleNamespace

    from va_mints.ui.description_sync import label_positions

    folder = tmp_path / "証拠"
    folder.mkdir()
    make_multipage_pdf(folder / "甲001 契約書.pdf", 1)
    make_multipage_pdf(folder / "甲002 領収書.pdf", 1)
    try:
        app.withdraw()
        _load(app, folder)
        app._create_description()  # PDF を作り、一覧に出す
        app.update()
        _select(app, "証拠説明書.pdf")
        pdf = app._current.path
        page_width, found, _heading = label_positions(pdf, 0)
        shown_w, shown_h = 400, 566  # プレビューの画像の大きさ（枠の線を含む）
        app._preview_shown = (pdf, 0, (shown_w, shown_h))
        moved = []
        app._options_canvas.yview_moveto = lambda fraction: moved.append(fraction)
        widget = app._preview_label
        scale = (shown_w - 2) / page_width

        def click(top_pt: float) -> None:
            x = (widget.winfo_width() - shown_w) / 2 + 1 + 100
            y = (widget.winfo_height() - shown_h) / 2 + 1 + top_pt * scale
            app._on_description_preview_click(SimpleNamespace(x=x, y=y))

        click(found[0][0] - 60)  # 頭書
        assert moved == [0.0]
        click(found[1][0] + 2)  # 甲002 の行
        assert len(moved) == 2
    finally:
        app.withdraw()


def test_delete_key_on_description_moves_folder(tmp_path: Path, monkeypatch, app):
    """一覧で証拠説明書の PDF を選んで［削除］：確認のうえ、フォルダごと消し、［作成］がまた押せる。"""
    import shutil
    from tkinter import messagebox

    from va_mints.ui import platform_support

    folder = tmp_path / "証拠"
    folder.mkdir()
    make_multipage_pdf(folder / "甲001 契約書.pdf", 1)
    try:
        app.withdraw()
        _load(app, folder)
        app._create_description()  # PDF を作る
        app.update()
        _select(app, "証拠説明書.pdf")
        moved = []
        monkeypatch.setattr(platform_support, "send_to_recycle_bin",
                            lambda path: (moved.append(path),
                                          shutil.rmtree(path) if path.is_dir() else path.unlink()))
        monkeypatch.setattr(messagebox, "askokcancel", lambda *a, **k: False)
        app._remove_files()  # 「キャンセル」なら消さない
        assert (folder / "証拠説明書.pdf").exists() and not moved
        monkeypatch.setattr(messagebox, "askokcancel", lambda *a, **k: True)
        app._remove_files()
        app.update()
        assert moved == [folder / "証拠説明書.pdf", folder / "証拠説明書データ"]
        assert not (folder / "証拠説明書.pdf").exists() and not (folder / "証拠説明書データ").exists()
        assert not app._description_active
        assert "disabled" not in app._desc_button.state()  # また［作成］を押せる
        assert all(r.path.name != "証拠説明書.pdf" for r in app._workspace.records)
        assert app._current is not None and app._current.path.name == "甲001 契約書.pdf"
    finally:
        app.withdraw()


def test_title_commits_while_typing_and_f5_syncs(tmp_path: Path, monkeypatch, app):
    """標目は、欄を離れなくても、字を入れ終えて少し待つと確定する（2026-11-18。プレビューの上を
    クリックしても確定せず、PDF に反映されなかった）。F5 で、待っている保存をすぐ行い、PDF を最新にする。"""
    import pdfplumber

    folder = tmp_path / "証拠"
    folder.mkdir()
    make_multipage_pdf(folder / "甲001 契約書.pdf", 1)
    try:
        app.withdraw()
        _load(app, folder)
        _select(app, "甲001 契約書.pdf")
        app._create_description()
        app.update()

        def walk(widget):
            for child in widget.winfo_children():
                yield child
                yield from walk(child)

        title = next(w for w in walk(app._options_frame) if w.winfo_class() == "TEntry")
        title.delete(0, "end")
        title.insert(0, "売買契約書")  # 欄を離れない（Enter も押さない）
        assert app._on_key_f5() == "break"  # F5
        ws = app._workspace
        assert any(ws.name_of(r) == "甲001 売買契約書.pdf" for r in ws.eligible)
        with pdfplumber.open(folder / "証拠説明書.pdf") as pdf:
            assert "売買契約書" in (pdf.pages[0].extract_text() or "").replace(" ", "")
        assert app._current.path.name == "甲001 契約書.pdf"  # 選んでいるファイルは、そのまま
    finally:
        app.withdraw()


def test_header_fields_one_after_another_reach_the_pdf(tmp_path: Path, monkeypatch, app):
    """頭書の欄を続けて入れても（裁判所 → 作り直し → 事件番号）、両方が保存され、PDF に入る
    （2026-11-18 班長の報告。「裁判所」の後の欄が PDF に入らなかった。入力の流れを1本にした）。"""
    import gc

    import pdfplumber

    folder = tmp_path / "証拠"
    folder.mkdir()
    make_multipage_pdf(folder / "甲001 契約書.pdf", 1)
    try:
        app.withdraw()
        _load(app, folder)
        app._create_description()
        app.update()
        _select(app, "証拠説明書.pdf")  # 頭書と全部の行の欄

        def entries():
            def walk(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from walk(child)

            return [w for w in walk(app._options_frame) if w.winfo_class() == "TEntry"]

        fields = entries()
        court, case_number = fields[0], fields[1]  # 頭書の「裁判所」「事件番号」
        court.insert(0, "東京地方裁判所")
        app._sync_description()  # 自動の作り直しと同じ道（待ち時間だけを省く）
        gc.collect()
        case_number.insert(0, "令和8年（ワ）第123号")
        app._sync_description()
        header = app._workspace.description.header
        assert (header.court, header.case_number) == ("東京地方裁判所", "令和8年（ワ）第123号")
        with pdfplumber.open(folder / "証拠説明書.pdf") as pdf:
            first = (pdf.pages[0].extract_text() or "").split("\n")[0]
        assert "東京地方裁判所" in first and "令和8年（ワ）第123号" in first
    finally:
        app.withdraw()


def test_preview_follows_the_pdf_after_its_number_changes(tmp_path: Path, monkeypatch, app):
    """証拠説明書の番号を入れて PDF の名前が変わっても（「証拠説明書（1）.pdf」）、その後に入れた頭書が
    プレビューに入る（2026-11-18 班長の報告。番号を入れた後は、F5 を押すまで描き直されなかった。
    作り直した後では、見ていた古い名前のファイルが証拠説明書の PDF と分からなくなっていた）。"""
    folder = tmp_path / "証拠"
    folder.mkdir()
    make_multipage_pdf(folder / "甲001 契約書.pdf", 1)
    try:
        app.withdraw()
        _load(app, folder)
        app._create_description()
        app.update()
        _select(app, "証拠説明書.pdf")

        def entries():
            def walk(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from walk(child)

            return [w for w in walk(app._options_frame) if w.winfo_class() == "TEntry"]

        fields = entries()
        case_number, number = fields[1], fields[2]  # 頭書の「事件番号」「証拠説明書の番号」
        number.insert(0, "1")
        app._sync_description()  # 自動の作り直しと同じ道（待ち時間だけを省く）
        renamed = folder / "証拠説明書（1）.pdf"
        assert renamed.exists() and not (folder / "証拠説明書.pdf").exists()
        assert app._current.path == renamed  # 新しい名前の PDF を見ている
        assert app._desc_title_label.cget("text") == renamed.name
        selected = app._tree.selection()
        assert selected and app._row_records[selected[0]].path == renamed

        redrawn = []
        monkeypatch.setattr(app, "_refresh_current_view", lambda: redrawn.append(app._current.path))
        case_number.insert(0, "令和8年（ワ）第123号")
        app._sync_description()
        assert redrawn == [renamed]  # 番号を入れた後の欄も、プレビューを描き直す
    finally:
        app.withdraw()
