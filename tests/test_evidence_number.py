from mints_prep.naming.evidence_number import check_evidence_sequence, match_evidence_number


def test_simple_match(config):
    m = match_evidence_number("甲001 売買契約書原本.pdf", config.evidence_number)
    assert m is not None
    assert m.symbol == "甲"
    assert m.subletter == ""
    assert m.number == "001"
    assert m.branch_from is None
    assert m.title == "売買契約書原本"
    assert m.title_separator == " "
    assert m.evidence_number_label == "甲001"


def test_title_with_no_separator_at_all(config):
    # 区切り文字を挟まない表記も許容し、標目はそのまま抽出できる。
    m = match_evidence_number("甲001売買契約書.pdf", config.evidence_number)
    assert m is not None
    assert m.title == "売買契約書"
    assert m.title_separator == ""


def test_defendant_subletter_match(config):
    # 準備の手引29頁の原文どおり、正式な表記は半角英字（例：「乙A001 領収書」）
    m = match_evidence_number("乙A001 陳述書.pdf", config.evidence_number)
    assert m is not None
    assert m.symbol == "乙"
    assert m.subletter == "A"
    assert m.number == "001"


def test_defendant_subletter_match_accepts_fullwidth_too(config):
    # 正式表記は半角だが、全角で入力されたファイルも証拠番号処理の対象外に
    # 誤判定しないよう、判定自体は全角も寛容に受理する
    m = match_evidence_number("乙Ａ001 陳述書.pdf", config.evidence_number)
    assert m is not None
    assert m.subletter == "Ａ"


def test_title_strips_leading_separator_when_no_branch_follows(config):
    # "_"の直後が数字でないため枝番としては成立せず証拠番号部分に含まれないが、
    # 区切り文字として使われた"_"自体は標目に残さず、title_separatorへ分離する。
    m = match_evidence_number("甲001_売買契約書.pdf", config.evidence_number)
    assert m is not None
    assert m.branch_from is None
    assert m.title == "売買契約書"
    assert m.title_separator == "_"


def test_title_strips_leading_separator_after_branch_match(config):
    # "_1"は枝番として成立するが、その直後にさらに続く"_"（標目との区切りの
    # つもりで打たれたもの）は枝番に取り込まれず標目側に残ってしまうため、
    # これも取り除いてtitle_separatorへ分離する（枝番結合時のファイル名生成で
    # 利用者の元の区切り文字を再現するために使う）。
    m = match_evidence_number("甲001_1_売買契約書A.pdf", config.evidence_number)
    assert m is not None
    assert m.branch_from == "1"
    assert m.title == "売買契約書A"
    assert m.title_separator == "_"


def test_branch_range_hyphen(config):
    m = match_evidence_number("甲001-1~20 領収書.pdf", config.evidence_number)
    assert m is not None
    assert m.branch_from == "1"
    assert m.branch_to == "20"
    assert m.evidence_number_label == "甲001-1~20"


def test_branch_range_underscore_fullwidth_tilde(config):
    # FAQ表記「甲001_1〜3」（アンダースコア＋全角チルダ）も受理する
    m = match_evidence_number("甲001_1〜3.pdf", config.evidence_number)
    assert m is not None
    assert m.branch_from == "1"
    assert m.branch_to == "3"


def test_single_branch_no_range(config):
    m = match_evidence_number("甲001-2.pdf", config.evidence_number)
    assert m is not None
    assert m.branch_from == "2"
    assert m.branch_to is None
    assert m.evidence_number_label == "甲001-2"


def test_no_match_for_non_evidence_filename(config):
    m = match_evidence_number("準備書面（案）.docx", config.evidence_number)
    assert m is None


def test_no_match_when_number_wrong_digit_count(config):
    # 2桁は半角3桁の要件に合致しない
    m = match_evidence_number("甲01 契約書.pdf", config.evidence_number)
    assert m is None


def test_sequence_check_detects_gap(config):
    filenames = ["甲001 契約書.pdf", "甲003 領収書.pdf"]
    findings = check_evidence_sequence(filenames, config.evidence_number)
    assert len(findings) == 1
    assert findings[0].symbol_key == "甲"
    assert findings[0].missing_numbers == (2,)
    assert findings[0].duplicate_numbers == ()


def test_sequence_check_no_gap_when_consecutive(config):
    filenames = ["甲001 契約書.pdf", "甲002 領収書.pdf", "甲003 陳述書.pdf"]
    findings = check_evidence_sequence(filenames, config.evidence_number)
    assert findings == ()


def test_sequence_check_branch_files_count_as_one_number(config):
    # 甲001-1〜3は親番号（甲001）単位で1つと数えるため、甲002が欠番となる
    filenames = ["甲001-1 領収書1.pdf", "甲001-2 領収書2.pdf", "甲001-3 領収書3.pdf", "甲003 陳述書.pdf"]
    findings = check_evidence_sequence(filenames, config.evidence_number)
    assert len(findings) == 1
    assert findings[0].missing_numbers == (2,)
    assert findings[0].duplicate_numbers == ()


def test_sequence_check_detects_duplicate_same_number_no_branch(config):
    filenames = ["甲001 契約書.pdf", "甲001 別の契約書.pdf"]
    findings = check_evidence_sequence(filenames, config.evidence_number)
    assert len(findings) == 1
    assert findings[0].duplicate_numbers == (1,)
    assert findings[0].missing_numbers == ()


