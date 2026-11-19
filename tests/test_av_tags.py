"""音声（MP3）・動画（MP4）のタグの読み取りと、MP3 のタグの除去（detectors/av_tags.py。2026-11-17、
mutagen〔GPL〕を外して自前にした）。"""

import struct
from pathlib import Path

from va_mints.detectors.av_tags import mp3_tag_fields, mp4_tag_fields, strip_mp3_tags
from va_mints.detectors.metadata_detector import detect_metadata
from va_mints.processors.metadata_remover import can_strip_metadata, strip_metadata

# 有効な MPEG1 Layer3 のフレーム（128kbps・44.1kHz＝417バイト）×4
_AUDIO = (b"\xff\xfb\x90\x64" + b"\x00" * 413) * 4


def _syncsafe(n: int) -> bytes:
    return bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F])


def _id3v23(*frames: tuple[bytes, bytes], padding: int = 20) -> bytes:
    body = b"".join(fid + struct.pack(">I", len(data)) + b"\x00\x00" + data for fid, data in frames)
    body += b"\x00" * padding
    return b"ID3\x03\x00\x00" + _syncsafe(len(body)) + body


def _id3v24(*frames: tuple[bytes, bytes], footer: bool = False) -> bytes:
    body = b"".join(fid + _syncsafe(len(data)) + b"\x00\x00" + data for fid, data in frames)
    flags = 0x10 if footer else 0
    tag = b"ID3\x04\x00" + bytes([flags]) + _syncsafe(len(body)) + body
    if footer:
        tag += b"3DI\x04\x00" + bytes([flags]) + _syncsafe(len(body))
    return tag


def _id3v1() -> bytes:
    return b"TAG" + "秘密".encode("cp932").ljust(30, b"\x00") + b"\x00" * 95


def _ape() -> bytes:
    item = struct.pack("<II", 5, 0) + b"Title\x00" + b"hello"
    size = len(item) + 32
    header = b"APETAGEX" + struct.pack("<IIII", 2000, size, 1, 0xA0000000) + b"\x00" * 8
    footer = b"APETAGEX" + struct.pack("<IIII", 2000, size, 1, 0x80000000) + b"\x00" * 8
    return header + item + footer


def test_mp3_tags_detected_and_stripped(tmp_path: Path):
    """先頭の ID3v2、末尾の APEv2・ID3v1 を見つけ、取り除くと音声のデータだけが残る。"""
    src = tmp_path / "audio.mp3"
    title = "秘密のタイトル".encode("utf-16")
    src.write_bytes(_id3v23((b"TIT2", b"\x01" + title), (b"TPE1", b"\x00me")) + _AUDIO + _ape() + _id3v1())

    fields = mp3_tag_fields(src)
    assert fields == ["ID3v2:TIT2", "ID3v2:TPE1", "ID3v1", "APEv2"]
    finding = detect_metadata(src)
    assert finding.has_metadata and "ID3v2:TIT2" in finding.detected_fields

    dest = tmp_path / "out.mp3"
    strip_metadata(src, dest, "MP3")
    assert dest.read_bytes() == _AUDIO        # 音声のデータは1バイトも変わらない
    assert detect_metadata(dest).has_metadata is False


def test_mp3_id3v24_with_footer_and_several_tags(tmp_path: Path):
    src = tmp_path / "a.mp3"
    src.write_bytes(_id3v24((b"TIT2", b"\x03abc"), footer=True) + _id3v23((b"COMM", b"x")) + _AUDIO)
    assert mp3_tag_fields(src) == ["ID3v2:TIT2", "ID3v2:COMM"]
    dest = tmp_path / "b.mp3"
    strip_mp3_tags(src, dest)
    assert dest.read_bytes() == _AUDIO


def test_mp3_without_tags(tmp_path: Path):
    src = tmp_path / "plain.mp3"
    src.write_bytes(_AUDIO)
    finding = detect_metadata(src)
    assert finding is not None and finding.has_metadata is False
    dest = tmp_path / "out.mp3"
    strip_mp3_tags(src, dest)
    assert dest.read_bytes() == _AUDIO


