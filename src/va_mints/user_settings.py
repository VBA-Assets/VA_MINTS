"""利用者ごとの設定（［ファイル］→［オプション］。Ver200）。

config.yaml は配布物の既定（実行ファイルの隣。インストールした場合は Program Files の
下にあり、ふつうの利用者の権限では書き換えられない）。画面で変えた設定は、利用者ごとの
アプリデータの場所（%APPDATA%\\VA mints\\settings.json。誤りの記録〔diagnostics.py〕と
同じフォルダ）に保存し、読み込んだ config.yaml の値の上に重ねて使う。

いま扱う設定：
- 証拠番号スタンプの文字の大きさ（ポイント）。既定は config.yaml の
  evidence_stamp.font_size_pt（16ポイント。2026-11-16 班長決定）。
- 証拠番号スタンプの見た目（黒字・赤字・赤字に赤枠。Ver300。2026-11-17 班長指示）。既定は
  config.yaml の evidence_stamp.style（赤字）。
- ウィンドウの大きさ・位置と、最大化していたか（Ver300。2026-11-17 班長指摘「ウインドウを
  最大化して終了したことが、保存されない」）。閉じるときに覚え、次に起動したときに戻す。

設定のファイルが読めない・壊れているときは、無いものとして config.yaml の値を使う
（画面の動きを止めない）。完全ローカル動作（設計原則1）のとおり、外部通信はしない。
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from va_mints.config import STAMP_STYLES, Config

# 画面で選べる文字の大きさの範囲（ポイント）。一次資料に定めはなく、UI 操作上の実装値
# （小さすぎると読めず、大きすぎると右上の余白からはみ出して本文に重なる）。
STAMP_FONT_SIZE_MIN = 8
STAMP_FONT_SIZE_MAX = 36


# 試験では、この環境変数で置き場所を一時のフォルダに差し替える（班長・利用者の設定に書かない。標準 02「設定ファイル」）。
ENV_DIR = "VA_MINTS_CONFIG_DIR"


def config_dir() -> Path:
    """この PC の設定と誤りの記録の置き場所。Windows は %APPDATA%\\VA mints、それ以外は ~/.va_mints/VA mints
    （Ver200 からの置き場所。利用者の設定を失わないよう変えない）。"""
    override = os.environ.get(ENV_DIR)
    if override:
        return Path(override)
    base = os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / ".va_mints"
    return root / "VA mints"


def settings_path() -> Path:
    return config_dir() / "settings.json"


@dataclass(frozen=True)
class UserSettings:
    # None は「config.yaml の既定のまま」。
    stamp_font_size_pt: float | None = None
    stamp_style: str | None = None  # "black"・"red"・"red_boxed"（config.STAMP_STYLES）
    # ウィンドウ：最大化していないときの大きさと位置（Tk の "幅x高さ+X+Y"）と、最大化していたか。
    window_geometry: str | None = None
    window_maximized: bool | None = None


# Tk の geometry() が返す形（左の画面にはみ出すと "+-8" のように負の数になる）。
_GEOMETRY = re.compile(r"^(\d{2,5})x(\d{2,5})\+(-?\d{1,5})\+(-?\d{1,5})$")


def parse_geometry(text: str | None) -> tuple[int, int, int, int] | None:
    """"幅x高さ+X+Y" を（幅, 高さ, X, Y）に。形が違えば None。"""
    match = _GEOMETRY.match(text or "")
    if match is None:
        return None
    w, h, x, y = (int(v) for v in match.groups())
    return w, h, x, y


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
    style = data.get("stamp_style")
    geometry = data.get("window_geometry")
    geometry = geometry if isinstance(geometry, str) else None
    maximized = data.get("window_maximized")
    return UserSettings(
        stamp_font_size_pt=_valid_size(data.get("stamp_font_size_pt")),
        stamp_style=style if style in STAMP_STYLES else None,
        window_geometry=geometry if parse_geometry(geometry) is not None else None,
        window_maximized=maximized if isinstance(maximized, bool) else None,
    )


def save_user_settings(settings: UserSettings, path: Path | None = None) -> None:
    """保存する。既定のままの設定しかなければ、ファイルを消す。書けなければ OSError。"""
    path = path or settings_path()
    data = {k: v for k, v in dataclasses.asdict(settings).items() if v is not None}
    if not data:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    # 改行を変えないよう、バイトで書く（Windows の write_text は \n を \r\n にする）
    tmp.write_bytes(json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8"))
    os.replace(tmp, path)


def apply_user_settings(config: Config, settings: UserSettings) -> Config:
    """config.yaml の設定（config）の上に、利用者ごとの設定を重ねたものを返す。"""
    changes: dict = {}
    if settings.stamp_font_size_pt is not None:
        changes["font_size_pt"] = settings.stamp_font_size_pt
    if settings.stamp_style is not None:
        changes["style"] = settings.stamp_style
    if not changes:
        return config
    return dataclasses.replace(
        config, evidence_stamp=dataclasses.replace(config.evidence_stamp, **changes)
    )
