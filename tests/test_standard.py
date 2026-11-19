"""標準（VAプロジェクト\\標準\\01・02）に沿っているかの試験（雛形の tests\\test_standard.py を、VA mints に合わせたもの）。"""
import re

from va_mints import APP_NAME, VERSION_LABEL, __version__, errors


def test_version_label():
    """版は1か所（__init__.py）にあり、表示は「Ver」と点なしの3文字（標準 01「版」）。"""
    assert re.fullmatch(r"Ver[0-9A-Z]{3}", VERSION_LABEL)
    assert VERSION_LABEL == "Ver" + __version__.replace(".", "")


def test_title(app):
    """題名はツールの名前だけ。版・説明を入れない（標準 01「ウィンドウとメッセージの題名」）。"""
    assert app.title() == APP_NAME


def test_menu_labels(app):
    """メニュー（標準 02「メニュー」）：画面が開く項目は全角の「…」、［終了］は置かない、［ヘルプ］の形。"""
    def labels(menu):
        return [menu.entrycget(i, "label") for i in range(menu.index("end") + 1) if menu.type(i) == "command"]

    file_labels = labels(app._menus["ファイル"])
    assert file_labels == ["ファイルを読み込み", "ファイルを更新", "元のファイルに戻す", "オプション…"]
    every = file_labels + labels(app._menus["編集"]) + labels(app._menus["ヘルプ"])
    assert all("..." not in s for s in every) and "終了" not in every
    assert labels(app._menus["ヘルプ"]) == ["診断情報をコピー", f"{APP_NAME} について"]


def test_about_text(app):
    """「について」は5行（標準 01「について」）。"""
    lines = app.about_text().split("\n")
    assert lines[0] == f"{APP_NAME}  {VERSION_LABEL}"
    assert lines[1] and lines[2] == ""
    assert lines[3:] == ["VBAアセット（vba-assets.net）", "MIT License"]


def test_command_key_only_on_mac(app):
    """⌘（Command）は Mac のときだけ割り当てる（標準 02「メニュー」）。Windows では Ctrl だけ。"""
    if app.tk.call("tk", "windowingsystem") == "aqua":
        assert app._key_modifiers() == ("Control", "Command")
    else:
        assert app._key_modifiers() == ("Control",)
        assert not any("Command" in seq for seq in app.bind())


def test_error_text_hides_program_words():
    """OSError などの文（英語・ファイルの場所）は出さず、要旨と診断情報の案内にする（標準 02「メッセージとエラー」）。"""
    secret = PermissionError(13, "Access is denied", r"C:\事件\甲001 契約書.pdf")
    text = errors.describe(secret)
    assert "甲001" not in text and "Access" not in text and "診断情報をコピー" in text
    assert errors.describe(ValueError("ページを挿入できるのは、PDF です。")) == "ページを挿入できるのは、PDF です。"
    assert errors.describe(ValueError("invalid literal"), detail=False) == "予期しない誤りが起きました。"


def test_library_error_with_japanese_file_name_is_not_shown():
    """部品の中で投げられた例外は、ファイルの名前（日本語）を含んでいても、文を出さずに要旨にする。"""
    from pathlib import PurePosixPath

    try:
        PurePosixPath("事件/甲001 契約書.pdf").relative_to("別の事件")  # pathlib の中で、名前入りの ValueError
    except ValueError as exc:
        assert "甲001" in str(exc)
        assert not errors.is_for_user(exc)
        assert "甲001" not in errors.describe(exc)


def test_tool_error_text_is_shown(tmp_path):
    """このツールの中で投げた日本語の文（config.yaml の誤りなど）は、そのまま出す。"""
    import pytest

    from va_mints.config import load_config

    p = tmp_path / "config.yaml"
    p.write_bytes("upload: [".encode("utf-8"))
    with pytest.raises(ValueError) as info:
        load_config(p)
    assert errors.is_for_user(info.value) and errors.describe(info.value) == str(info.value)