def _box(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", 8 + len(body)) + kind + body


def _mp4(tagged: bool) -> bytes:
    ftyp = _box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2mp41")
    trak = _box(b"trak", _box(b"tkhd", b"\x00" * 84) + (_box(b"udta", _box(b"name", b"track")) if tagged else b""))
    moov_children = _box(b"mvhd", b"\x00" * 100) + trak
    if tagged:
        ilst = _box(b"ilst", _box(b"\xa9too", _box(b"data", b"\x00" * 8 + b"Lavf")))
        meta = _box(b"meta", b"\x00\x00\x00\x00" + _box(b"hdlr", b"\x00" * 25) + ilst)
        udta = _box(b"udta", _box(b"\xa9xyz", b"\x00\x11\x15\xc7+35.6812+139.7671/") + meta)
        # iPhone の形（QuickTime の meta：版・フラグなし、keys と番号の ilst）
        key = b"com.apple.quicktime.location.ISO6709"
        keys = _box(b"keys", b"\x00\x00\x00\x00" + struct.pack(">I", 1) + struct.pack(">I", 8 + len(key)) + b"mdta" + key)
        qt_ilst = _box(b"ilst", _box(struct.pack(">I", 1), _box(b"data", b"\x00" * 8 + b"+35.68+139.76/")))
        moov_children += udta + _box(b"meta", _box(b"hdlr", b"\x00" * 25) + keys + qt_ilst)
    out = ftyp + _box(b"moov", moov_children) + _box(b"mdat", b"\x00" * 64)
    if tagged:
        out += _box(b"uuid", bytes.fromhex("BE7ACFCB97A942E89C71999491E3AFAC") + b"<x:xmpmeta/>")
    return out


def test_mp4_tags_detected(tmp_path: Path):
    """スマートフォンの位置（©xyz・keys の ISO6709）、作成ソフト（©too）、XMP を見つける。"""
    path = tmp_path / "movie.mp4"
    path.write_bytes(_mp4(tagged=True))
    fields = mp4_tag_fields(path)
    for name in ("©xyz", "©too", "com.apple.quicktime.location.ISO6709", "XMP", "name"):
        assert name in fields, fields
    assert detect_metadata(path).has_metadata is True


def test_mp4_without_tags(tmp_path: Path):
    path = tmp_path / "movie.mp4"
    path.write_bytes(_mp4(tagged=False))
    assert mp4_tag_fields(path) == []
    assert detect_metadata(path).has_metadata is False


def test_mp4_not_readable_returns_none(tmp_path: Path):
    path = tmp_path / "broken.mp4"
    path.write_bytes(b"not a movie at all" * 4)
    assert detect_metadata(path) is None


def test_mp4_tags_are_not_stripped(tmp_path: Path):
    """MP4 のタグは取り除かない（知らせるだけ）。万一呼ばれても、そのまま写す。"""
    assert can_strip_metadata("MP4") is False
    assert can_strip_metadata("MP3") is True
    src = tmp_path / "movie.mp4"
    src.write_bytes(_mp4(tagged=True))
    dest = tmp_path / "out.mp4"
    strip_metadata(src, dest, "MP4")
    assert dest.read_bytes() == src.read_bytes()


def test_mp4_metadata_is_shown_as_notice_not_option():
    """右のペインでは、MP4 のタグを、チェックできない知らせとして出す。"""
    from va_mints.config import FilenameConfig
    from va_mints.models import Classification, FileRecord, FilenameFinding, FormatStatus, MetadataFinding
    from va_mints.processors.output_writer import PlannedCopy
    from va_mints.ui.review_logic import ReviewItem, build_review_options

    name = "甲004 動画.mp4"
    record = FileRecord(
        path=Path(name), size_bytes=100, format_status=FormatStatus.RECORD_READY, detected_format="MP4",
        classification=Classification.B_NEEDS_FIX,
        filename_finding=FilenameFinding(length=len(name), exceeds_max_length=False, exceeds_warning_length=False),
        metadata_finding=MetadataFinding(has_metadata=True, detected_fields=("©xyz",)),
    )
    prelim = PlannedCopy(record=record, output_filename=name)
    config = FilenameConfig(max_length=100, warning_length=50, forbidden_chars=("/",), jis_x0213_codec="shift_jis_2004")
    options = build_review_options(ReviewItem(preliminary=prelim, preview_record=record, batch_dir=Path("o")), config)
    assert not any(o.key == "strip_metadata" for o in options)
    notice = [o for o in options if not o.enabled and "©xyz" in o.label]
    assert notice and "取り除けません" in notice[0].label
