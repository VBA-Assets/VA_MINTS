import dataclasses
from pathlib import Path

from fixtures.generate_fixtures import (
    make_a4_pdf,
    make_a4_pdf_no_metadata,
    make_docx_with_metadata,
    make_letter_size_pdf,
    make_long_filename_pdf,
    make_password_pdf,
    make_pdf_with_text,
    make_permission_restricted_pdf,
    make_unsupported_file,
)
from mints_prep.models import Classification
from mints_prep.scanner.walker import scan_folder


def test_scan_folder_classifies_all_files(tmp_path: Path, config):
    make_a4_pdf_no_metadata(tmp_path / "甲001 売買契約書原本.pdf")  # A: 適合済み
    make_letter_size_pdf(tmp_path / "乙001 レターサイズ書証.pdf")  # B: 要修正（用紙寸法）
    make_password_pdf(tmp_path / "甲002 パスワード付き証拠.pdf")  # B: 要修正（パスワード）
    make_docx_with_metadata(tmp_path / "準備書面案.docx")  # C: 要変換
    make_unsupported_file(tmp_path / "圧縮ファイル.zip")  # D: 処理対象外

    result = scan_folder(tmp_path, config, include_subfolders=False)

    assert len(result.records) == 5
    by_name = {r.path.name: r for r in result.records}

    assert by_name["甲001 売買契約書原本.pdf"].classification == Classification.A_COMPLIANT
    assert by_name["乙001 レターサイズ書証.pdf"].classification == Classification.B_NEEDS_FIX
    assert by_name["甲002 パスワード付き証拠.pdf"].classification == Classification.B_NEEDS_FIX
    assert by_name["準備書面案.docx"].classification == Classification.C_NEEDS_CONVERSION
    assert by_name["圧縮ファイル.zip"].classification == Classification.D_UNSUPPORTED


def test_scan_folder_does_not_modify_files(tmp_path: Path, config):
    p = make_a4_pdf(tmp_path / "test.pdf")
    before = p.stat().st_mtime_ns
    before_bytes = p.read_bytes()

    scan_folder(tmp_path, config, include_subfolders=False)

    assert p.stat().st_mtime_ns == before
    assert p.read_bytes() == before_bytes


def test_scan_folder_flags_over_100_char_filename(tmp_path: Path, config):
    p = make_long_filename_pdf(tmp_path)
    result = scan_folder(tmp_path, config, include_subfolders=False)
    record = next(r for r in result.records if r.path == p)
    assert record.filename_finding.exceeds_max_length
    assert record.classification == Classification.B_NEEDS_FIX


def test_scan_folder_flags_single_file_exceeding_200mb(tmp_path: Path, config):
    # 実際に200MBのファイルを作るのは重いため、上限を小さくしたconfigで検証する。
    tiny_upload = dataclasses.replace(config.upload, max_total_mb=0.001)  # 約1KB上限
    tiny_config = dataclasses.replace(config, upload=tiny_upload)

    p = make_a4_pdf(tmp_path / "甲001 巨大ファイル.pdf")
    assert p.stat().st_size > tiny_upload.max_total_bytes  # 前提の確認

    result = scan_folder(tmp_path, tiny_config, include_subfolders=False)

    record = result.records[0]
    assert record.exceeds_single_file_limit is True
    assert record.classification == Classification.B_NEEDS_FIX
    # 警告文言の上限値はハードコードせずconfigの値を反映する（設計原則5）。
    assert any("0.001MBを超えています" in note for note in record.notes)


def test_scan_folder_does_not_flag_file_within_limit(tmp_path: Path, config):
    p = make_a4_pdf_no_metadata(tmp_path / "甲001 通常サイズ.pdf")
    assert p.stat().st_size < config.upload.max_total_bytes

    result = scan_folder(tmp_path, config, include_subfolders=False)

    record = result.records[0]
    assert record.exceeds_single_file_limit is False
    assert record.classification == Classification.A_COMPLIANT


def test_scan_folder_flags_permission_restricted_pdf(tmp_path: Path, config):
    make_permission_restricted_pdf(tmp_path / "登記情報.pdf")
    result = scan_folder(tmp_path, config, include_subfolders=False)
    record = result.records[0]
    assert record.pdf_finding.has_permission_restriction is True
    assert record.pdf_finding.has_password is False
    assert record.classification == Classification.B_NEEDS_FIX


def test_scan_folder_flags_pii_in_pdf_text(tmp_path: Path, config):
    make_pdf_with_text(tmp_path / "甲001 陳述書.pdf", ["連絡先 090-1234-5678"])
    result = scan_folder(tmp_path, config, include_subfolders=False)
    record = result.records[0]
    assert record.pii_finding.has_pii is True
    assert "電話番号" in record.pii_finding.detected_categories
    assert record.classification == Classification.B_NEEDS_FIX


def test_scan_folder_flags_pii_in_filename(tmp_path: Path, config):
    make_a4_pdf_no_metadata(tmp_path / "報告書_taro.tanaka@example.com.pdf")
    result = scan_folder(tmp_path, config, include_subfolders=False)
    record = result.records[0]
    assert record.pii_finding.has_pii is True
    assert "メールアドレス" in record.pii_finding.detected_categories


def test_scan_folder_no_pii_stays_compliant(tmp_path: Path, config):
    make_a4_pdf_no_metadata(tmp_path / "甲001 通常書証.pdf")
    result = scan_folder(tmp_path, config, include_subfolders=False)
    record = result.records[0]
    assert record.pii_finding.has_pii is False
    assert record.classification == Classification.A_COMPLIANT


def test_scan_folder_subfolder_option(tmp_path: Path, config):
    sub = tmp_path / "サブフォルダ"
    sub.mkdir()
    make_a4_pdf(tmp_path / "root.pdf")
    make_a4_pdf(sub / "nested.pdf")

    shallow = scan_folder(tmp_path, config, include_subfolders=False)
    deep = scan_folder(tmp_path, config, include_subfolders=True)

    assert len(shallow.records) == 1
    assert len(deep.records) == 2


def test_scan_folder_skips_system_and_temp_files(tmp_path: Path, config):
    """OS・アプリの一時ファイル（Thumbs.db・desktop.ini・Officeロック
    ファイル〔~$~〕・.tmp）はスキャン対象にしない（特に~$ファイルは
    拡張子フォールバックでC「要変換」と誤案内される回帰テスト）。"""
    make_a4_pdf(tmp_path / "甲001 通常ファイル.pdf")
    (tmp_path / "Thumbs.db").write_bytes(b"\xd0\xcf\x11\xe0dummy")
    (tmp_path / "desktop.ini").write_text("[.ShellClassInfo]", encoding="utf-8")
    (tmp_path / "~$報告書.docx").write_bytes(b"\x00lockfile")
    (tmp_path / "作業中.tmp").write_bytes(b"temp")

    result = scan_folder(tmp_path, config, include_subfolders=False)

    names = [r.path.name for r in result.records]
    assert names == ["甲001 通常ファイル.pdf"]