def test_sequence_check_branch_group_not_flagged_as_duplicate(config):
    # 枝番違いは正当な結合対象であり、重複ではない
    filenames = ["甲001-1 領収書1.pdf", "甲001-2 領収書2.pdf"]
    findings = check_evidence_sequence(filenames, config.evidence_number)
    assert findings == ()


def test_sequence_check_independent_series_per_symbol(config):
    # 符号（甲・乙）ごとに独立した連番系列として扱うため、甲002の欠番が乙側に影響しない
    filenames = ["甲001 契約書.pdf", "甲003 領収書.pdf", "乙A001 陳述書.pdf", "乙A002 陳述書2.pdf"]
    findings = check_evidence_sequence(filenames, config.evidence_number)
    assert len(findings) == 1
    assert findings[0].symbol_key == "甲"


def test_sequence_check_ignores_non_matching_filenames(config):
    filenames = ["甲001 契約書.pdf", "準備書面（案）.docx"]
    findings = check_evidence_sequence(filenames, config.evidence_number)
    assert findings == ()


def test_fullwidth_tilde_range_parsed(config):
    """Windows IMEの「～」（U+FF5E全角チルダ）による枝番範囲表記
    （FAQ記載の「甲001_1～3」）を範囲として解析できること。"""
    m = match_evidence_number("甲001_1～3 契約書.pdf", config.evidence_number)
    assert m is not None
    assert m.branch_from == "1"
    assert m.branch_to == "3"
    assert m.title == "契約書"


def test_date_like_filename_not_matched(config):
    """規定桁数（3桁）を超える数字列（日付入りファイル名）の先頭3桁を
    証拠番号と誤解釈しない（「甲20240401」→「甲202」誤解析の回帰テスト）。"""
    assert match_evidence_number("甲20240401_領収書.pdf", config.evidence_number) is None
    assert match_evidence_number("甲0012 契約書.pdf", config.evidence_number) is None


def test_sequence_duplicate_detected_across_fullwidth_digits(config):
    """全角数字表記のファイルとの実質重複（甲001と甲００１）を見逃さない
    （重複判定キーの数値正規化の回帰テスト）。"""
    findings = check_evidence_sequence(
        ["甲001 a.pdf", "甲００１ b.pdf"], config.evidence_number
    )
    assert len(findings) == 1
    assert findings[0].duplicate_numbers == (1,)


def test_sequence_duplicate_detected_when_branch_range_overlaps(config):
    """枝番範囲（甲001-1~3）と範囲内の単独枝番（甲001-2）の併存を重複として
    検出する（範囲を展開して照合する回帰テスト）。"""
    findings = check_evidence_sequence(
        ["甲001-1~3 領収書.pdf", "甲001-2 領収書.pdf"], config.evidence_number
    )
    assert len(findings) == 1
    assert findings[0].duplicate_numbers == (1,)


def test_sequence_duplicate_detected_across_fullwidth_subletter(config):
    """全角添字表記との実質重複（乙A001と乙Ａ001）を見逃さない
    （系列キーのNFKC正規化の回帰テスト。全角数字の正規化と同じ趣旨）。"""
    findings = check_evidence_sequence(
        ["乙A001 a.pdf", "乙Ａ001 b.pdf"], config.evidence_number
    )
    assert len(findings) == 1
    assert findings[0].symbol_key == "乙A"
    assert findings[0].duplicate_numbers == (1,)


def test_fullwidth_branch_separator_parsed(config):
    """全角の枝番区切り（甲００１－１）も枝番として解析する（枝番なし・
    標目「－１」と誤解析して「甲001-1」との実質重複を見逃す不具合の回帰テスト）。"""
    m = match_evidence_number("甲００１－１ 領収書.pdf", config.evidence_number)
    assert m is not None
    assert m.branch_from == "１"

    findings = check_evidence_sequence(
        ["甲001-1 a.pdf", "甲００１－１ b.pdf"], config.evidence_number
    )
    assert len(findings) == 1
    assert findings[0].duplicate_numbers == (1,)


def test_sequence_parent_and_branch_coexistence_flagged_as_duplicate(config):
    """親番号そのもの（甲001）と枝番付き（甲001-1）の併存は、同一号証の
    提出単位が衝突しているため重複として検出する。"""
    findings = check_evidence_sequence(
        ["甲001 契約書.pdf", "甲001-1 契約書別紙.pdf", "甲001-2 契約書別紙2.pdf"],
        config.evidence_number,
    )
    assert len(findings) == 1
    assert findings[0].duplicate_numbers == (1,)


def test_sequence_findings_ordered_by_symbol_convention(config):
    """符号系列の表示順は慣用の甲・乙・丙順（config.symbolsの並び）。
    Unicodeコードポイント順（丙<乙<甲）にならないこと。"""
    findings = check_evidence_sequence(
        ["丙001 a.pdf", "丙003 b.pdf", "乙001 c.pdf", "乙003 d.pdf", "甲001 e.pdf", "甲003 f.pdf"],
        config.evidence_number,
    )
    assert [f.symbol_key for f in findings] == ["甲", "乙", "丙"]
