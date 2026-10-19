import pytest

pytest.importorskip("tkinter")

import tkinter as tk  # noqa: E402
from tkinter import ttk  # noqa: E402

from mints_prep.ui.theme import (  # noqa: E402
    ACCENT,
    CONTROL_BG,
    HOVER_BG,
    SCROLL_THUMB,
    SCROLL_TROUGH,
    WINDOW_BG,
    apply_theme,
)


@pytest.fixture(scope="module")
def root():
    r = tk.Tk()
    r.withdraw()
    apply_theme(r)
    yield r
    r.destroy()


def test_clam_theme_selected(root: tk.Tk):
    # Windows既定テーマはスタイル上書きが効かないため、clamを基盤にする。
    assert ttk.Style(root).theme_use() == "clam"


def test_window_background_matches_theme(root: tk.Tk):
    assert root.cget("background") == WINDOW_BG


def test_button_uses_flat_control_colors(root: tk.Tk):
    style = ttk.Style(root)
    assert style.lookup("TButton", "background") == CONTROL_BG
    # ホバー（active）で淡い青の面＋青いアクセント枠になる。
    assert style.lookup("TButton", "background", ["active"]) == HOVER_BG
    assert style.lookup("TButton", "bordercolor", ["active"]) == ACCENT


def test_entry_focus_border_uses_accent(root: tk.Tk):
    style = ttk.Style(root)
    assert style.lookup("TEntry", "fieldbackground") == "white"
    assert style.lookup("TEntry", "bordercolor", ["focus"]) == ACCENT


def test_emphasis_button_keeps_bold_font(root: tk.Tk):
    font = ttk.Style(root).lookup("Emphasis.TButton", "font")
    assert "bold" in str(font)


def test_scrollbar_thumb_darker_than_trough(root: tk.Tk):
    # レール（trough）よりスライダー（つまみ）の方が濃い配色であること
    # （clam既定の反転した配色〔レールが濃い〕になっていないこと）。
    style = ttk.Style(root)
    assert style.lookup("TScrollbar", "troughcolor") == SCROLL_TROUGH
    assert style.lookup("TScrollbar", "background") == SCROLL_THUMB
    # 16進カラーの数値比較で「つまみの方が濃い（値が小さい）」ことを確認する。
    assert int(SCROLL_THUMB.lstrip("#"), 16) < int(SCROLL_TROUGH.lstrip("#"), 16)
