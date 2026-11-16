"""利用者ごとの設定（［ファイル］→［オプション］。user_settings.py）。"""

from pathlib import Path

from mints_prep.user_settings import (
    UserSettings,
    apply_user_settings,
    load_user_settings,
    save_user_settings,
)


def test_default_stamp_size_is_16pt(config):
    """既定は16ポイント（2026-11-16 班長決定。config.yaml）。"""
    assert config.evidence_stamp.font_size_pt == 16


def test_saved_size_overrides_config_and_survives_reload(tmp_path: Path, config):
    path = tmp_path / "settings.json"
    save_user_settings(UserSettings(stamp_font_size_pt=20), path)

    loaded = load_user_settings(path)
    applied = apply_user_settings(config, loaded)

    assert loaded.stamp_font_size_pt == 20
    assert applied.evidence_stamp.font_size_pt == 20
    # ほかの設定は config.yaml のまま。
    assert applied.evidence_stamp.margin_top_mm == config.evidence_stamp.margin_top_mm
    assert applied.upload == config.upload


def test_back_to_default_removes_the_file(tmp_path: Path, config):
    path = tmp_path / "settings.json"
    save_user_settings(UserSettings(stamp_font_size_pt=20), path)
    save_user_settings(UserSettings(), path)

    assert not path.exists()
    assert apply_user_settings(config, load_user_settings(path)) is config


def test_missing_broken_or_out_of_range_file_falls_back_to_config(tmp_path: Path, config):
    path = tmp_path / "settings.json"
    assert load_user_settings(path) == UserSettings()  # ない

    path.write_text("{broken", encoding="utf-8")
    assert load_user_settings(path) == UserSettings()  # 壊れている

    path.write_text('{"stamp_font_size_pt": 200}', encoding="utf-8")
    assert load_user_settings(path) == UserSettings()  # 範囲の外

    path.write_text('{"stamp_font_size_pt": "大きい"}', encoding="utf-8")
    assert apply_user_settings(config, load_user_settings(path)) is config
