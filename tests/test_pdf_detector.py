from pathlib import Path

from fixtures.generate_fixtures import (
    make_a3_landscape_pdf,
    make_a3_pdf,
    make_a4_pdf,
    make_letter_size_pdf,
    make_password_pdf,
    make_permission_restricted_pdf,
)
from mints_prep.detectors.pdf_detector import detect_pdf


def test_a4_pdf_detected(tmp_path: Path, config):
    p = make_a4_pdf(tmp_path / "a4.pdf")
    finding = detect_pdf(p, config.pdf)
    assert finding.has_password is False
    assert finding.page_size_judgments == ("A4",)
    assert finding.page_orientations == ("縦",)
    assert finding.orientation_mismatch_pages == ()


def test_a3_landscape_matches_basic_form(tmp_path: Path, config):
    p = make_a3_landscape_pdf(tmp_path / "a3_landscape.pdf")
    finding = detect_pdf(p, config.pdf)
    assert finding.page_size_judgments == ("A3",)
    assert finding.page_orientations == ("横",)
    assert finding.orientation_mismatch_pages == ()


def test_a3_portrait_flagged_as_orientation_mismatch(tmp_path: Path, config):
    """A3は横置きが基本形（マニュアル別紙4）のため、縦置きは向き不一致として警告する。"""
    p = make_a3_pdf(tmp_path / "a3_portrait.pdf")
    finding = detect_pdf(p, config.pdf)
    assert finding.page_size_judgments == ("A3",)
    assert finding.page_orientations == ("縦",)
    assert finding.orientation_mismatch_pages == (1,)


def test_letter_size_pdf_flagged_non_compliant(tmp_path: Path, config):
    p = make_letter_size_pdf(tmp_path / "letter.pdf")
    finding = detect_pdf(p, config.pdf)
    assert finding.page_size_judgments == ("非対応",)


def test_password_protected_pdf_detected(tmp_path: Path, config):
    p = make_password_pdf(tmp_path / "secret.pdf")
    finding = detect_pdf(p, config.pdf)
    assert finding.has_password is True
    assert finding.has_permission_restriction is False
    assert finding.page_sizes_mm == ()


def test_permission_restricted_pdf_detected_separately_from_password(tmp_path: Path, config):
    """ユーザーパスワードなしで開けるが印刷・コピーが制限されたPDF（要件定義書4.6）。

    パスワードが必須で開けないファイル（has_password）とは別のフィールドで
    判定され、has_passwordはFalseのままとなることを確認する。
    """
    p = make_permission_restricted_pdf(tmp_path / "restricted.pdf")
    finding = detect_pdf(p, config.pdf)
    assert finding.has_password is False
    assert finding.has_permission_restriction is True
    # ユーザーパスワードなしで開けているため、寸法・向きの判定も通常どおり行える。
    assert finding.page_size_judgments == ("A4",)


def test_ordinary_password_pdf_not_flagged_as_permission_restricted(tmp_path: Path, config):
    """通常のA4/A3 PDF（暗号化なし）はhas_permission_restrictionもFalseのまま。"""
    p = make_a4_pdf(tmp_path / "a4.pdf")
    finding = detect_pdf(p, config.pdf)
    assert finding.has_permission_restriction is False


def test_encrypted_pdf_with_all_permissions_still_flagged(tmp_path: Path, config):
    """要件定義書4.6の定義（ユーザーパスワードなしで開けるが暗号化辞書が
    存在する場合に該当）の回帰テスト：全権限許可でも暗号化辞書があれば
    検出する（権限フラグで絞る旧実装はこの形態を見逃していた）。"""
    from fixtures.generate_fixtures import make_encrypted_pdf_all_permissions_allowed

    p = make_encrypted_pdf_all_permissions_allowed(tmp_path / "encrypted_allow_all.pdf")
    finding = detect_pdf(p, config.pdf)
    assert finding.has_password is False
    assert finding.has_permission_restriction is True


def test_rotated_portrait_a4_judged_as_landscape(tmp_path: Path, config):
    """縦置きMediaBox＋/Rotate 90（スキャナ・複合機の典型出力）は、閲覧時の
    見た目どおり「横」と判定される（回転属性を無視した誤判定の回帰テスト）。
    A4の横向きは基本形（A4=縦）からの逸脱として向き不一致になる。"""
    from fixtures.generate_fixtures import make_rotated_pdf

    p = make_rotated_pdf(tmp_path / "rotated.pdf", rotate=90)
    finding = detect_pdf(p, config.pdf)
    assert finding.page_size_judgments == ("A4",)
    assert finding.page_orientations == ("横",)
    assert finding.orientation_mismatch_pages == (1,)
