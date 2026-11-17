"""画面全体のテーマ設定（フラットデザイン）。

Microsoft製品（Word等のダイアログ画面）の配色・質感に近づけるための
フォント・配色・部品スタイルを一元管理する。一次資料に画面デザインの
定めはなく、いずれも実装上の選択。

ttk.Styleはインタープリタ（Tkインスタンス）単位で共有されるため、
MainWindow生成時にapply_theme()を一度呼べば、Toplevelダイアログ
（完了のお知らせ等）にも同じテーマが適用される。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

# Microsoft系のアクセント青（フォーカス・ホバー時の枠線）
ACCENT = "#0078d4"
# ウィンドウ背景（Wordのダイアログ相当の明るいグレー）
WINDOW_BG = "#f0f0f0"
# 入力欄・カード等の背景
FIELD_BG = "#ffffff"
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
# 補足・注記テキスト
NOTE = "#666666"
# 押せないボタン等の文字色
DISABLED_TEXT = "#a0a0a0"
# リボンのボタンのアイコン（VA Case・VA Document と同じ色。班長の UI 設計方針のフラットデザイン）
ICON_TINT = "#9cc9f0"            # アイコンの塗りつぶし版を敷く色（薄い青）
ICON_TINT_DISABLED = "#e2e2e2"   # 押せないときの、塗りつぶし版の色
RIBBON_HOVER_BORDER = "#a9cdee"  # リボンのボタンに重ねたときの枠
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


def scale(widget: tk.Misc) -> float:
    """画面の拡大率（96 dpi を 1.0 とする）。画素で決めた幅・高さに掛けて使う（VA Case と同じ）。"""
    try:
        return max(1.0, widget.winfo_fpixels("1i") / 96.0)
    except Exception:
        return 1.0


def px(widget: tk.Misc, n: int) -> int:
    """96 dpi のときの n 画素を、いまの画面の画素にする。"""
    return int(round(n * scale(widget)))


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
    style.map(".", foreground=[("disabled", DISABLED_TEXT)])

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
    # 主要な操作ボタン（お知らせの OK など）：地色は変えず、枠線だけをアクセント色にする
    # （班長の UI 設計方針）。
    style.configure("Accent.TButton", bordercolor=ACCENT)

    # 左ペインのファイルの一覧（Ver200）：白地・細い灰色の枠。行の地色は分類（A〜D）の
    # 色分け（app.py の _TAG_COLORS）で、選んだ行は押下時の青にする。
    style.configure(
        "Files.Treeview", background=FIELD_BG, fieldbackground=FIELD_BG, foreground=TEXT,
        bordercolor=BORDER, lightcolor=FIELD_BG, darkcolor=FIELD_BG, rowheight=px(root, 22),
    )
    style.map(
        "Files.Treeview",
        background=[("selected", PRESSED_BG)],
        foreground=[("selected", TEXT)],
    )
    style.configure(
        "Files.Treeview.Heading", background=WINDOW_BG, foreground=TEXT, bordercolor=BORDER,
        lightcolor=WINDOW_BG, darkcolor=WINDOW_BG, padding=(4, 2),
    )
    style.map("Files.Treeview.Heading", background=[("active", WINDOW_BG)])

    # リボンのボタン（VA Case・VA Document と同じ）：Word のリボンと同じく、ふだんは枠も地もなく、
    # 重ねたときだけ淡い青の地と枠を出す（班長の UI 方針の「常時、細い枠線」の、リボンだけの例外。
    # VA Document の 2026-10-24 班長決定と同じ）。大きいボタン（RibbonLarge）はアイコンの下に字、
    # 小さいボタン（Ribbon）はアイコンの右に字。
    ribbon_states = dict(
        background=[("disabled", WINDOW_BG), ("pressed", PRESSED_BG), ("active", HOVER_BG)],
        lightcolor=[("disabled", WINDOW_BG), ("pressed", PRESSED_BG), ("active", HOVER_BG)],
        darkcolor=[("disabled", WINDOW_BG), ("pressed", PRESSED_BG), ("active", HOVER_BG)],
        bordercolor=[("disabled", WINDOW_BG), ("pressed", RIBBON_HOVER_BORDER), ("active", RIBBON_HOVER_BORDER)],
    )
    for name, padding, anchor in (("Ribbon.TButton", (3, 1), "w"), ("RibbonLarge.TButton", (4, 2), "center")):
        style.configure(
            name, background=WINDOW_BG, bordercolor=WINDOW_BG, lightcolor=WINDOW_BG, darkcolor=WINDOW_BG,
            padding=padding, width=0, anchor=anchor, justify="center",
        )
        style.map(name, **ribbon_states)
    # 押すとメニューが開くリボンのボタン（［回転］。2026-11-17）：小さいボタン（Ribbon.TButton）と
    # 同じ見た目に、右に小さな下向きの三角を付ける。
    style.configure(
        "Ribbon.TMenubutton", background=WINDOW_BG, bordercolor=WINDOW_BG, lightcolor=WINDOW_BG,
        darkcolor=WINDOW_BG, padding=(3, 1), width=0, anchor="w", arrowcolor=TEXT, arrowsize=6,
    )
    style.map("Ribbon.TMenubutton", arrowcolor=[("disabled", DISABLED_TEXT)], **ribbon_states)
    style.configure("Ribbon.TFrame", background=WINDOW_BG)
    style.configure("Ribbon.TLabel", background=WINDOW_BG)
    style.configure("RibbonGroup.TLabel", background=WINDOW_BG, foreground=NOTE, padding=(0, 0, 0, 2))
    style.configure("RibbonNote.TLabel", background=WINDOW_BG, foreground=NOTE)
    # リボンの主要な操作（読み込み・書き出しの大きいボタン）：見落とされないよう、ほかのリボンのボタンと
    # 違い、いつも地と青い枠を出す（VA Case の「編集を終える」〔RibbonEditEnd〕と同じ形。班長の UI 方針の
    # 「主要な操作ボタンは枠線の色をアクセント色にする」に合わせる）。
    style.configure(
        "RibbonAccent.TButton", background=CONTROL_BG, bordercolor=ACCENT, lightcolor=CONTROL_BG,
        darkcolor=CONTROL_BG, padding=(4, 2), width=0, anchor="center", justify="center",
    )
    style.map(
        "RibbonAccent.TButton",
        background=[("disabled", CONTROL_BG), ("pressed", PRESSED_BG), ("active", HOVER_BG)],
        lightcolor=[("disabled", CONTROL_BG), ("pressed", PRESSED_BG), ("active", HOVER_BG)],
        darkcolor=[("disabled", CONTROL_BG), ("pressed", PRESSED_BG), ("active", HOVER_BG)],
        bordercolor=[("disabled", BORDER), ("!disabled", ACCENT)],
    )

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
