"""画面全体のテーマ設定（フラットデザイン）。

Microsoft製品（Word等のダイアログ画面）の配色・質感に近づけるための
フォント・配色・部品スタイルを一元管理する。一次資料に画面デザインの
定めはなく、いずれも実装上の選択。

ttk.Styleはインタープリタ（Tkインスタンス）単位で共有されるため、
MainWindow生成時にapply_theme()を一度呼べば、Toplevelダイアログ
（write_review_dialog・checklist_dialog等）にも同じテーマが適用される。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

# Microsoft系のアクセント青（フォーカス・ホバー時の枠線）
ACCENT = "#0078d4"
# ウィンドウ背景（Wordのダイアログ相当の明るいグレー）
WINDOW_BG = "#f0f0f0"
# ボタンの面（背景よりわずかに白寄りにして浮かせる）
CONTROL_BG = "#fdfdfd"
# ホバー時の面（淡い青）
HOVER_BG = "#e0eef9"
# 押下時の面（ホバーより一段濃い青）
PRESSED_BG = "#cce4f7"
# 通常時の枠線（細いグレー）
BORDER = "#c8c8c8"
# 文字色（純黒より柔らかい黒）
TEXT = "#1a1a1a"
# スクロールバー：レール（trough）は背景と同系の薄いグレー、スライダー
# （つまみ）はそれより濃いグレーとし、つまみの方が目立つようにする
# （Windows・ブラウザ標準のスクロールバーの配色に倣う）。
SCROLL_TROUGH = "#f0f0f0"
SCROLL_THUMB = "#c1c1c1"
SCROLL_THUMB_ACTIVE = "#a8a8a8"
SCROLL_THUMB_PRESSED = "#8a8a8a"

# Windows標準の日本語UIフォント（Word等のダイアログと同じ）。
# 搭載されていない環境では既定フォントのまま変更しない。
_PREFERRED_FONT_FAMILY = "Yu Gothic UI"
_FONT_SIZE = 9


def apply_theme(root: tk.Tk) -> None:
    """テーマを適用する。MainWindowの__init__で、ウィジェット生成前に一度呼ぶ。"""
    families = set(tkfont.families(root))
    base_family = _PREFERRED_FONT_FAMILY if _PREFERRED_FONT_FAMILY in families else ""
    if base_family:
        # 名前付きフォントを変更すると、フォント未指定の全ウィジェット
        # （tk・ttk双方）に波及する。
        for name in ("TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont"):
            tkfont.nametofont(name).configure(family=base_family, size=_FONT_SIZE)

    style = ttk.Style(root)
    # Windows既定テーマ（vista等）はOSネイティブの立体装飾を描画し、スタイル
    # の上書きがほぼ効かないため、指定どおり描画されるclamを基盤にする。
    if "clam" in style.theme_names():
        style.theme_use("clam")

    root.configure(background=WINDOW_BG)
    style.configure(".", background=WINDOW_BG, foreground=TEXT, bordercolor=BORDER, focuscolor=ACCENT)

    # ボタン：白系の面＋細いグレー枠線。ホバーで淡い青の面＋青枠、押下で
    # 一段濃い青（Word・Office系ダイアログのボタン挙動に倣う）。
    # clamの立体感はlightcolor/darkcolorを面と同色にして消す。
    style.configure(
        "TButton",
        background=CONTROL_BG,
        bordercolor=BORDER,
        lightcolor=CONTROL_BG,
        darkcolor=CONTROL_BG,
        padding=(10, 3),
    )
    style.map(
        "TButton",
        background=[("pressed", PRESSED_BG), ("active", HOVER_BG)],
        bordercolor=[("pressed", ACCENT), ("active", ACCENT)],
        lightcolor=[("pressed", PRESSED_BG), ("active", HOVER_BG)],
        darkcolor=[("pressed", PRESSED_BG), ("active", HOVER_BG)],
    )
    # 主要操作ボタン（読み込み・書き出し）：太字のみで強調（配色は共通）。
    style.configure("Emphasis.TButton", font=(base_family, 10, "bold"), foreground="black")

    # 入力欄：白背景＋細いグレー枠線、フォーカス時は青枠（Word系の入力欄に倣う）。
    style.configure(
        "TEntry",
        fieldbackground="white",
        bordercolor=BORDER,
        lightcolor="white",
        darkcolor="white",
        insertcolor=TEXT,
        padding=4,
    )
    style.map("TEntry", bordercolor=[("focus", ACCENT)])

    # スクロールバー：clamの既定はレールが濃くスライダーが薄い反転した
    # 配色で使いにくいため、レールを薄く・スライダーを濃くする（ホバー・
    # ドラッグ中はさらに濃く）。
    style.configure(
        "TScrollbar",
        troughcolor=SCROLL_TROUGH,
        background=SCROLL_THUMB,
        bordercolor=SCROLL_TROUGH,
        lightcolor=SCROLL_THUMB,
        darkcolor=SCROLL_THUMB,
        arrowcolor="#606060",
    )
    style.map(
        "TScrollbar",
        background=[("pressed", SCROLL_THUMB_PRESSED), ("active", SCROLL_THUMB_ACTIVE)],
        lightcolor=[("pressed", SCROLL_THUMB_PRESSED), ("active", SCROLL_THUMB_ACTIVE)],
        darkcolor=[("pressed", SCROLL_THUMB_PRESSED), ("active", SCROLL_THUMB_ACTIVE)],
    )

    # LabelFrame（スタンプ位置調整等）：細いグレー枠のみのフラットな囲み。
    style.configure(
        "TLabelframe",
        background=WINDOW_BG,
        bordercolor=BORDER,
        lightcolor=WINDOW_BG,
        darkcolor=WINDOW_BG,
    )
    style.configure("TLabelframe.Label", background=WINDOW_BG, foreground=TEXT)
