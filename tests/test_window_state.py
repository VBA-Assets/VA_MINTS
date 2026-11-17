"""ウィンドウの大きさ・位置と、最大化していたかを覚える（Ver300。2026-11-17 班長指摘
「ウインドウを最大化して終了したことが、保存されない」）。"""

from pathlib import Path

import pytest

from mints_prep.user_settings import (
    UserSettings,
    load_user_settings,
    parse_geometry,
    save_user_settings,
    settings_path,
)


def test_window_fields_round_trip_and_keep_other_settings(tmp_path: Path):
    path = tmp_path / "settings.json"
    save_user_settings(
        UserSettings(stamp_font_size_pt=20, window_geometry="1200x700+-8+30", window_maximized=True), path
    )
    loaded = load_user_settings(path)
    assert loaded == UserSettings(stamp_font_size_pt=20, window_geometry="1200x700+-8+30", window_maximized=True)
    assert parse_geometry(loaded.window_geometry) == (1200, 700, -8, 30)


def test_broken_window_fields_are_ignored(tmp_path: Path):
    path = tmp_path / "settings.json"
    path.write_text('{"window_geometry": "big", "window_maximized": "yes"}', encoding="utf-8")
    assert load_user_settings(path) == UserSettings()


def _window():
    pytest.importorskip("tkinter")
    import tkinter as tk

    from mints_prep.ui.app import MainWindow

    try:
        return MainWindow()
    except tk.TclError:
        pytest.skip("画面がない環境")


def test_maximized_window_is_saved_on_close_and_restored(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))  # 本物の設定ファイルを汚さない
    app = _window()
    try:
        app.geometry("1000x600+40+50")
        app.update()
        app.state("zoomed")
        app.update()
        app._on_close()  # 下書きがないので、そのまま閉じる
    except BaseException:
        app.destroy()
        raise
    saved = load_user_settings(settings_path())
    assert saved.window_maximized is True
    # 最大化を解いたときに戻る大きさは、最大化する前の大きさ
    assert parse_geometry(saved.window_geometry)[:2] == (1000, 600)

    app = _window()
    try:
        app.update()
        assert app.state() == "zoomed"
        app.state("normal")
        app.update()
        assert (app.winfo_width(), app.winfo_height()) == (1000, 600)
        app._on_close()
    except BaseException:
        app.destroy()
        raise
    saved = load_user_settings(settings_path())
    assert saved.window_maximized is None and parse_geometry(saved.window_geometry)[:2] == (1000, 600)
