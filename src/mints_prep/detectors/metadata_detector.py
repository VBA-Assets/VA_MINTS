"""メタデータ有無の検出（全形式）。

docs/03_requirements.md 4.2「メタデータ」：作成者・編集者・GPS位置情報等の有無を検出する。
根拠：docs/02_constraints-report.md 第3章5節（PDFプロパティ、画像Exif〔GPS必須〕、
Office文書プロパティ、音声・動画タグ情報）。

検出のみであり、除去は行わない（除去は4.5節・確認必須の別処理）。
このモジュールが読み取り方法を持たない形式（旧Office形式・一部動画コンテナ等）は
None を返し、呼び出し側で「検出未対応形式」として扱う。
"""

from __future__ import annotations

from pathlib import Path

import pikepdf
from PIL import ExifTags, Image

from mints_prep.detectors.av_tags import mp3_tag_fields, mp4_tag_fields
from mints_prep.models import MetadataFinding

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".gif"}
_OFFICE_DOCX = {".docx"}
_OFFICE_XLSX = {".xlsx"}
_OFFICE_PPTX = {".pptx"}
_AUDIO_VIDEO_EXTS = {".mp3", ".mp4", ".wav", ".m4a", ".aac", ".mov", ".avi", ".wmv"}
_ID3_EXTS = {".mp3", ".aac"}
_MP4_EXTS = {".mp4", ".m4a", ".mov"}


def _detect_pdf(path: Path) -> MetadataFinding:
    try:
        with pikepdf.open(path) as pdf:
            fields = [str(k).lstrip("/") for k in pdf.docinfo.keys()] if pdf.docinfo else []
            # docinfoが空でもXMPメタデータ（/Root/Metadata）にのみ作成者・
            # 作成ツールが残るPDFがある（近年の生成系ソフトで一般的）ため、
            # XMPストリームの有無も確認する。
            if "/Metadata" in pdf.Root:
                try:
                    with pdf.open_metadata() as meta:
                        if len(meta) > 0 and "XMP" not in fields:
                            fields.append("XMP")
                except Exception:
                    # XMPの解析に失敗しても、ストリームが存在する事実は
                    # 「メタデータあり」として扱う（安全側）。
                    if "XMP" not in fields:
                        fields.append("XMP")
            return MetadataFinding(has_metadata=bool(fields), detected_fields=tuple(fields))
    except pikepdf.PasswordError:
        # パスワード付きは detect_pdf（pdf_detector）側で別途警告するため、
        # ここでは検出不可として扱う。
        return MetadataFinding(has_metadata=False, detected_fields=())


# PILがJPEG/PNGの構造情報としてimg.infoへ必ず（又は高頻度で）格納するキー。
# JFIFヘッダやdpi等はどのJPEGにもある形式上の情報であり、作成者・位置情報の
# ような「除去すべきメタデータ」ではない。これらまで検出対象にすると、
# ごく普通のJPEGが全件「メタデータあり→B（要修正）」になってしまう。
_STRUCTURAL_INFO_KEYS = {
    "jfif", "jfif_version", "jfif_unit", "jfif_density",
    "dpi", "adobe", "adobe_transform", "progression", "progressive",
    "gamma", "srgb", "chromaticity", "interlace", "transparency", "aspect",
}


def _detect_image(path: Path) -> MetadataFinding:
    fields: list[str] = []
    with Image.open(path) as img:
        exif = img.getexif()
        for tag_id in exif:
            tag_name = ExifTags.TAGS.get(tag_id, str(tag_id))
            fields.append(tag_name)
        # GPS情報はIFDが別扱いになる場合があるため個別に確認する。
        gps_ifd = exif.get_ifd(ExifTags.IFD.GPSInfo) if hasattr(ExifTags, "IFD") else None
        if gps_ifd:
            fields.append("GPSInfo")
        # PNG等のテキストチャンク（Author/Software等）・ICCプロファイル等。
        # 形式上の構造キー（_STRUCTURAL_INFO_KEYS）は除外する。
        for key in img.info:
            if (
                isinstance(key, str)
                and key.lower() not in _STRUCTURAL_INFO_KEYS
                and key not in fields
            ):
                fields.append(key)
    return MetadataFinding(has_metadata=bool(fields), detected_fields=tuple(fields))


def _detect_docx(path: Path) -> MetadataFinding:
    from docx import Document

    props = Document(path).core_properties
    fields = [
        name
        for name in ("author", "last_modified_by", "company", "title", "comments")
        if getattr(props, name, None)
    ]
    return MetadataFinding(has_metadata=bool(fields), detected_fields=tuple(fields))


def _detect_xlsx(path: Path) -> MetadataFinding:
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True)
    try:
        props = wb.properties
        fields = [
            name
            for name in ("creator", "lastModifiedBy", "company", "title", "description")
            if getattr(props, name, None)
        ]
        return MetadataFinding(has_metadata=bool(fields), detected_fields=tuple(fields))
    finally:
        wb.close()


def _detect_pptx(path: Path) -> MetadataFinding:
    from pptx import Presentation

    props = Presentation(path).core_properties
    fields = [
        name
        for name in ("author", "last_modified_by", "company", "title", "comments")
        if getattr(props, name, None)
    ]
    return MetadataFinding(has_metadata=bool(fields), detected_fields=tuple(fields))


def _detect_audio_video(path: Path) -> MetadataFinding | None:
    """音声・動画のタグ（av_tags.py。Ver201 までの mutagen を、2026-11-17 に自前の読み取りに替えた）。

    タグの名前は、決まった項目（題名・作成者など）に限らず、見つけたものをすべて挙げる
    （スマートフォンの動画の位置〔©xyz・com.apple.quicktime.location.ISO6709〕を見逃さない。
    要件定義書 4.2「GPS位置情報等の有無を検出」）。
    """
    ext = path.suffix.lower()
    if ext in _ID3_EXTS:
        fields = tuple(mp3_tag_fields(path))
    elif ext in _MP4_EXTS:
        found = mp4_tag_fields(path)
        if found is None:
            return None
        fields = tuple(found)
    else:
        # 読み方を持たない形式（WAV・AVI・WMV）。「メタデータなし」と混同せず「検出不可」と
        # して扱う（旧Office形式のNone扱いと同じ。呼び出し側で「検出未対応形式」の注記になる）。
        return None
    return MetadataFinding(has_metadata=bool(fields), detected_fields=fields)


def detect_metadata(path: Path) -> MetadataFinding | None:
    """メタデータ有無を検出する。読み取り方法を持たない形式では None を返す。"""
    ext = path.suffix.lower()
    try:
        if ext == ".pdf":
            return _detect_pdf(path)
        if ext in _IMAGE_EXTS:
            return _detect_image(path)
        if ext in _OFFICE_DOCX:
            return _detect_docx(path)
        if ext in _OFFICE_XLSX:
            return _detect_xlsx(path)
        if ext in _OFFICE_PPTX:
            return _detect_pptx(path)
        if ext in _AUDIO_VIDEO_EXTS:
            return _detect_audio_video(path)
    except Exception:
        # 破損ファイル等、読み取り自体に失敗した場合は「検出不可」として扱う
        # （形式判定・パスワード検出等の他の判定結果でユーザーに気付かせる）。
        return None
    return None
