from pathlib import Path

from fixtures.generate_fixtures import make_pdf_with_text
from mints_prep.detectors.pii_detector import (
    detect_pii_in_filename,
    detect_pii_in_pdf_text,
    merge_pii_findings,
)
from mints_prep.models import PiiFinding


def test_filename_email_detected(config):
    finding = detect_pii_in_filename("報告書_taro.tanaka@example.com.pdf", config.pii_detection)
    assert finding.has_pii is True
    assert "メールアドレス" in finding.detected_categories


def test_filename_without_pii_not_flagged(config):
    finding = detect_pii_in_filename("甲001 契約書.pdf", config.pii_detection)
    assert finding.has_pii is False
    assert finding.detected_categories == ()


def test_pdf_text_my_number_detected(tmp_path: Path, config):
    p = make_pdf_with_text(tmp_path / "doc.pdf", ["マイナンバー: 123456789012"])
    finding = detect_pii_in_pdf_text(p, config.pii_detection)
    assert finding.has_pii is True
    assert "マイナンバー" in finding.detected_categories
    assert "1ページ目" in finding.details[0] or "1" in "".join(finding.details)


def test_pdf_text_phone_number_detected(tmp_path: Path, config):
    p = make_pdf_with_text(tmp_path / "doc.pdf", ["連絡先 090-1234-5678"])
    finding = detect_pii_in_pdf_text(p, config.pii_detection)
    assert "電話番号" in finding.detected_categories


def test_pdf_text_postal_address_detected(tmp_path: Path, config):
    p = make_pdf_with_text(tmp_path / "doc.pdf", ["住所 150-0001"])
    finding = detect_pii_in_pdf_text(p, config.pii_detection)
    assert "郵便番号・住所" in finding.detected_categories


def test_pdf_text_bank_account_detected(tmp_path: Path, config):
    p = make_pdf_with_text(tmp_path / "doc.pdf", ["口座番号 1234567"])
    finding = detect_pii_in_pdf_text(p, config.pii_detection)
    assert "銀行口座番号" in finding.detected_categories


def test_pdf_text_credit_card_luhn_valid_detected(tmp_path: Path, config):
    # 4111 1111 1111 1111 はテスト用の有効なLuhn番号（Visaのテスト用番号）
    p = make_pdf_with_text(tmp_path / "doc.pdf", ["カード番号 4111 1111 1111 1111"])
    finding = detect_pii_in_pdf_text(p, config.pii_detection)
    assert "クレジットカード番号" in finding.detected_categories


def test_pdf_text_credit_card_luhn_invalid_not_detected(tmp_path: Path, config):
    # 最後の桁を変えるとLuhnチェックが不成立になるため誤検出を抑止する
    p = make_pdf_with_text(tmp_path / "doc.pdf", ["カード番号 4111 1111 1111 1112"])
    finding = detect_pii_in_pdf_text(p, config.pii_detection)
    assert "クレジットカード番号" not in finding.detected_categories


def test_pdf_without_pii_not_flagged(tmp_path: Path, config):
    p = make_pdf_with_text(tmp_path / "doc.pdf", ["これは通常の契約書の本文です。"])
    finding = detect_pii_in_pdf_text(p, config.pii_detection)
    assert finding.has_pii is False


def test_non_pdf_path_returns_none_to_distinguish_failure(tmp_path: Path, config):
    # 解析自体に失敗した場合はNone（検出を実行できなかった）を返す。
    # 「検出なし（has_pii=False）」と区別しないと、解析失敗が無警告の
    # 「個人情報なし」に化けてしまうため（walker側で注記を出す）。
    p = tmp_path / "not_a_pdf.pdf"
    p.write_bytes(b"this is not a pdf")
    finding = detect_pii_in_pdf_text(p, config.pii_detection)
    assert finding is None


def test_pdf_text_phone_after_kanji_detected(tmp_path: Path, config):
    # 日本語文中では数字がラベル漢字に直接続く（「電話03-…」）のが主流。
    # 旧パターンの\bはPythonでは漢字を\w扱いするため、この形を系統的に
    # 見逃していた（境界を(?<![0-9])方式に変更して解消）。
    p = make_pdf_with_text(tmp_path / "doc.pdf", ["電話03-1234-5678"])
    finding = detect_pii_in_pdf_text(p, config.pii_detection)
    assert "電話番号" in finding.detected_categories


def test_pdf_text_fullwidth_phone_detected(tmp_path: Path, config):
    # 全角数字・全角ハイフン表記もNFKC正規化により検出できる。
    p = make_pdf_with_text(tmp_path / "doc.pdf", ["連絡先　０９０－１２３４－５６７８"])
    finding = detect_pii_in_pdf_text(p, config.pii_detection)
    assert "電話番号" in finding.detected_categories


def test_pdf_text_long_digit_run_not_flagged_as_my_number(tmp_path: Path, config):
    # 12桁を内包する、より長い数字列（22桁）はマイナンバーとして拾わない
    # （数字境界(?<![0-9])・(?![0-9])の確認）。
    p = make_pdf_with_text(tmp_path / "doc.pdf", ["整理番号 1234567890123456789012"])
    finding = detect_pii_in_pdf_text(p, config.pii_detection)
    assert "マイナンバー" not in finding.detected_categories


def test_merge_pii_findings_combines_categories():
    a = PiiFinding(has_pii=True, detected_categories=("メールアドレス",), details=("a",))
    b = PiiFinding(has_pii=True, detected_categories=("電話番号",), details=("b",))
    merged = merge_pii_findings(a, b)
    assert merged.has_pii is True
    assert set(merged.detected_categories) == {"メールアドレス", "電話番号"}
    assert merged.details == ("a", "b")


def test_merge_pii_findings_no_pii_when_all_empty():
    merged = merge_pii_findings(PiiFinding(has_pii=False), PiiFinding(has_pii=False))
    assert merged.has_pii is False
    assert merged.detected_categories == ()


def test_koseki_issue_number_not_flagged(tmp_path: Path, config):
    """戸籍謄本の発行番号（ハイフン連結の書類番号）を銀行口座番号・
    クレジットカード番号・マイナンバーと誤検出しない（実事例の回帰テスト）。"""
    p = make_pdf_with_text(
        tmp_path / "doc.pdf", ["発行番号 00394577-20260303-00000024"]
    )
    finding = detect_pii_in_pdf_text(p, config.pii_detection)
    assert finding.has_pii is False, f"誤検出: {finding.detected_categories}"


def test_bare_digit_run_not_flagged_as_bank_account(tmp_path: Path, config):
    """文脈語（口座・普通・当座）のない7〜8桁の数字列（受付番号等）は
    銀行口座番号として検出しない。"""
    p = make_pdf_with_text(tmp_path / "doc.pdf", ["受付番号 12345678"])
    finding = detect_pii_in_pdf_text(p, config.pii_detection)
    assert "銀行口座番号" not in finding.detected_categories


def test_bank_account_with_futsu_context_detected(tmp_path: Path, config):
    """「普通 1234567」（銀行の口座種別表記）は検出する。"""
    p = make_pdf_with_text(tmp_path / "doc.pdf", ["○○銀行△△支店 普通 1234567"])
    finding = detect_pii_in_pdf_text(p, config.pii_detection)
    assert "銀行口座番号" in finding.detected_categories
