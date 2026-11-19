"""ウィンドウの大きさ・位置と、最大化していたかを覚える（Ver300。2026-11-17 班長指摘
「ウインドウを最大化して終了したことが、保存されない」）。"""

from pathlib import Path

from va_mints.user_settings import (
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


def test_maximized_window_is_saved_on_close_and_restored(monkeypatch, app):
    """閉じるときに覚え、次に起動したときに戻す。画面は試験の全体で1つなので（conftest）、閉じる（destroy）
    の代わりに何もしない手順にし、起動したときの戻し（_restore_window）を、覚えた設定を読み直して行う。"""
    monkeypatch.setattr(app, "destroy", lambda: None)
    app.deiconify()
    app.geometry("1500x800+40+50")
    app.update()
    app.state("zoomed")
    app.update()
    app._on_close()  # 下書きがないので、そのまま閉じる（設定は、この試験の一時フォルダに書く）
    saved = load_user_settings(settings_path())
    assert saved.window_maximized is True
    # 最大化を解いたときに戻る大きさは、最大化する前の大きさ
    assert parse_geometry(saved.window_geometry)[:2] == (1500, 800)

    # 次に起動したとき
    app.state("normal")
    app.geometry("700x500+60+70")
    app.update()
    app._user_settings = load_user_settings()
    app._restore_window()
    app.update()
    assert app.state() == "zoomed"
    app.state("normal")
    app.update()
    assert (app.winfo_width(), app.winfo_height()) == (1500, 800)
    app._on_close()
    saved = load_user_settings(settings_path())
    assert saved.window_maximized is None and parse_geometry(saved.window_geometry)[:2] == (1500, 800)
