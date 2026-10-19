"""スキャン結果を表すデータモデル。

docs/03_requirements.md 第3章の分類（A〜D）、4.1〜4.2節の判定項目に対応する。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Classification(Enum):
    """ファイルごとの分類（要件定義書 第3章）。"""

    A_COMPLIANT = "A"  # 対応形式・適合済み
    B_NEEDS_FIX = "B"  # 対応形式・要修正
    C_NEEDS_CONVERSION = "C"  # 非対応形式・要変換
    D_UNSUPPORTED = "D"  # 処理対象外


class FormatStatus(Enum):
    """形式判定の結果（要件定義書 4.2「形式判定」）。"""

    RECORD_READY = "record_ready"  # 記録対応形式（PDF/MP4/MP3/JPEG/PNG）
    CONVERTIBLE = "convertible"  # 変換元として受理する非対応形式
    UNSUPPORTED = "unsupported"  # いずれにも該当しない処理対象外形式
    EXTENSION_MISMATCH = "extension_mismatch"  # 拡張子と実体（マジックナンバー）の不一致


@dataclass
class FilenameFinding:
    """ファイル名検証結果（要件定義書 4.2「ファイル名」）。"""

    length: int
    exceeds_max_length: bool
    exceeds_warning_length: bool
    forbidden_chars_found: tuple[str, ...] = ()
    non_jis_x0213_chars: tuple[str, ...] = ()
    # 判定に使った上限文字数（config.filename.max_length）。表示文言が
    # 「上限100文字」等の数値をハードコードせず、実際に適用された値を示す
    # ために保持する（CLAUDE.md設計原則5の趣旨）。
    max_length: int | None = None


@dataclass
class PdfFinding:
    """PDF固有の検出結果（要件定義書 4.2「パスワード有無」「用紙寸法」「向き」、
    4.6「操作制限（印刷・コピー制限）PDFの検出」）。

    パスワード付きで内容が読めない場合、寸法・向きは None のままとなる。

    has_password と has_permission_restriction は互いに排他的なフィールドで
    ある：ユーザーパスワードが必須で開けない場合（要件定義書4.6「パスワード
    解除」の対象）が has_password、ユーザーパスワードなしで開けるが暗号化
    辞書が存在し印刷・コピー等が制限されている場合（オーナーパスワードに
    よる権限制限。登記情報提供サービスのPDF等が典型例）が
    has_permission_restriction。両者を同一フィールドで判定していた過去の
    実装は誤りだったため区別した（要件定義書4.6節参照）。
    """

    has_password: bool
    has_permission_restriction: bool = False
    page_sizes_mm: tuple[tuple[float, float], ...] = ()
    page_size_judgments: tuple[str, ...] = ()  # "A4" / "A3" / "非対応"（ページ単位）
    page_orientations: tuple[str, ...] = ()  # "縦" / "横"（ページ単位）
    orientation_mismatch_pages: tuple[int, ...] = ()  # 内容の向きとページ設定が食い違う疑いのあるページ（1始まり）


@dataclass
class MetadataFinding:
    """メタデータ有無の検出結果（要件定義書 4.2「メタデータ」）。"""

    has_metadata: bool
    detected_fields: tuple[str, ...] = ()


@dataclass
class RedactionFinding:
    """墨消し完全性の検証結果（要件定義書 4.6「墨消し完全性の検証・防止処理」）。

    ページ内の黒塗り・マーカー等の被覆オブジェクト（塗りつぶし矩形、ハイライト
    注釈）の座標範囲と、下層のテキストオブジェクトの座標とを照合し、被覆下に
    文字情報が残存しているかを判定する。検証（検出・警告）のみを行い、
    除去（防止処理）の実行はチェック専念方針により廃止した。読み取り専用の
    検証のため、CLAUDE.mdの確認UI原則（設計原則3）の対象外＝自動実行してよい。
    """

    has_residual_text: bool
    pages_with_residual_text: tuple[int, ...] = ()
    details: tuple[str, ...] = ()  # 人が読める形式の詳細（ページ・被覆種別・残存テキストの例）


@dataclass
class PiiFinding:
    """個人情報パターンの検出結果（要件定義書 4.9「個人情報パターンの検出」）。

    PDFのテキストレイヤー及びファイル名から、正規表現により個人情報らしき
    パターン（マイナンバー・電話番号・メールアドレス・郵便番号＋住所・
    銀行口座番号・クレジットカード番号）を検出する。誤検出（事件番号・
    書証中の日付等を誤って拾う場合）があり得るため、断定は避ける。検出の
    みを行い、削除・墨消しの実行はしない（チェック専念方針）。
    """

    has_pii: bool
    detected_categories: tuple[str, ...] = ()  # 例：("マイナンバー", "電話番号")
    details: tuple[str, ...] = ()  # 人が読める詳細（検出カテゴリ・該当ページ等）


@dataclass
class FileRecord:
    """フォルダスキャンで得られる1ファイル分の状態把握結果。"""

    path: Path
    size_bytes: int
    format_status: FormatStatus
    detected_format: str  # 判定された実体形式（例: "PDF", "JPEG", "DOCX", "unknown"）
    classification: Classification
    filename_finding: FilenameFinding
    metadata_finding: MetadataFinding | None = None
    pdf_finding: PdfFinding | None = None
    redaction_finding: RedactionFinding | None = None
    pii_finding: PiiFinding | None = None
    exceeds_single_file_limit: bool = False  # 要件定義書4.5「単体ファイル200MB超過の検出・警告」
    # 判定に使った単体上限（config.upload.max_total_mb）。表示文言用（設計原則5）。
    single_file_limit_mb: float | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def size_mb(self) -> float:
        return self.size_bytes / (1024 * 1024)


@dataclass
class EvidenceNumberMatch:
    """証拠番号パターンの判定結果（要件定義書 4.3「ファイル名パターンの判定」）。

    符号（甲/乙/丙）＋被告複数時の添字（乙A等、準備の手引29頁の原文で半角指定）
    ＋半角3桁数字＋任意の枝番（ハイフン/アンダースコアいずれも許容。
    準備の手引29頁は「-」、FAQは「_」）。
    """

    symbol: str
    subletter: str  # 例: "A"（半角）。無ければ空文字列
    number: str  # 例: "001"
    branch_from: str | None = None
    branch_to: str | None = None
    title: str = ""  # 証拠番号部分より後ろの標目（拡張子を除く）
    # 証拠番号部分と標目の間に元々あった区切り文字（空白・"-"・"_"等。無ければ
    # 空文字列）。ツールはファイル名の内容の適否を判断できないため（4.3節）、
    # 利用者が選んだ区切り文字をそのまま保持し、枝番ファイル結合時のファイル名
    # 生成（processors/evidence_merger.py）で流用する（ツールが独自の区切り文字
    # を押し付けない）。
    title_separator: str = " "

    @property
    def evidence_number_label(self) -> str:
        base = f"{self.symbol}{self.subletter}{self.number}"
        if self.branch_from and self.branch_to:
            return f"{base}-{self.branch_from}~{self.branch_to}"
        if self.branch_from:
            return f"{base}-{self.branch_from}"
        return base


@dataclass
class EvidenceSequenceFinding:
    """号証の連番・欠番チェック結果（要件定義書 4.7「号証の連番・欠番チェック」）。

    符号（甲/乙/丙、乙A等）ごとに独立した連番系列として扱う。枝番（甲001-1〜3等）
    は親番号単位で1つと数える。検出・警告のみで、自動採番の押し付けはしない。
    ファイルの内容を一切変更しない読み取り専用の集計処理のため、CLAUDE.mdの
    確認UI原則（設計原則3）の対象外＝自動実行してよい。
    """

    symbol_key: str  # 例："甲"、"乙A"（符号＋添字）
    missing_numbers: tuple[int, ...] = ()  # 欠番（観測範囲1〜最大番号のうち未出現のもの）
    duplicate_numbers: tuple[int, ...] = ()  # 重複（同一符号・番号・枝番の組み合わせが複数ファイルに存在）


@dataclass
class ScanResult:
    """フォルダ単位のスキャン結果一式。"""

    root: Path
    include_subfolders: bool
    records: list[FileRecord] = field(default_factory=list)

    def by_classification(self, classification: Classification) -> list[FileRecord]:
        return [r for r in self.records if r.classification == classification]

    @property
    def total_size_bytes(self) -> int:
        return sum(r.size_bytes for r in self.records)
