"""重い計算を裏のスレッドで行い、終わったら画面のスレッドで知らせる（Ver300）。

Tk の画面の部品は、画面のスレッドからしか触れない。そこで、計算は裏のスレッドで行い、
結果は画面のスレッドで、少しずつ（interval_ms ごとに）見に行って受け取る。
使い道：圧縮の大きさの見込み（要件定義書 4.13。ui/app.py）。
"""

from __future__ import annotations

import concurrent.futures
import threading
import tkinter as tk
from typing import Any, Callable, Hashable


class BackgroundJobs:
    def __init__(self, widget: tk.Misc, interval_ms: int = 300) -> None:
        self._widget = widget
        self._interval_ms = interval_ms
        self._jobs: dict[Hashable, tuple[concurrent.futures.Future, Callable[[Any, BaseException | None], None]]] = {}

    def running(self, key: Hashable) -> bool:
        return key in self._jobs

    def busy(self) -> bool:
        """何か1つでも計算しているか。"""
        return bool(self._jobs)

    def submit(
        self, key: Hashable, func: Callable[[], Any], on_done: Callable[[Any, BaseException | None], None]
    ) -> None:
        """func を裏で行い、終わったら画面のスレッドで on_done(結果, 例外又は None) を呼ぶ。
        同じ key のものが動いている間は、新しく始めない。"""
        if key in self._jobs:
            return
        future: concurrent.futures.Future = concurrent.futures.Future()

        def _run() -> None:
            try:
                future.set_result(func())
            except BaseException as exc:  # noqa: BLE001 - 画面のスレッドで on_done に渡す
                future.set_exception(exc)

        # 画面を閉じるときに、計算の終わりを待たないよう、デーモンのスレッドで行う。
        threading.Thread(target=_run, daemon=True).start()
        self._jobs[key] = (future, on_done)
        if len(self._jobs) == 1:
            self._widget.after(self._interval_ms, self._poll)

    def _poll(self) -> None:
        for key, (future, on_done) in list(self._jobs.items()):
            if not future.done():
                continue
            del self._jobs[key]
            exc = future.exception()
            on_done(None if exc is not None else future.result(), exc)
        if self._jobs:
            self._widget.after(self._interval_ms, self._poll)
