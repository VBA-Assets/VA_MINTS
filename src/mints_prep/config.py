"""config.yaml の読み込みと型付きアクセスを提供する。

数値パラメータをコード中にハードコードしないという方針（CLAUDE.md「絶対に譲れない
設計原則」5、docs/03_requirements.md 第5章）に基づき、全ての上限値・許容値は
config.yaml から取得する。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import yaml


def _default_config_path() -> Path:
    """config.yamlの既定パスを決める。

    PyInstaller等で実行ファイル化した場合、実行ファイル自体に埋め込まず、
    実行ファイルと同じフォルダに置いた外部ファイルとして扱う（Python環境を
    持たない利用者でも、再ビルドなしにテキストエディタで数値パラメータを
    編集できるようにするため。CLAUDE.md「絶対に譲れない設計原則」5の趣旨）。
    ソースから直接実行する開発時は、従来どおりプロジェクトルート直下を見る。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "config.yaml"
    return Path(__file__).resolve().parents[2] / "config.yaml"


DEFAULT_CONFIG_PATH = _default_config_path()


@dataclass(frozen=True)
class UploadConfig:
    max_total_mb: float
    safety_margin_ratio: float

    @property
    def max_total_bytes(self) -> int:
        return int(self.max_total_mb * 1024 * 1024)

    @property
    def safe_batch_bytes(self) -> int:
        return int(self.max_total_bytes * self.safety_margin_ratio)


@dataclass(frozen=True)
class FilenameConfig:
    max_length: int
    warning_length: int
    forbidden_chars: tuple[str, ...]
    jis_x0213_codec: str


@dataclass(frozen=True)
class PdfConfig:
    a4_mm: tuple[float, float]
    a3_mm: tuple[float, float]
    size_tolerance_mm: float


@dataclass(frozen=True)
class EvidenceNumberConfig:
    symbols: tuple[str, ...]
    digits: int


STAMP_STYLES = ("black", "red", "red_boxed")


@dataclass(frozen=True)
class EvidenceStampConfig:
    font_size_pt: float
    margin_top_mm: float
    margin_right_mm: float
    style: str = "red"
    box_line_width_pt: float = 1.0
    box_padding_mm: float = 1.5

    def __post_init__(self) -> None:
        if self.style not in STAMP_STYLES:
            raise ValueError(
                f"evidence_stamp.style は {STAMP_STYLES} のいずれかである必要があります"
                f"（指定値：{self.style!r}）"
            )


@dataclass(frozen=True)
class LoggingConfig:
    log_dir: str
    level: str


@dataclass(frozen=True)
class PiiDetectionConfig:
    """個人情報パターンの検出（要件定義書4.9）で使う正規表現群。

    カテゴリ名をキーとする正規表現文字列の辞書。config.yamlで追加・調整
    できるようにする（要件定義書4.9「検出パターンは設定ファイルで追加・
    調整可能にする」）。"クレジットカード番号"カテゴリのみ、検出後に
    Luhnチェックを併用する（detectors/pii_detector.py参照）。
    """

    patterns: dict[str, str]


@dataclass(frozen=True)
class Config:
    upload: UploadConfig
    filename: FilenameConfig
    pdf: PdfConfig
    evidence_number: EvidenceNumberConfig
    evidence_stamp: EvidenceStampConfig
    pii_detection: PiiDetectionConfig
    logging: LoggingConfig


_REQUIRED_SECTIONS = (
    "upload", "filename", "pdf", "evidence_number", "evidence_stamp", "pii_detection", "logging",
)


# セクションごとの必須キー。利用者の編集ミス（キー行の削除等）を、KeyErrorの
# ままアプリを落とすのではなく、どのキーが欠けているか分かる日本語メッセージで
# 知らせるための一覧（_validate参照）。
_REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    "upload": ("max_total_mb", "safety_margin_ratio"),
    "filename": ("max_length", "warning_length", "forbidden_chars", "jis_x0213_codec"),
    "pdf": ("a4_mm", "a3_mm", "size_tolerance_mm"),
    "evidence_number": ("symbols", "digits"),
    "evidence_stamp": (
        "font_size_pt", "margin_top_mm", "margin_right_mm",
        "style", "box_line_width_pt", "box_padding_mm",
    ),
    "pii_detection": ("patterns",),
    "logging": ("log_dir", "level"),
}


