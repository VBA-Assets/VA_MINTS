"""利用者ごとの設定（［ファイル］→［オプション］。Ver200）。

config.yaml は配布物の既定（実行ファイルの隣。インストールした場合は Program Files の
下にあり、ふつうの利用者の権限では書き換えられない）。画面で変えた設定は、利用者ごとの
アプリデータの場所（%APPDATA%\\VA mints\\settings.json。誤りの記録〔diagnostics.py〕と
同じフォルダ）に保存し、読み込んだ config.yaml の値の上に重ねて使う。

いま扱う設定：
- 証拠番号スタンプの文字の大きさ（ポイント）。既定は config.yaml の
  evidence_stamp.font_size_pt（16ポイント。2026-11-16 班長決定）。

設定のファイルが読めない・壊れているときは、無いものとして config.yaml の値を使う
（画面の動きを止めない）。完全ローカル動作（設計原則1）のとおり、外部通信はしない。
"""

from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import dataclass
from pathlib import Path

from mints_prep.config import Config

# 画面で選べる文字の大きさの範囲（ポイント）。一次資料に定めはなく、UI 操作上の実装値
# （小さすぎると読めず、大きすぎると右上の余白からはみ出して本文に重なる）。
STAMP_FONT_SIZE_MIN = 8
STAMP_FONT_SIZE_MAX = 36


def settings_path() -> Path:
    base = os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / ".va_mints"
    return root / "VA mints" / "settings.json"


@dataclass(frozen=True)
class UserSettings:
    # None は「config.yaml の既定のまま」。
    stamp_font_size_pt: float | None = None


def _valid_size(value) -> float | None:
    try:
        size = float(value)
    except (TypeError, ValueError):
        return None
    if STAMP_FONT_SIZE_MIN <= size <= STAMP_FONT_SIZE_MAX:
        return size
    return None


def load_user_settings(path: Path | None = None) -> UserSettings:
    path = path or settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return UserSettings()
    if not isinstance(data, dict):
        return UserSettings()
    return UserSettings(stamp_font_size_pt=_valid_size(data.get("stamp_font_size_pt")))


def save_user_settings(settings: UserSettings, path: Path | None = None) -> None:
    """保存する。既定のままの設定しかなければ、ファイルを消す。書けなければ OSError。"""
    path = path or settings_path()
    data = {k: v for k, v in dataclasses.asdict(settings).items() if v is not None}
    if not data:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def apply_user_settings(config: Config, settings: UserSettings) -> Config:
    """config.yaml の設定（config）の上に、利用者ごとの設定を重ねたものを返す。"""
    if settings.stamp_font_size_pt is None:
        return config
    return dataclasses.replace(
        config,
        evidence_stamp=dataclasses.replace(
            config.evidence_stamp, font_size_pt=settings.stamp_font_size_pt
        ),
    )
