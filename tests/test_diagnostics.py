"""診断情報（［ヘルプ］→［診断情報をコピー］）の試験。

要点：中身（証拠・書面のファイル名、ファイルやフォルダの場所、例外のメッセージの本文）が
一切入らないこと（diagnostics.py の冒頭。VA FAX の F-24 と同じ考え方）。
"""

import json

import pytest

from mints_prep import __version__, diagnostics


def _raise_with_secret():
    raise OSError(13, "アクセスが拒否されました", r"C:\Users\秘密太郎\事件\甲001 契約書.pdf")


def test_exception_record_hides_message_and_paths():
    try:
        _raise_with_secret()
    except OSError as exc:
        record = diagnostics.exception_record(exc, "書き出し")
    dumped = json.dumps(record, ensure_ascii=False)
    # errno 13 の OSError は、Python が PermissionError に読み替える
    assert record["type"] == "PermissionError" and record["stage"] == "書き出し"
    assert record["errno"] == 13
    assert any("test_diagnostics.py" in w for w in record["where"])      # 場所（ファイル名・行・関数名）は入る
    assert "秘密太郎" not in dumped and "甲001" not in dumped            # メッセージの本文・パスは入らない
    assert "アクセスが拒否" not in dumped


def test_exception_record_includes_cause_type():
    try:
        try:
            raise ValueError("元の誤り")
        except ValueError as inner:
            raise RuntimeError("包んだ誤り") from inner
    except RuntimeError as exc:
        record = diagnostics.exception_record(exc)
    assert record["cause"] == "ValueError"
    assert "元の誤り" not in json.dumps(record, ensure_ascii=False)


def test_error_log_persists_and_caps(tmp_path):
    path = tmp_path / "errors.json"
    log = diagnostics.ErrorLog(path)
    for i in range(diagnostics.KEEP_ERRORS + 5):
        try:
            raise ValueError(f"誤り {i}")
        except ValueError as exc:
            log.record(exc, "試験")
    assert len(log.records) == diagnostics.KEEP_ERRORS                   # 上限で古いものから消える
    reloaded = diagnostics.ErrorLog(path)                                # 別の起動でも読み戻せる
    assert len(reloaded.records) == diagnostics.KEEP_ERRORS
    assert reloaded.records[-1]["type"] == "ValueError"
    log.clear()
    assert not path.exists() and log.records == []


def test_error_log_survives_broken_file(tmp_path):
    path = tmp_path / "errors.json"
    path.write_text("{壊れたJSON", encoding="utf-8")
    assert diagnostics.ErrorLog(path).records == []                      # 壊れていても起動を妨げない


def test_diagnostic_text_has_no_file_names(tmp_path):
    """画面の診断情報の文に、フォルダの場所・ファイル名が入らないこと。"""
    pytest.importorskip("tkinter")
    import tkinter as tk

    from mints_prep.ui.app import MainWindow

    try:
        app = MainWindow()
    except tk.TclError:
        pytest.skip("画面がない環境")
    try:
        app.withdraw()
        app._errors = diagnostics.ErrorLog(tmp_path / "errors.json")     # 本物の記録ファイルを汚さない
        secret_dir = tmp_path / "秘密の事件フォルダ"
        secret_dir.mkdir()
        app._input_dir_var.set(str(secret_dir))
        try:
            _raise_with_secret()
        except OSError as exc:
            app._errors.record(exc, "書き出し")
        text = app._diagnostic_text()
        assert __version__ in text and "PermissionError" in text
        assert "対象フォルダの欄：設定あり（フォルダがある）" in text
        assert "秘密" not in text and "甲001" not in text                # 場所・ファイル名・本文は入らない
        assert str(tmp_path) not in text
    finally:
        app.destroy()
