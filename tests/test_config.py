"""config.yamlの検証（config.py:_validate）のテスト。

配布方針上、config.yamlは利用者がテキストエディタで直接編集する前提の
ファイルのため、編集ミスは読み込み時点で日本語メッセージ付きで検知する。
"""
from pathlib import Path

import pytest
import yaml

from mints_prep.config import DEFAULT_CONFIG_PATH, load_config


def _write_modified_config(tmp_path: Path, mutate) -> Path:
    raw = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    mutate(raw)
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return p


def test_valid_default_config_loads():
    assert load_config() is not None


def test_broken_pii_regex_raises_actionable_error(tmp_path: Path):
    """PII正規表現を1つ壊しただけで全ファイルが「読み取りエラー（D）」に
    化ける不具合の回帰テスト：読み込み時点でカテゴリ名入りのValueErrorになる。"""
    def mutate(raw):
        raw["pii_detection"]["patterns"]["電話番号"] = r"([0-9"  # 閉じ括弧なし

    p = _write_modified_config(tmp_path, mutate)
    with pytest.raises(ValueError, match="電話番号"):
        load_config(p)


def test_missing_section_raises_actionable_error(tmp_path: Path):
    def mutate(raw):
        del raw["upload"]

    p = _write_modified_config(tmp_path, mutate)
    with pytest.raises(ValueError, match="upload"):
        load_config(p)


def test_warning_length_above_max_length_rejected(tmp_path: Path):
    def mutate(raw):
        raw["filename"]["warning_length"] = raw["filename"]["max_length"] + 1

    p = _write_modified_config(tmp_path, mutate)
    with pytest.raises(ValueError, match="warning_length"):
        load_config(p)


def test_invalid_safety_margin_rejected(tmp_path: Path):
    def mutate(raw):
        raw["upload"]["safety_margin_ratio"] = 1.5

    p = _write_modified_config(tmp_path, mutate)
    with pytest.raises(ValueError, match="safety_margin_ratio"):
        load_config(p)


def test_yaml_syntax_error_raises_japanese_valueerror(tmp_path: Path):
    """YAML構文の破壊（引用符の閉じ忘れ等、最も起こりやすい編集ミス）が
    yaml.YAMLErrorのまま伝播して--windowedのexeを無言終了させる不具合の
    回帰テスト：日本語メッセージのValueErrorへ変換される。"""
    p = tmp_path / "config.yaml"
    p.write_text("upload:\n  max_total_mb: '閉じ忘れ\n", encoding="utf-8")
    with pytest.raises(ValueError, match="YAML構文"):
        load_config(p)


def test_empty_config_file_raises_valueerror(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="config.yaml"):
        load_config(p)


def test_missing_key_raises_actionable_error(tmp_path: Path):
    """セクションはあるが個別キー（max_length行）を削除した場合の回帰テスト：
    KeyErrorのまま落とさず、欠けているキー名入りのValueErrorになる。"""
    def mutate(raw):
        del raw["filename"]["max_length"]

    p = _write_modified_config(tmp_path, mutate)
    with pytest.raises(ValueError, match="max_length"):
        load_config(p)


def test_non_numeric_max_length_rejected(tmp_path: Path):
    def mutate(raw):
        raw["filename"]["max_length"] = "abc"

    p = _write_modified_config(tmp_path, mutate)
    with pytest.raises(ValueError, match="max_length"):
        load_config(p)


def test_empty_symbols_rejected(tmp_path: Path):
    """symbols: [] は証拠番号パターンが空符号になり数字先頭ファイルを
    誤マッチさせるため弾く。"""
    def mutate(raw):
        raw["evidence_number"]["symbols"] = []

    p = _write_modified_config(tmp_path, mutate)
    with pytest.raises(ValueError, match="symbols"):
        load_config(p)


def test_non_integer_digits_rejected(tmp_path: Path):
    """digits: 3.0（float）は正規表現の量指定子として無効になり、証拠番号
    判定が無言で全件不成立になるため、読み込み時点で弾く。"""
    def mutate(raw):
        raw["evidence_number"]["digits"] = 3.0

    p = _write_modified_config(tmp_path, mutate)
    with pytest.raises(ValueError, match="digits"):
        load_config(p)
