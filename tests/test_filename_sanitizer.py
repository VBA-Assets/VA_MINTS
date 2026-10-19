from mints_prep.detectors.filename_detector import check_filename
from mints_prep.processors.filename_sanitizer import needs_sanitization, sanitize_filename


def test_no_sanitization_needed_for_compliant_name(config):
    finding = check_filename("甲001 売買契約書原本.pdf", config.filename)
    assert needs_sanitization(finding) is False


def test_sanitization_needed_for_over_length_name(config):
    finding = check_filename("あ" * 97 + ".pdf", config.filename)  # 101 chars
    assert needs_sanitization(finding) is True


def test_sanitization_needed_for_forbidden_chars(config):
    finding = check_filename("甲001<証拠>.pdf", config.filename)
    assert needs_sanitization(finding) is True


def test_forbidden_chars_replaced_with_underscore(config):
    result = sanitize_filename("甲001<証拠>.pdf", config.filename)
    assert result == "甲001_証拠_.pdf"
    assert len(result) <= config.filename.max_length


def test_over_length_name_truncated_preserving_extension(config):
    name = "あ" * 97 + ".pdf"  # 101 chars
    result = sanitize_filename(name, config.filename)
    assert result == "あ" * 96 + ".pdf"
    assert len(result) == config.filename.max_length


def test_compliant_name_unchanged(config):
    name = "甲001 売買契約書原本.pdf"
    assert sanitize_filename(name, config.filename) == name


def test_both_forbidden_chars_and_over_length(config):
    name = "甲" + "<" * 5 + "あ" * 95 + ">.pdf"  # forbidden chars + over 100 chars
    result = sanitize_filename(name, config.filename)
    assert "<" not in result
    assert ">" not in result
    assert result.endswith(".pdf")
    assert len(result) <= config.filename.max_length


def test_custom_replacement_char(config):
    result = sanitize_filename("甲001<証拠>.pdf", config.filename, replacement="-")
    assert result == "甲001-証拠-.pdf"
