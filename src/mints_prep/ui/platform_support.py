"""OSごとに異なる画面まわりの処理（Windows以外でソースから動かす場合への対応）。

VA mintsはWindows用の実行ファイルで配布するが、Macの利用者はソースから
Pythonで動かす（動作は保証しない）。Windowsでの動作は従来のまま変えず、
Windows以外のときだけ代わりの方法をとる。いずれも外部通信を伴わない
（CLAUDE.md「完全ローカル動作」）。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path


def is_aqua(widget: tk.Misc) -> bool:
    """MacのTk（ウィンドウシステムaqua）で動いているかどうか。"""
    return widget.tk.call("tk", "windowingsystem") == "aqua"


def mousewheel_units(widget: tk.Misc, delta: int, windows_units: int) -> int:
    """マウスホイール1回分のスクロール量（行数）を返す。

    WindowsのTkはホイール1目盛りをdelta=±120の倍数で通知するため、呼び出し側の
    従来の計算（windows_units）をそのまま使う。MacのTkはdeltaを±1前後の小さな値で
    通知するため、120で割ると0になりスクロールしない。Macでは向きだけを見て
    1行ずつ動かす。
    """
    if is_aqua(widget):
        if delta == 0:
            return 0
        return -1 if delta > 0 else 1
    return windows_units


def open_folder(path: Path | str) -> None:
    """ユーザーが選んだローカルのフォルダを、OSのファイル管理画面で開く。

    Windowsはos.startfile（エクスプローラー）、Macはopenコマンド（Finder）、
    それ以外はxdg-openを使う。
    """
    if os.name == "nt":
        os.startfile(path)  # noqa: S606 - ユーザーが選択した既存のローカルフォルダのみを開く
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])  # noqa: S603,S607 - 同上
    else:
        subprocess.Popen(["xdg-open", str(path)])  # noqa: S603,S607 - 同上
