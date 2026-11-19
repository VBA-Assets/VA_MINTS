"""音声（MP3）・動画（MP4）のタグ情報を、ファイルの構造から直接読む（Ver300。2026-11-17）。

Ver201 までは mutagen を使っていたが、mutagen のライセンス（GPL-2.0-or-later）は、VA mints の
MIT License と合わない（exe に入れると、exe 全体を GPL の条件で配ることになる）。そのため、
2026-11-17 の班長決定で、mutagen を外し、ここで自前で読む。

・MP3（と ADTS の AAC）：先頭の ID3v2、末尾の ID3v1・APEv2 を探す。どこにあるかが決まっている
  （ファイルの前と後ろに付く）ので、取り除くのは、その範囲を除いて写すだけでよい。音声のデータは
  1バイトも変えない（processors/metadata_remover.py）。
・MP4（と M4A・MOV）：箱（box）の並びをたどり、moov・trak の下の udta（ユーザーデータ。
  スマートフォンの位置 ©xyz など）と meta（ilst・keys。iPhone の位置・機種・ソフトなど）、
  XMP の uuid 箱を探す。取り除くには、箱の大きさと、音声・映像のデータの位置（stco・co64）を
  書き換える必要があり、証拠を壊すおそれがあるので、このツールでは取り除かない（知らせるだけ）。

どちらも、ファイルの全体は読まない（大きなファイルでも、先頭・末尾と箱の見出しだけを読む）。
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import BinaryIO

# ---------------------------------------------------------------- MP3（ID3・APE）

_ID3V1_SIZE = 128
_ID3V1_EXT_SIZE = 227          # 拡張 ID3v1（"TAG+"。ID3v1 の直前に付く）
_APE_FOOTER_SIZE = 32


def _syncsafe(b: bytes) -> int:
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def _id3v2_frames(tag: bytes) -> list[str]:
    """ID3v2 の見出しの後ろの、フレームの名前（TIT2・TPE1・APIC など）の並び。読めなければ空。"""
    major, flags = tag[3], tag[5]
    pos = 10
    if flags & 0x40:  # 拡張ヘッダ
        if major == 4:
            pos += _syncsafe(tag[10:14])
        elif major == 3:
            pos += 4 + int.from_bytes(tag[10:14], "big")
    names: list[str] = []
    id_len, size_len = (3, 3) if major == 2 else (4, 4)
    head = id_len + size_len + (0 if major == 2 else 2)
    while pos + head <= len(tag):
        frame_id = tag[pos:pos + id_len]
        if not frame_id.strip(b"\x00") or not all(48 <= c <= 57 or 65 <= c <= 90 for c in frame_id):
            break  # パディング（0 埋め）か、読めない並び
        raw = tag[pos + id_len:pos + id_len + size_len]
        size = _syncsafe(raw) if major == 4 else int.from_bytes(raw, "big")
        names.append(frame_id.decode("ascii"))
        pos += head + size
    return names


def id3_spans(f: BinaryIO, file_size: int) -> tuple[list[tuple[int, int]], list[str]]:
    """タグの置かれた範囲（[始め, 終わり) の並び）と、見つけた項目の名前。

    先頭の ID3v2（いくつか続くことがある）、末尾の ID3v1（拡張 ID3v1 を含む）・APEv2・
    末尾の ID3v2（"3DI" のフッター付き）を探す。
    """
    spans: list[tuple[int, int]] = []
    fields: list[str] = []

    pos = 0
    while pos + 10 <= file_size:
        f.seek(pos)
        header = f.read(10)
        if header[:3] != b"ID3" or header[3] not in (2, 3, 4) or any(c & 0x80 for c in header[6:10]):
            break
        size = 10 + _syncsafe(header[6:10]) + (10 if header[3] == 4 and header[5] & 0x10 else 0)
        end = min(file_size, pos + size)
        f.seek(pos)
        tag = f.read(end - pos)
        frames = _id3v2_frames(tag) if not header[5] & 0x80 else []  # 非同期化されたものは名前を読まない
        fields += [f"ID3v2:{n}" for n in frames] or ["ID3v2"]
        spans.append((pos, end))
        pos = end

    end = file_size
    if end - _ID3V1_SIZE >= pos:
        f.seek(end - _ID3V1_SIZE)
        if f.read(3) == b"TAG":
            start = end - _ID3V1_SIZE
            if start - _ID3V1_EXT_SIZE >= pos:
                f.seek(start - _ID3V1_EXT_SIZE)
                if f.read(4) == b"TAG+":
                    start -= _ID3V1_EXT_SIZE
            spans.append((start, end))
            fields.append("ID3v1")
            end = start
    if end - _APE_FOOTER_SIZE >= pos:
        f.seek(end - _APE_FOOTER_SIZE)
        footer = f.read(_APE_FOOTER_SIZE)
        if footer[:8] == b"APETAGEX":
            size, _count, flags = struct.unpack("<III", footer[12:24])
            total = size + (_APE_FOOTER_SIZE if flags & 0x80000000 else 0)
            start = end - total
            if pos <= start < end:
                spans.append((start, end))
                fields.append("APEv2")
                end = start
    if end - 10 >= pos:
        f.seek(end - 10)
        footer = f.read(10)
        if footer[:3] == b"3DI" and not any(c & 0x80 for c in footer[6:10]):
            start = end - 20 - _syncsafe(footer[6:10])
            if pos <= start < end:
                spans.append((start, end))
                fields.append("ID3v2")
    return sorted(spans), fields


def mp3_tag_fields(path: Path) -> list[str]:
    with open(path, "rb") as f:
        return id3_spans(f, path.stat().st_size)[1]


def strip_mp3_tags(src: Path, dest: Path) -> None:
    """ID3・APE のタグの範囲だけを除いて写す（音声のデータは、そのまま写す）。"""
    size = src.stat().st_size
    with open(src, "rb") as f:
        spans, _fields = id3_spans(f, size)
        keep: list[tuple[int, int]] = []
        pos = 0
        for start, end in spans:
            if start > pos:
                keep.append((pos, start))
            pos = max(pos, end)
        if pos < size:
            keep.append((pos, size))
        with open(dest, "wb") as out:
            for start, end in keep:
                f.seek(start)
                left = end - start
                while left:
                    chunk = f.read(min(left, 1 << 20))
                    if not chunk:
                        break
                    out.write(chunk)
                    left -= len(chunk)


# ---------------------------------------------------------------- MP4（ISO の箱）

# 中を見る箱（ほかの箱は、見出しだけ読んで飛ばす。mdat〔音声・映像のデータ〕は読まない）
_CONTAINERS = {b"moov", b"trak"}
_PADDING = {b"free", b"skip"}      # 詰め物の箱（中身は空き。タグではない）
_XMP_UUID =bytes.fromhex("BE7ACFCB97A942E89C71999491E3AFAC")
_MAX_META_BOX = 16 * 1024 * 1024   # udta・meta を丸ごと読む上限（ふつうは数 KB）


def _boxes(f: BinaryIO, start: int, end: int):
    """[start, end) にある箱の（種類, 中身の始め, 箱の終わり）を順に返す。"""
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        head = f.read(8)
        if len(head) < 8:
            return
        size, kind = struct.unpack(">I4s", head)
        body = pos + 8
        if size == 1:
            large = f.read(8)
            if len(large) < 8:
                return
            size = struct.unpack(">Q", large)[0]
            body = pos + 16
        elif size == 0:
            size = end - pos
        if size < body - pos or pos + size > end:
            return  # 壊れた箱。そこで読むのをやめる
        yield kind, body, pos + size
        pos += size


def _name(kind: bytes) -> str:
    return kind.decode("latin-1")


def _child_boxes(data: bytes, start: int):
    pos = start
    while pos + 8 <= len(data):
        size, kind = struct.unpack(">I4s", data[pos:pos + 8])
        if size < 8 or pos + size > len(data):
            return
        yield kind, pos + 8, pos + size
        pos += size


def _meta_fields(data: bytes) -> list[str]:
    """meta 箱の中身（data は見出しの後ろ）から、ilst の項目と keys の名前を読む。"""
    # ISO の meta は版・フラグ（4バイト）で始まり、QuickTime の meta は始まらない
    start = 4 if data[:4] == b"\x00\x00\x00\x00" else 0
    keys: list[str] = []
    items: list[str] = []
    for kind, body, end in _child_boxes(data, start):
        if kind == b"keys":
            count = int.from_bytes(data[body + 4:body + 8], "big")
            pos = body + 8
            for _ in range(count):
                if pos + 8 > end:
                    break
                size = int.from_bytes(data[pos:pos + 4], "big")
                if size < 8:
                    break
                keys.append(data[pos + 8:pos + size].decode("utf-8", "replace"))
                pos += size
        elif kind == b"ilst":
            for item, _b, _e in _child_boxes(data, body):
                if item not in _PADDING:
                    items.append(item)
    fields = []
    for item in items:
        index = int.from_bytes(item, "big")
        if keys and 1 <= index <= len(keys):
            fields.append(keys[index - 1])      # QuickTime の keys の番号
        else:
            fields.append(_name(item))
    return fields


def _udta_fields(data: bytes) -> list[str]:
    fields: list[str] = []
    for kind, body, end in _child_boxes(data, 0):
        if kind == b"meta":
            fields += _meta_fields(data[body:end])
        elif end > body and kind not in _PADDING:  # 中身の空の箱・詰め物の箱は数えない
            fields.append(_name(kind))
    return fields


def mp4_tag_fields(path: Path) -> list[str] | None:
    """MP4 のタグの項目の名前（©xyz・com.apple.quicktime.location.ISO6709 など）。
    箱の並びとして読めなければ None（検出できない）。"""
    size = path.stat().st_size
    with open(path, "rb") as f:
        top = list(_boxes(f, 0, size))
        if not top or top[0][0] != b"ftyp":
            return None
        fields: list[str] = []

        def walk(start: int, end: int, depth: int) -> None:
            for kind, body, box_end in _boxes(f, start, end):
                if kind in _CONTAINERS and depth < 3:
                    walk(body, box_end, depth + 1)
                elif kind in (b"udta", b"meta") and depth >= 1:
                    if box_end - body > _MAX_META_BOX:
                        fields.append(_name(kind))
                        continue
                    f.seek(body)
                    data = f.read(box_end - body)
                    fields.extend(_udta_fields(data) if kind == b"udta" else _meta_fields(data))
                elif kind == b"uuid" and depth == 0:
                    f.seek(body)
                    if f.read(16) == _XMP_UUID:
                        fields.append("XMP")
                elif kind == b"meta" and depth == 0:
                    if box_end - body <= _MAX_META_BOX:
                        f.seek(body)
                        fields.extend(_meta_fields(f.read(box_end - body)) or ["meta"])

        walk(0, size, 0)
    return list(dict.fromkeys(fields))
