"""メタデータ除去（要件定義書 4.5「メタデータ除去」）。

PDFプロパティ、画像Exif（GPS含む）、音声・動画タグ情報を一括除去する。
根拠：docs/02_constraints-report.md 第3章5節（Q&A 10頁、操作マニュアル別紙3）。

Office文書プロパティの除去は、Office文書自体が書き出し対象（記録対応形式）
ではない（C分類＝要変換。4.4節の形式変換機能はチェック専念方針により廃止）
ため対象としない。

このモジュールは実際にファイルを変更する処理（除去済みコピーの生成）を
含むため、呼び出し側は必ず確認UI（ui/write_review_dialog.py の逐次レビュー
画面）でユーザーの確認を得たうえで実行すること（CLAUDE.md「絶対に譲れない
設計原則」3）。元ファイルは変更しない（出力先へ除去済みコピーを新規に書き出す）。

【既知の制約】MP3/MP4のタグ除去はmutagenで実装しているが、自動テストによる
検証はPDF・画像（JPEG/PNG）にとどまる（有効なMP3/MP4フィクスチャの合成が
複雑なため）。音声・動画ファイルでの実運用前には手動確認を推奨する。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pikepdf
from PIL import Image

_IMAGE_FORMATS = {"JPEG", "PNG"}
_AUDIO_VIDEO_FORMATS = {"MP3", "MP4"}


def strip_pdf_metadata(src: Path, dest: Path) -> None:
    """PDFのdocinfo（作成者・編集日時等）及びXMPメタデータを除去したコピーを作成する。

    pikepdfのopen_metadata()は既定でset_pikepdf_as_editor=True・
    update_docinfo=Trueであり、そのままclear()してもpikepdf自身が保存時に
    Producer（作成ソフト名）とMetadataDate（編集日時）を再挿入してしまう。
    その結果、除去済みの出力を再読み込みしても「メタデータあり（Producer）」
    と検出され続け、「処理→再読み込み→A（適合済み）」の再検証ループが
    収束しない不具合があったため、両オプションを無効化し、XMPストリーム
    自体（/Root/Metadata）も削除する。
    """
    with pikepdf.open(src) as pdf:
        for key in list(pdf.docinfo.keys()):
            del pdf.docinfo[key]
        with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as meta:
            meta.clear()
        if "/Metadata" in pdf.Root:
            del pdf.Root.Metadata
        pdf.save(dest)


# JPEGのメタデータ用セグメントマーカー（2バイト目）。これらのセグメントのみを
# 取り除き、圧縮済み画像データ（DQT/DHT/SOF/SOS以降）には一切手を触れない。
#   0xE1 APP1（Exif・XMP）／0xE2 APP2（ICCプロファイル）／
#   0xED APP13（Photoshop IRB）／0xFE COM（コメント）
# APP0（JFIFヘッダ）・APP14（Adobe色変換情報。除去するとCMYK系JPEGの色解釈が
# 壊れる）等の構造・表示に必要なセグメントは残す。検出側（metadata_detector）が
# メタデータとして報告する項目（exif・icc_profile・photoshop・comment）と対応
# しており、除去済み出力の再読み込みで「メタデータなし」に収束する。
_JPEG_METADATA_MARKERS = {0xE1, 0xE2, 0xED, 0xFE}
# 長さフィールドを持たないスタンドアロンマーカー（TEM・RSTn）。
_JPEG_STANDALONE_MARKERS = {0x01} | set(range(0xD0, 0xD8))


def _strip_jpeg_metadata_segments(src: Path, dest: Path) -> None:
    """JPEGのメタデータセグメントのみをバイトレベルで取り除いたコピーを作成する。

    画像を開いて保存し直す方式（再エンコード）は、JPEGでは非可逆の再圧縮を
    伴い画質が劣化する（原本忠実性〔CLAUDE.md設計原則4〕に反する）。そのため
    ファイル構造（セグメント列）を走査し、メタデータ用セグメントだけを落として
    残りをそのまま書き出す。圧縮済み画像データは1バイトも変わらない（無損失）。
    """
    data = src.read_bytes()
    if len(data) < 4 or data[0:2] != b"\xff\xd8":
        raise ValueError("JPEGファイルの形式が不正です（SOIマーカーがありません）")

    out = bytearray(b"\xff\xd8")
    pos = 2
    while pos < len(data):
        if data[pos] != 0xFF:
            raise ValueError("JPEGファイルの形式が不正です（セグメント境界が壊れています）")
        marker = data[pos + 1] if pos + 1 < len(data) else None
        if marker is None:
            break
        if marker in _JPEG_STANDALONE_MARKERS:
            out += data[pos:pos + 2]
            pos += 2
            continue
        if marker == 0xDA:  # SOS：以降はエントロピー符号化データ。無加工でコピーする。
            out += data[pos:]
            break
        if pos + 4 > len(data):
            raise ValueError("JPEGファイルの形式が不正です（セグメント長が壊れています）")
        seg_len = int.from_bytes(data[pos + 2:pos + 4], "big")
        seg_end = pos + 2 + seg_len
        if seg_len < 2 or seg_end > len(data):
            raise ValueError("JPEGファイルの形式が不正です（セグメント長が壊れています）")
        if marker not in _JPEG_METADATA_MARKERS:
            out += data[pos:seg_end]
        pos = seg_end
    dest.write_bytes(bytes(out))


def strip_image_metadata(src: Path, dest: Path) -> None:
    """画像のExif（GPS位置情報等）を除去したコピーを作成する。

    JPEGはメタデータセグメントのみのバイトレベル除去（_strip_jpeg_metadata_segments）
    を用いる。画像を開いて保存し直す方式では非可逆の再圧縮（Pillow既定
    quality=75）が発生し、メタデータ除去のはずが画質まで劣化してしまう
    （原本忠実性〔CLAUDE.md設計原則4〕への違反）ため、圧縮済み画像データには
    一切手を触れない方式とする。

    PNG（可逆圧縮のため再保存しても画質は変わらない）は、ピクセルデータのみを
    新規Imageオブジェクトへ複製することで、テキストチャンク・ICCプロファイル等の
    付随情報を一切引き継がない。

    パレット形式（Pモード。PNG8等）はピクセル値がパレットへのインデックスの
    ため、パレット自体を複製しないと出力が真っ黒になる（原本忠実性への重大な
    違反となる）。パレットと透過設定は「画像の内容そのもの」でありメタデータ
    ではないため、必ず引き継ぐ。
    """
    with Image.open(src) as image:
        image_format = image.format
        if image_format == "JPEG":
            pass  # withブロックの外でバイトレベル処理する（Pillowで開き直さない）。
        else:
            clean = Image.frombytes(image.mode, image.size, image.tobytes())
            if image.mode == "P":
                palette = image.getpalette()
                if palette is not None:
                    clean.putpalette(palette)
                transparency = image.info.get("transparency")
                if transparency is not None:
                    clean.info["transparency"] = transparency
                    clean.save(dest, format=image_format, transparency=transparency)
                    return
            clean.save(dest, format=image_format)
            return
    _strip_jpeg_metadata_segments(src, dest)


def strip_audio_video_metadata(src: Path, dest: Path) -> None:
    """音声・動画のタグ情報（作成者・作成ソフト・GPS座標等）を除去したコピーを作成する。

    検出側（detectors/metadata_detector.py）と同じく生タグAPI（easy=False）を
    使う。delete()はマッピング済みキーに限らずタグ全体（MP4の©xyzアトム等の
    非標準タグを含む）を除去するため、検出された項目が除去後も残ることはない。
    """
    import mutagen

    shutil.copy2(src, dest)
    audio = mutagen.File(dest)
    if audio is not None and audio.tags is not None:
        audio.delete()
        audio.save()


def strip_metadata(src: Path, dest: Path, detected_format: str) -> None:
    """検出形式に応じたメタデータ除去処理を実行する。"""
    if detected_format == "PDF":
        strip_pdf_metadata(src, dest)
    elif detected_format in _IMAGE_FORMATS:
        strip_image_metadata(src, dest)
    elif detected_format in _AUDIO_VIDEO_FORMATS:
        strip_audio_video_metadata(src, dest)
    else:
        # 対応外形式（想定外の呼び出し）は安全側としてそのままコピーする。
        shutil.copy2(src, dest)
