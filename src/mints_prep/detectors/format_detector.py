"""形式判定（拡張子＋マジックナンバー照合）。

docs/03_requirements.md 4.2「形式判定」：拡張子及びファイルのマジックナンバー（実体）を
照合し、対応／変換要／対象外を判定する。

libmagic（python-magic）はWindows単体exe化（CLAUDE.md「配布形態に関する留意」）で
別途バイナリ同梱が必要になり依存が重いため採用せず、VA mintsが扱う形式の
範囲に限定した自前のシグネチャ照合とする。
"""

from __future__ import annotations

from pathlib import Path

from mints_prep.models import FormatStatus

# 「記録」対応形式（docs/03_requirements.md 2.1節）
RECORD_EXTENSIONS = {".pdf", ".mp4", ".mp3", ".jpg", ".jpeg", ".png"}

# 変換元として受理する非対応形式（同節）
CONVERTIBLE_EXTENSIONS = {
    # 文書（記録外専用。変換先はPDF）
    ".docx", ".xlsx", ".pptx", ".doc", ".xls", ".ppt",
    # 動画（変換先はMP4）
    ".mov", ".avi", ".wmv",
    # 音声（変換先はMP3）
    ".wav", ".m4a", ".aac",
    # 画像（変換先はJPEG又はPNG）
    ".tiff", ".tif", ".bmp", ".gif", ".heic",
}

_EXTENSION_TO_FORMAT_NAME = {
    ".pdf": "PDF", ".mp4": "MP4", ".mp3": "MP3", ".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG",
    ".docx": "DOCX", ".xlsx": "XLSX", ".pptx": "PPTX",
    ".doc": "DOC(旧形式)", ".xls": "XLS(旧形式)", ".ppt": "PPT(旧形式)",
    ".mov": "MOV", ".avi": "AVI", ".wmv": "WMV",
    ".wav": "WAV", ".m4a": "M4A", ".aac": "AAC",
    ".tiff": "TIFF", ".tif": "TIFF", ".bmp": "BMP", ".gif": "GIF", ".heic": "HEIC",
}


def _read_head(path: Path, n: int = 16) -> bytes:
    with open(path, "rb") as f:
        return f.read(n)


def _sniff_magic(path: Path) -> str | None:
    """マジックナンバーから実体形式を推定する。判定できない場合は None。

    OLE2（旧Office形式）とZIP系（新Office形式）はコンテナが共通のため、
    内部構造まで見ずヘッダのみで判定できる範囲に限定する
    （旧形式か新形式かの厳密な判別は拡張子照合と併用する前提）。
    """
    head = _read_head(path, 16)
    if head.startswith(b"%PDF-"):
        return "PDF"
    if head.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if head[4:8] == b"ftyp":
        # ISOベースメディアファイル（MP4系コンテナ）。HEICもftypを共有する
        # ため、ブランド（8〜12バイト目）で先に判別する（「MP4」判定を先に
        # returnするとHEIC判定が到達不能になり、.mp4拡張子のHEICが記録対応
        # 形式をすり抜ける）。
        brand = head[8:12]
        if brand in (b"heic", b"heix", b"hevc", b"heif", b"mif1", b"msf1"):
            return "HEIC"
        if brand == b"qt  ":
            return "MOV"
        return "MP4"
    if head.startswith(b"ID3") or head[:2] in (b"\xff\xfa", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "MP3"
    if head.startswith(b"PK\x03\x04"):
        return "ZIP_CONTAINER"  # DOCX/XLSX/PPTX いずれか（拡張子で区別）
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "OLE2_CONTAINER"  # DOC/XLS/PPT（旧形式）いずれか
    if head.startswith(b"II*\x00") or head.startswith(b"MM\x00*"):
        return "TIFF"
    if head.startswith(b"BM"):
        return "BMP"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return "GIF"
    if head.startswith(b"RIFF") and head[8:12] == b"AVI ":
        return "AVI"
    if head.startswith(b"\x30\x26\xb2\x75\x8e\x66\xcf\x11"):
        return "WMV"
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return "WAV"
    return None


_ZIP_CONTAINER_EXT_MAP = {".docx": "DOCX", ".xlsx": "XLSX", ".pptx": "PPTX"}
_OLE2_CONTAINER_EXT_MAP = {".doc": "DOC(旧形式)", ".xls": "XLS(旧形式)", ".ppt": "PPT(旧形式)"}


def detect_format(path: Path) -> tuple[FormatStatus, str]:
    """拡張子とマジックナンバーを照合し、(FormatStatus, 判定された実体形式名) を返す。"""
    ext = path.suffix.lower()
    magic = _sniff_magic(path)

    if magic == "ZIP_CONTAINER" and ext in _ZIP_CONTAINER_EXT_MAP:
        detected = _ZIP_CONTAINER_EXT_MAP[ext]
        return FormatStatus.CONVERTIBLE, detected
    if magic == "OLE2_CONTAINER" and ext in _OLE2_CONTAINER_EXT_MAP:
        detected = _OLE2_CONTAINER_EXT_MAP[ext]
        return FormatStatus.CONVERTIBLE, detected

    if ext in RECORD_EXTENSIONS:
        if magic is None:
            return FormatStatus.EXTENSION_MISMATCH, "unknown"
        if magic != _EXTENSION_TO_FORMAT_NAME.get(ext):
            # JPG/JPEGの表記ゆれのみ許容
            if not (magic == "JPEG" and ext in (".jpg", ".jpeg")):
                return FormatStatus.EXTENSION_MISMATCH, magic
        return FormatStatus.RECORD_READY, _EXTENSION_TO_FORMAT_NAME[ext]

    if ext in CONVERTIBLE_EXTENSIONS:
        # マジックナンバーで判定できない形式（HEIC等の亜種、拡張子頼みで妥当とする）は
        # 拡張子ベースの判定にフォールバックする。
        return FormatStatus.CONVERTIBLE, _EXTENSION_TO_FORMAT_NAME.get(ext, ext.lstrip("."))

    return FormatStatus.UNSUPPORTED, magic or ext.lstrip(".") or "unknown"
