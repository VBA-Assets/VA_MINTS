"""リボン（VA Case シリーズと同じ形）の試験。"""

import pytest

pytest.importorskip("tkinter")

import tkinter as tk  # noqa: E402
from tkinter import ttk  # noqa: E402

from mints_prep.ui import theme  # noqa: E402
from mints_prep.ui.icons import BUTTON_ICONS, ICON_DIR, IconSet, icon_name  # noqa: E402
from mints_prep.ui.ribbon import Ribbon  # noqa: E402
from mints_prep.ui.tooltip import Tooltip, _by_sentence  # noqa: E402


@pytest.fixture(scope="module")
def root():
    try:
        r = tk.Tk()
    except tk.TclError:
        pytest.skip("画面がない環境")
    r.withdraw()
    theme.apply_theme(r)
    yield r
    r.destroy()


def test_every_button_icon_has_images():
    for name in set(BUTTON_ICONS.values()):
        assert (ICON_DIR / f"{name}.png").exists() and (ICON_DIR / f"{name}_disabled.png").exists(), name
    assert (ICON_DIR / "LICENSE").exists()                      # Fluent UI System Icons（MIT License）
    assert icon_name("ファイルを読み込み") == "folder_open"
    assert icon_name("ファイルを更新") == "save"
    assert icon_name("元のファイルに戻す") == "arrow_undo"


def test_ribbon_groups_and_buttons(root):
    icons = IconSet(root)
    rb = Ribbon(root, icons)
    rb.pack(fill="x")
    g = rb.group("読み込み")
    big = g.large("ファイルを読み込み", lambda: None, style="RibbonAccent.TButton")
    g2 = rb.group("更新")
    out = g2.large("ファイルを更新", lambda: None, style="RibbonAccent.TButton")
    root.update()
    assert str(big.cget("compound")) == "top"
    assert big.cget("image") and out.cget("image")
    assert str(big.cget("takefocus")) in ("0", "False")
    out.state(["disabled"])                                     # 押せないときは灰色のアイコン
    assert "disabled" in str(out.cget("image"))
    assert [g.title for g in rb.groups] == ["読み込み", "更新"]
    rb.reflow(60)                                               # 狭いときは、組を次の段に送る
    # （update() を挟むと、非表示の窓では幅 1 の Configure で1段に戻るため、置き直した直後に確かめる）
    assert g.winfo_manager() == "grid" and g2.grid_info()["in"] != g.grid_info()["in"]
    rb.destroy()


def test_tooltips(root):
    """マウスを重ねたときの説明書き。説明は、文字列か、いまの説明を返す関数。"""
    b = ttk.Button(root, text="ファイルを書き出し")
    b.pack()
    state = {"busy": False}
    t = Tooltip(b, lambda: "処理中" if state["busy"] else "書き出します")
    assert t.current_text() == "書き出します"
    state["busy"] = True
    assert t.current_text() == "処理中"                        # 字が変わるボタンは、説明も変わる
    assert _by_sentence("一つ。二つ。") == "一つ。\n二つ。"       # 文ごとに改行する
    assert Tooltip(ttk.Button(root), "").current_text() == ""  # 空の説明は出さない
    b.destroy()


def test_ribbon_accent_style(root):
    """主要な操作（読み込み・書き出し）のボタンは、いつも青い枠（班長の UI 方針：主要な操作ボタンは
    枠線の色をアクセント色にする）。ほかのリボンのボタンは、ふだんは枠も地もない。"""
    style = ttk.Style(root)
    assert style.lookup("RibbonAccent.TButton", "bordercolor", ["!disabled"]) == theme.ACCENT
    assert style.lookup("Ribbon.TButton", "background") == theme.WINDOW_BG
    assert style.lookup("Ribbon.TButton", "background", ["active"]) == theme.HOVER_BG