def _require_number(section: dict, section_name: str, key: str) -> float:
    value = section[key]
    # boolはintのサブクラスのため明示的に弾く（true/falseの誤記入を数値扱いしない）。
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"config.yaml: {section_name}.{key} は数値である必要があります（指定値：{value!r}）"
        )
    return value


def _require_int(section: dict, section_name: str, key: str) -> int:
    value = section[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            f"config.yaml: {section_name}.{key} は整数である必要があります（指定値：{value!r}）"
        )
    return value


def _validate(raw: object) -> None:
    """config.yamlの内容を検証し、問題があれば日本語メッセージ付きのValueErrorを送出する。

    配布方針上、config.yamlは利用者がテキストエディタで直接編集する前提の
    ファイルである（CLAUDE.md「配布形態に関する留意」）。検証なしで読み込むと、
    例えば個人情報パターンの正規表現を1つ壊しただけで全ファイルが
    「読み取りエラー（D：対象外）」になり、原因が設定ファイルだと利用者には
    分からない。編集ミスは読み込み時点で、どこが悪いか分かる形で知らせる。
    セクション・キーの欠落や型違い（数値欄への文字列等）も、KeyError・TypeError
    のままアプリを落とさず、ここですべて日本語メッセージのValueErrorに変換する。
    """
    import re as _re

    if not isinstance(raw, dict):
        raise ValueError(
            "config.yamlの内容が空か、設定ファイルの形式になっていません。"
            "配布時のconfig.yamlを参考に復元してください。"
        )

    missing = [s for s in _REQUIRED_SECTIONS if s not in raw]
    if missing:
        raise ValueError(
            f"config.yamlに必要なセクションがありません：{', '.join(missing)}。"
            "配布時のconfig.yamlを参考に復元してください。"
        )

    for section_name, keys in _REQUIRED_KEYS.items():
        section = raw[section_name]
        if not isinstance(section, dict):
            raise ValueError(
                f"config.yaml: {section_name} セクションの形式が正しくありません。"
                "配布時のconfig.yamlを参考に復元してください。"
            )
        missing_keys = [k for k in keys if k not in section]
        if missing_keys:
            raise ValueError(
                f"config.yaml: {section_name} セクションに必要な項目がありません："
                f"{', '.join(missing_keys)}。配布時のconfig.yamlを参考に復元してください。"
            )

    filename = raw["filename"]
    if _require_int(filename, "filename", "max_length") <= 0:
        raise ValueError("config.yaml: filename.max_length は正の整数である必要があります")
    if _require_int(filename, "filename", "warning_length") > filename["max_length"]:
        raise ValueError(
            "config.yaml: filename.warning_length は max_length 以下である必要があります"
        )

    upload = raw["upload"]
    if _require_number(upload, "upload", "max_total_mb") <= 0:
        raise ValueError("config.yaml: upload.max_total_mb は正の数である必要があります")
    if not (0 < _require_number(upload, "upload", "safety_margin_ratio") <= 1):
        raise ValueError(
            "config.yaml: upload.safety_margin_ratio は0より大きく1以下である必要があります"
        )

    if _require_number(raw["pdf"], "pdf", "size_tolerance_mm") < 0:
        raise ValueError("config.yaml: pdf.size_tolerance_mm は0以上である必要があります")
    for key in ("a4_mm", "a3_mm"):
        value = raw["pdf"][key]
        if not (isinstance(value, (list, tuple)) and len(value) == 2):
            raise ValueError(
                f"config.yaml: pdf.{key} は [幅, 高さ] の2つの数値である必要があります"
                f"（指定値：{value!r}）"
            )

    evidence = raw["evidence_number"]
    symbols = evidence["symbols"]
    # 空リスト・空文字列を許すと、証拠番号パターンが空符号（^(?P<symbol>)）に
    # なり「001 判決.pdf」等の数字先頭ファイルを証拠番号と誤判定するため弾く。
    if (
        not isinstance(symbols, (list, tuple))
        or not symbols
        or not all(isinstance(s, str) and s for s in symbols)
    ):
        raise ValueError(
            "config.yaml: evidence_number.symbols は1つ以上の文字（甲・乙・丙等）の"
            f"リストである必要があります（指定値：{symbols!r}）"
        )
    if _require_int(evidence, "evidence_number", "digits") <= 0:
        raise ValueError("config.yaml: evidence_number.digits は正の整数である必要があります")

    patterns = raw["pii_detection"]["patterns"]
    if not isinstance(patterns, dict):
        raise ValueError(
            "config.yaml: pii_detection.patterns は「カテゴリ名: 正規表現」の一覧で"
            "ある必要があります。配布時のconfig.yamlを参考に復元してください。"
        )
    for category, pattern in patterns.items():
        if not isinstance(pattern, str):
            raise ValueError(
                f"config.yaml: pii_detection.patterns「{category}」の値は正規表現の"
                f"文字列である必要があります（指定値：{pattern!r}）"
            )
        try:
            _re.compile(pattern)
        except _re.error as exc:
            raise ValueError(
                f"config.yaml: pii_detection.patterns「{category}」の正規表現に誤りがあります"
                f"（{exc}）。パターンを修正するか、この行を削除してください。"
            ) from exc


