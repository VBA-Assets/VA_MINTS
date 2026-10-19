"""4.3の画面配線（証拠番号編集UIを持たないこと・列構成）のテスト。

証拠番号判定結果が「検出事項」列の文言に正しく畳み込まれること自体は
describe_expected_processing() の純粋関数テスト（test_ui_formatting.py）で
検証済みのため、ここでは重複させない。MainWindowの実インスタンス化（Tk()の
生成）を避け、モジュールレベルの列定義とクラス属性の検査にとどめることで、
この環境でまれに発生するTclライブラリの読み込み不安定さ（Tk()を同一プロセス内
で複数回生成することに起因すると推測される）の影響を受けないようにしている。
"""

from mints_prep.ui.app import RESULT_TABLE_COLUMNS, MainWindow


def test_result_table_has_no_separate_output_filename_or_evidence_columns():
    # ファイル名編集・自動採番機能は撤回済み。列は元の6列構成のみ。
    keys = tuple(col.key for col in RESULT_TABLE_COLUMNS)
    assert keys == (
        "filename",
        "classification",
        "format",
        "size",
        "processing",
        "notes",
    )


def test_main_window_has_no_editing_methods():
    # ダブルクリックでの編集機能・連番自動採番機能は撤回済み。
    assert not hasattr(MainWindow, "_apply_output_filename")
    assert not hasattr(MainWindow, "_apply_auto_numbering")
    assert not hasattr(MainWindow, "_begin_cell_edit")
