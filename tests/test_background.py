"""裏で行う計算（ui/background.py）。"""

import time

import pytest

tk = pytest.importorskip("tkinter")

from va_mints.ui.background import BackgroundJobs  # noqa: E402


def _pump(root, until, seconds=5.0):
    end = time.time() + seconds
    while not until() and time.time() < end:
        root.update()
        time.sleep(0.01)


def test_result_and_error_reach_the_screen_thread(root):
    import threading

    jobs = BackgroundJobs(root, interval_ms=10)
    got = {}
    main = threading.get_ident()

    def done(key):
        def _on_done(result, exc):
            got[key] = (result, exc, threading.get_ident() == main)
        return _on_done

    jobs.submit("ok", lambda: 42, done("ok"))
    jobs.submit("ng", lambda: 1 / 0, done("ng"))
    assert jobs.running("ok") and jobs.running("ng")
    _pump(root, lambda: len(got) == 2)
    assert got["ok"] == (42, None, True)
    result, exc, on_main = got["ng"]
    assert result is None and isinstance(exc, ZeroDivisionError) and on_main
    assert not jobs.running("ok")


def test_same_key_is_not_started_twice(root):
    jobs = BackgroundJobs(root, interval_ms=10)
    calls = []

    def slow():
        calls.append(1)
        time.sleep(0.2)
        return len(calls)

    results = []
    jobs.submit("k", slow, lambda r, e: results.append(r))
    jobs.submit("k", slow, lambda r, e: results.append(r))
    _pump(root, lambda: results)
    assert calls == [1] and results == [1]
