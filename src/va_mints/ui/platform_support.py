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


def clipboard_files() -> list[Path]:
    """エクスプローラーでコピーしたファイル（クリップボードの CF_HDROP）の位置。Windows 以外や、
    ファイルがコピーされていないときは空（Ver300。左の一覧への貼り付け）。

    Tk のクリップボードは文字しか扱えないため、Windows の API で読む。読むだけで、
    クリップボードの中身は変えない。
    """
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes

    cf_hdrop = 15
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
    shell32.DragQueryFileW.restype = wintypes.UINT

    if not user32.IsClipboardFormatAvailable(cf_hdrop):
        return []
    if not user32.OpenClipboard(None):
        return []
    try:
        handle = user32.GetClipboardData(cf_hdrop)
        if not handle:
            return []
        count = shell32.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)
        paths: list[Path] = []
        for i in range(count):
            length = shell32.DragQueryFileW(handle, i, None, 0)
            buffer = ctypes.create_unicode_buffer(length + 1)
            shell32.DragQueryFileW(handle, i, buffer, length + 1)
            paths.append(Path(buffer.value))
        return paths
    finally:
        user32.CloseClipboard()


def enable_file_drop(widget: tk.Misc, on_drop) -> bool:
    """エクスプローラーからのファイルのドラッグアンドドロップを、widget で受ける（Ver300）。
    on_drop にはドロップされたものの位置の並びを渡す。使えないとき（tkinterdnd2 がない等）は
    False（ドロップできないだけで、ほかの操作はできる）。

    tkinterdnd2（MIT License。中の tkdnd は BSD 系）を使う。ドロップを受け取るだけで、
    通信はしない（DLL が使うのは kernel32・shell32・user32・ole32 だけ。2026-11-17 に確かめた）。
    """
    from tkinterdnd2 import DND_FILES, TkinterDnD

    TkinterDnD._require(widget.winfo_toplevel())

    def _drop(event) -> str:
        on_drop([Path(p) for p in widget.tk.splitlist(event.data)])
        return event.action

    widget.drop_target_register(DND_FILES)
    widget.dnd_bind("<<Drop>>", _drop)
    return True


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


def send_to_recycle_bin(path: Path) -> None:
    """ファイル・フォルダを、ごみ箱に移す（証拠説明書のフォルダを消すとき。4.17。ごみ箱から戻せる）。
    Windows は、シェルの SHFileOperationW（元に戻せる削除）を使う。Mac は、ホームの .Trash へ移す
    （なければ消す）。移せなければ OSError。"""
    path = Path(path).resolve()
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND),
                ("wFunc", wintypes.UINT),
                ("pFrom", wintypes.LPCWSTR),
                ("pTo", wintypes.LPCWSTR),
                ("fFlags", ctypes.c_ushort),
                ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", wintypes.LPCWSTR),
            ]

        FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 3, 0x4, 0x10, 0x40, 0x400
        op = SHFILEOPSTRUCTW()
        op.wFunc = FO_DELETE
        op.pFrom = str(path) + "\0"  # 2つの終わりの印（文字列の終わりに、もう1つ付く）
        op.fFlags = FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO | FOF_NOERRORUI
        result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        if result != 0 or op.fAnyOperationsAborted or path.exists():
            raise OSError(f"ごみ箱に移せませんでした（{result}）：{path.name}")
        return
    import shutil

    trash = Path.home() / ".Trash"
    if trash.is_dir():
        target = trash / path.name
        n = 1
        while target.exists():
            n += 1
            target = trash / f"{path.name} {n}"
        shutil.move(str(path), str(target))
    elif path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
