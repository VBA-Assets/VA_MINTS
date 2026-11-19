"""試験の道具（標準 02「テスト」）。

設定と誤りの記録は、試験ごとの一時フォルダに置く（班長・利用者の設定〔%APPDATA%\\VA mints〕に書かない）。
画面（Tk）は、試験の全体で1つだけ作る（_session_window。MainWindow は tk.Tk なので、画面の試験はすべてこれを使う）。
Windows の Python では、Tk を作り直すと、まれに初期化に失敗する（VA Case で 2026-11-19 に起きた）。
使う字・ファイルの名前はすべて架空のもの（実データは使わない）。
"""
from __future__ import annotations

import gc
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from va_mints.config import load_config
from va_mints.user_settings import ENV_DIR

FIXTURES_DIR = Path(__file__).parent / "fixtures"
sys.path.insert(0, str(FIXTURES_DIR.parent))


@pytest.fixture(autouse=True)
def _config_dir(tmp_path_factory, monkeypatch):
    """設定と誤りの記録の置き場所を、試験ごとの一時フォルダに差し替える。"""
    folder = tmp_path_factory.mktemp("設定")
    monkeypatch.setenv(ENV_DIR, str(folder))
    return folder


@pytest.fixture
def config():
    return load_config()


@pytest.fixture(scope="session")
def _session_window(tmp_path_factory):
    """試験の全体で1つの画面。session の fixture では monkeypatch を使えないので、設定の場所はここで差し替える。"""
    tk = pytest.importorskip("tkinter")
    from va_mints.ui.app import MainWindow

    old = os.environ.get(ENV_DIR)
    os.environ[ENV_DIR] = str(tmp_path_factory.mktemp("設定_画面"))
    try:
        window = MainWindow()
    except tk.TclError as exc:
        pytest.skip(f"画面がない環境（{exc}）")
    window.withdraw()
    yield window
    window.destroy()
    if old is None:
        os.environ.pop(ENV_DIR, None)
    else:
        os.environ[ENV_DIR] = old
    gc.collect()


def reset_window(window) -> None:
    """1つの試験の前後で、画面を読み込む前の状態に戻す（設定と誤りの記録は、いまの試験の一時フォルダから読み直す）。"""
    from va_mints import diagnostics
    from va_mints.user_settings import apply_user_settings, load_user_settings

    window._cancel_description_jobs()
    window._scan_result = None
    window._close_workspace()
    window._reselect_name = None
    window._clear_file_list()
    window._input_dir_var.set("")
    window._set_sequence_text("")
    window._sync_description_button()
    window._errors = diagnostics.ErrorLog()
    window._user_settings = load_user_settings()
    window.config_obj = apply_user_settings(window._base_config, window._user_settings)
    if window.state() != "withdrawn":
        window.state("normal")
        window.withdraw()
    window.update_idletasks()


@pytest.fixture
def root(_session_window):
    """Tk の部品を作る試験の親（試験の全体で1つの画面）。作った部品は、各試験で消す。"""
    yield _session_window
    _session_window.update_idletasks()


@pytest.fixture
def app(_session_window):
    """VA mints の画面（読み込む前の状態にしてから渡す）。"""
    reset_window(_session_window)
    yield _session_window
    reset_window(_session_window)
