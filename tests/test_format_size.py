"""左の一覧に出す大きさの書き方（ui/app.py の _format_size）。"""

import pytest

pytest.importorskip("tkinter")

from mints_prep.ui.app import _format_size  # noqa: E402


def test_format_size():
    assert _format_size(12_789_668) == "12.2MB"
    assert _format_size(1024 * 1024) == "1.0MB"
    assert _format_size(7300) == "7KB"
    assert _format_size(10) == "1KB"  # 0KB とは書かない