def load_config(path: Path | None = None) -> Config:
    """config.yaml を読み込み、型付きの Config オブジェクトを返す。

    内容に問題がある場合は、どこをどう直すべきかが分かる日本語メッセージ付きの
    ValueErrorを送出する（_validate参照）。YAML構文の破壊（インデント崩れ・
    引用符の閉じ忘れ等、利用者がテキストエディタで編集する際に最も起こり
    やすいミス）も、yaml.YAMLErrorのまま伝播させず日本語メッセージの
    ValueErrorへ変換する（--windowedのexeでは未捕捉例外が無言終了になるため。
    ui/app.py main()の起動エラーダイアログ参照）。
    """
    config_path = path or DEFAULT_CONFIG_PATH
    with open(config_path, "r", encoding="utf-8") as f:
        try:
            raw = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            raise ValueError(
                f"config.yamlの書式（YAML構文）に誤りがあります：\n{exc}\n"
                "インデント（行頭の空白）や引用符の閉じ忘れがないか確認してください。"
            ) from exc

    _validate(raw)

    return Config(
        upload=UploadConfig(
            max_total_mb=raw["upload"]["max_total_mb"],
            safety_margin_ratio=raw["upload"]["safety_margin_ratio"],
        ),
        filename=FilenameConfig(
            max_length=raw["filename"]["max_length"],
            warning_length=raw["filename"]["warning_length"],
            forbidden_chars=tuple(raw["filename"]["forbidden_chars"]),
            jis_x0213_codec=raw["filename"]["jis_x0213_codec"],
        ),
        pdf=PdfConfig(
            a4_mm=tuple(raw["pdf"]["a4_mm"]),
            a3_mm=tuple(raw["pdf"]["a3_mm"]),
            size_tolerance_mm=raw["pdf"]["size_tolerance_mm"],
        ),
        evidence_number=EvidenceNumberConfig(
            symbols=tuple(raw["evidence_number"]["symbols"]),
            digits=raw["evidence_number"]["digits"],
        ),
        evidence_stamp=EvidenceStampConfig(
            font_size_pt=raw["evidence_stamp"]["font_size_pt"],
            margin_top_mm=raw["evidence_stamp"]["margin_top_mm"],
            margin_right_mm=raw["evidence_stamp"]["margin_right_mm"],
            style=raw["evidence_stamp"]["style"],
            box_line_width_pt=raw["evidence_stamp"]["box_line_width_pt"],
            box_padding_mm=raw["evidence_stamp"]["box_padding_mm"],
        ),
        pii_detection=PiiDetectionConfig(
            patterns=dict(raw["pii_detection"]["patterns"]),
        ),
        logging=LoggingConfig(
            log_dir=raw["logging"]["log_dir"],
            level=raw["logging"]["level"],
        ),
    )
