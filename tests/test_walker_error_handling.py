from pathlib import Path

from mints_prep.models import Classification
from mints_prep.scanner import walker as walker_module
from mints_prep.scanner.walker import scan_folder


def test_scan_folder_isolates_per_file_errors(tmp_path: Path, config, monkeypatch):
    """1ファイルの読み取りで想定外の例外が発生しても、スキャン全体は中断しない。"""
    ok_file = tmp_path / "ok.txt"
    ok_file.write_text("dummy")
    broken_file = tmp_path / "broken.txt"
    broken_file.write_text("dummy")

    original_scan_file = walker_module.scan_file

    def flaky_scan_file(path, cfg):
        if path.name == "broken.txt":
            raise RuntimeError("simulated read failure")
        return original_scan_file(path, cfg)

    monkeypatch.setattr(walker_module, "scan_file", flaky_scan_file)

    result = scan_folder(tmp_path, config, include_subfolders=False)

    assert len(result.records) == 2
    by_name = {r.path.name: r for r in result.records}
    broken_record = by_name["broken.txt"]
    assert broken_record.classification == Classification.D_UNSUPPORTED
    assert any("読み取りエラー" in note for note in broken_record.notes)
    # 正常なファイルは影響を受けない
    assert by_name["ok.txt"].path.name == "ok.txt"
