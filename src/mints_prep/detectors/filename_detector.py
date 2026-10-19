"""ファイル名検証（文字数・JIS X 0213外字・禁止文字）。

docs/03_requirements.md 4.2「ファイル名」：拡張子込みの文字数（全角・半角とも1文字）、
JIS X 0213外の文字、禁止文字の有無を判定する。根拠は
docs/02_constraints-report.md 第3章2節・第7章1節（操作マニュアル2.3版6頁、一次・確定）。
"""

from __future__ import annotations

from mints_prep.config import FilenameConfig
from mints_prep.models import FilenameFinding


# Windows（cp932）とJIS X 0213系コーデックとで割り当てが食い違う互換文字の写像。
# Windows IMEで「～」を入力するとU+FF5E（全角チルダ）になるが、shift_jis_2004
# コーデックはこれをエンコードできず（正字はU+301C波ダッシュ）、ごく普通の
# ファイル名「決算書～控え.pdf」が毎回「JIS X 0213外の文字の可能性」と警告される
# ノイズになる。実質的に同じ文字とみなせる互換ペアは正字へ写像してから判定する。
_CP932_COMPAT_MAP = str.maketrans({
    "～": "〜",  # ～ 全角チルダ → 波ダッシュ
    "∥": "‖",  # ∥ 平行記号 → 双柱
    "－": "−",  # － 全角ハイフンマイナス → マイナス記号
    "￠": "¢",  # ￠ → ¢
    "￡": "£",  # ￡ → £
    "￢": "¬",  # ￢ → ¬
})


def _non_jis_x0213_chars(name: str, codec: str) -> tuple[str, ...]:
    """指定コーデック（既定 shift_jis_2004＝JIS X 0213:2004）でエンコードできない
    文字を外字候補として抽出する。cp932互換文字（_CP932_COMPAT_MAP）は正字へ
    写像したうえで判定する（写像は判定のためだけで、ファイル名自体は変更しない）。
    """
    found: list[str] = []
    for ch in name:
        try:
            ch.translate(_CP932_COMPAT_MAP).encode(codec)
        except UnicodeEncodeError:
            found.append(ch)
    return tuple(found)


def check_filename(filename: str, config: FilenameConfig) -> FilenameFinding:
    """ファイル名（拡張子込み）を検証し、FilenameFinding を返す。

    文字数は Python の len() をそのまま用いる（全角・半角とも1文字と数える
    マニュアルの計算方法と一致する。サロゲートペアを要する結合文字等の特殊ケースは
    対象外とする）。
    """
    length = len(filename)
    forbidden = tuple(ch for ch in config.forbidden_chars if ch in filename)
    non_jis = _non_jis_x0213_chars(filename, config.jis_x0213_codec)

    return FilenameFinding(
        length=length,
        exceeds_max_length=length > config.max_length,
        exceeds_warning_length=length > config.warning_length,
        forbidden_chars_found=forbidden,
        non_jis_x0213_chars=non_jis,
        max_length=config.max_length,
    )
