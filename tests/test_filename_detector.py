from mints_prep.detectors.filename_detector import check_filename


def test_ok_filename(config):
    finding = check_filename("甲001 売買契約書原本.pdf", config.filename)
    assert not finding.exceeds_max_length
    assert not finding.exceeds_warning_length
    assert finding.forbidden_chars_found == ()


def test_exceeds_max_length(config):
    name = "あ" * 97 + ".pdf"  # 101 chars
    finding = check_filename(name, config.filename)
    assert finding.length == 101
    assert finding.exceeds_max_length
    assert finding.exceeds_warning_length


def test_exceeds_warning_but_not_max_length(config):
    name = "あ" * 55 + ".pdf"  # 59 chars: warning(50) < x <= max(100)
    finding = check_filename(name, config.filename)
    assert not finding.exceeds_max_length
    assert finding.exceeds_warning_length


def test_forbidden_chars_detected(config):
    finding = check_filename('甲001<証拠>.pdf', config.filename)
    assert set(finding.forbidden_chars_found) == {"<", ">"}


def test_non_jis_x0213_char_detected(config):
    # 絵文字はJIS X 0213（shift_jis_2004）で符号化できない
    finding = check_filename("証拠\U0001F600.pdf", config.filename)
    assert "\U0001F600" in finding.non_jis_x0213_chars


def test_windows_fullwidth_tilde_not_flagged_as_non_jis(config):
    """Windows IMEの「～」（U+FF5E）はcp932互換文字として正字（U+301C）へ
    写像してから判定するため、外字警告を出さない（常時警告ノイズの回帰テスト）。"""
    finding = check_filename("決算書～控え.pdf", config.filename)
    assert finding.non_jis_x0213_chars == ()
