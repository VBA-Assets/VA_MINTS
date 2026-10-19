from pathlib import Path

from mints_prep.detectors.size_detector import get_size_bytes


def test_get_size_bytes(tmp_path: Path):
    p = tmp_path / "sample.bin"
    p.write_bytes(b"0" * 12345)
    assert get_size_bytes(p) == 12345
