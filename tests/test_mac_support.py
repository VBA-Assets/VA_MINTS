"""Windows以外（Mac）でソースから動かす場合の分岐の試験。

Macの実機はないため、os.name・sys.platform・Tkのウィンドウシステム・
書体の候補を差し替えて、Mac側の分岐を通す。あわせて、Windowsでの
動作・出力が従来どおりであることを確かめる。
"""

import shutil
import sys
import unicodedata
from pathlib import Path

import pytest
from PIL import Image

from fixtures.generate_fixtures import make_image_without_metadata
from mints_prep.config import EvidenceStampConfig
from mints_prep.detectors.filename_detector import check_filename
from mints_prep.processors import evidence_stamp
from mints_prep.processors.filename_sanitizer import sanitize_filename
from mints_prep.ui import platform_support

_CONFIG = EvidenceStampConfig(font_size_pt=10, margin_top_mm=8, margin_right_mm=8)
_WINDOWS_FONT = Path(r"C:\Windows\Fonts\msgothic.ttc")

# 「ガス料金」をMacのファイル名と同じ分解した形（NFD）にしたもの
_NFD_NAME = unicodedata.normalize("NFD", "甲001 ガス料金.pdf")


class _FakeTk:
    def __init__(self, windowing_system: str) -> None:
        self._windowing_system = windowing_system

    def call(self, *args):
        assert args == ("tk", "windowingsystem")
        return self._windowing_system


class _FakeWidget:
    def __init__(self, windowing_system: str) -> None:
        self.tk = _FakeTk(windowing_system)


# --- マウスホイール ---------------------------------------------------------


def test_mousewheel_units_on_windows_uses_caller_calculation():
    widget = _FakeWidget("win32")
    assert platform_support.mousewheel_units(widget, 120, -1) == -1
    assert platform_support.mousewheel_units(widget, -240, 2) == 2


@pytest.mark.parametrize("delta, expected", [(1, -1), (3, -1), (-1, 1), (-5, 1), (0, 0)])
def test_mousewheel_units_on_mac_scrolls_by_direction(delta, expected):
    # MacのTkはdeltaを±1前後で通知する。120で割る計算では0になり動かない
    widget = _FakeWidget("aqua")
    assert platform_support.mousewheel_units(widget, delta, int(-1 * (delta / 120))) == expected


# --- 出力フォルダを開く -----------------------------------------------------


def test_open_folder_on_windows_uses_startfile(monkeypatch, tmp_path: Path):
    opened = []
    monkeypatch.setattr(platform_support.os, "name", "nt")
    monkeypatch.setattr(platform_support.os, "startfile", opened.append, raising=False)
    monkeypatch.setattr(platform_support.subprocess, "Popen", lambda *a, **k: pytest.fail("Popen"))
    platform_support.open_folder(tmp_path)
    assert opened == [tmp_path]


def test_open_folder_on_mac_uses_open_command(monkeypatch, tmp_path: Path):
    calls = []
    monkeypatch.setattr(platform_support.os, "name", "posix")
    monkeypatch.setattr(platform_support.sys, "platform", "darwin")
    monkeypatch.setattr(platform_support.os, "startfile", lambda p: pytest.fail("startfile"), raising=False)
    monkeypatch.setattr(platform_support.subprocess, "Popen", lambda args: calls.append(args))
    platform_support.open_folder(tmp_path)
    assert calls == [["open", str(tmp_path)]]


# --- 画像スタンプの書体 -----------------------------------------------------


def test_image_font_windows_candidates_come_first():
    # Windowsでは従来どおりメイリオが選ばれる（出力を変えない）
    assert evidence_stamp._IMAGE_FONT_CANDIDATES[:3] == (
        r"C:\Windows\Fonts\meiryo.ttc",
        r"C:\Windows\Fonts\YuGothM.ttc",
        r"C:\Windows\Fonts\msgothic.ttc",
    )
    assert "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc" in evidence_stamp._IMAGE_FONT_CANDIDATES


@pytest.mark.skipif(not _WINDOWS_FONT.exists(), reason="試験用の書体ファイルがない")
def test_stamp_image_with_mac_font_stored_in_nfd(monkeypatch, tmp_path: Path):
    """Windowsの書体がなく、ヒラギノのファイル名がNFDで保存されたMacを装う。"""
    fonts_dir = tmp_path / "System" / "Library" / "Fonts"
    fonts_dir.mkdir(parents=True)
    hiragino_nfc = fonts_dir / "ヒラギノ角ゴシック W3.ttc"
    hiragino_nfd = fonts_dir / unicodedata.normalize("NFD", hiragino_nfc.name)
    shutil.copyfile(_WINDOWS_FONT, hiragino_nfd)
    assert not hiragino_nfc.exists()  # NTFSは正規化しないので、NFCの名前では見つからない

    monkeypatch.setattr(
        evidence_stamp,
        "_IMAGE_FONT_CANDIDATES",
        (str(tmp_path / "Windows" / "Fonts" / "meiryo.ttc"), str(hiragino_nfc)),
    )
    font = evidence_stamp._load_image_font(20)
    assert Path(font.path) == hiragino_nfd

    src = make_image_without_metadata(tmp_path / "甲004 写真.png")
    dest = tmp_path / "out.png"
    evidence_stamp.stamp_image(src, dest, "甲004", _CONFIG)
    with Image.open(src) as before, Image.open(dest) as after:
        assert after.size == before.size
        assert after.convert("RGB").tobytes() != before.convert("RGB").tobytes()


def test_image_font_not_found_message_mentions_mac(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(evidence_stamp, "_IMAGE_FONT_CANDIDATES", (str(tmp_path / "none.ttc"),))
    with pytest.raises(FileNotFoundError, match="ヒラギノ角ゴシック"):
        evidence_stamp._load_image_font(20)


# --- Macのファイル名（濁点を分解した形） --------------------------------------


def test_nfd_filename_on_windows_is_checked_as_is(config, monkeypatch):
    # Windowsでは従来どおり、ファイル名をそのまま判定する
    monkeypatch.setattr(sys, "platform", "win32")
    finding = check_filename(_NFD_NAME, config.filename)
    assert finding.length == len(_NFD_NAME)
    assert "\u3099" in finding.non_jis_x0213_chars


def test_nfd_filename_on_mac_is_checked_in_composed_form(config, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    finding = check_filename(_NFD_NAME, config.filename)
    assert finding.length == len("甲001 ガス料金.pdf")
    assert finding.non_jis_x0213_chars == ()


def test_sanitize_nfd_filename_on_mac_does_not_split_dakuten(config, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    # NFCで101文字（「ガ」の分解で、NFDでは198文字）。短縮で濁点だけが残らないこと
    name = unicodedata.normalize("NFD", "ガ" * 97 + ".pdf")
    assert check_filename(name, config.filename).exceeds_max_length
    result = sanitize_filename(name, config.filename)
    assert result == "ガ" * 96 + ".pdf"
    assert len(result) == config.filename.max_length
