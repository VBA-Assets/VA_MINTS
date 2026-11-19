"""対象フォルダの欄（2行分の高さ。2026-11-17 班長指示）。"""

from pathlib import Path


def _box(app):
    import tkinter as tk

    def walk(widget):
        for child in widget.winfo_children():
            yield child
            yield from walk(child)

    return next(w for w in walk(app) if isinstance(w, tk.Text) and int(w.cget("height")) == 2)


def test_folder_box_is_two_lines_and_follows_the_folder_value(tmp_path: Path, monkeypatch, app):
    try:
        app.withdraw()
        box = _box(app)
        # ［...］で選んだとき（変数が変わる）→ 欄に出る
        app._input_dir_var.set(r"C:\事件\甲事件\証拠")
        assert box.get("1.0", "end-1c") == r"C:\事件\甲事件\証拠"
        # 貼り付けたとき（欄が変わる）→ 改行は外して、変数に入る
        box.delete("1.0", "end")
        box.insert("1.0", '"C:\\事件\\乙事件\\証拠"\r\n')
        app.update()
        assert app._input_dir_var.get() == '"C:\\事件\\乙事件\\証拠"'
        # エクスプローラーの［パスのコピー］の「"」は外して読む
        assert app._input_dir == Path(r"C:\事件\乙事件\証拠")
        # 欄で文字を入れている間は、Ctrl+Z などをページの編集に回さない
        app.focus_get = lambda: box  # 隠した画面では入力先を移せないので、入力先だけを差し替える
        assert app._typing()
    finally:
        app.withdraw()
