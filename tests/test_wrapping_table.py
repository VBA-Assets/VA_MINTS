import pytest

pytest.importorskip("tkinter")

import tkinter as tk  # noqa: E402

from mints_prep.ui.wrapping_table import ColumnSpec, WrappingTable  # noqa: E402

_COLUMNS = [
    ColumnSpec("a", "列A", 150),
    ColumnSpec("b", "列B", 60),
    ColumnSpec("c", "列C", 250),
]


@pytest.fixture(scope="module")
def root():
    # モジュール内でTk()を使い回す。テストごとにTk()を作成・破棄すると、
    # この環境ではまれにTclライブラリの読み込みが失敗することがあったため
    # （OneDrive同期等による一時的なファイルロックと推測）、生成回数を
    # 減らして安定させる。
    r = tk.Tk()
    yield r
    r.destroy()


@pytest.fixture
def table(root: tk.Tk):
    t = WrappingTable(root, _COLUMNS, tag_colors={"cls_a": "#e6f4ea"})
    t.pack(fill="both", expand=True)
    yield t
    t.destroy()


def test_long_text_wraps_to_taller_row(root: tk.Tk, table: WrappingTable):
    table.add_row(("short.pdf", "A", "短い"))
    long_text = "証拠番号処理の適用候補：甲001 / メタデータあり（除去を推奨）/ さらに長い説明文を追加してラップの発生を確認する"
    table.add_row(("long.pdf", "B", long_text))
    root.update()

    short_height = table._row_frames[0].winfo_height()
    long_height = table._row_frames[1].winfo_height()
    assert long_height > short_height


def test_header_and_row_columns_stay_aligned(root: tk.Tk, table: WrappingTable):
    table.add_row(("a.pdf", "A", "テスト"))
    root.update()
    root.update_idletasks()

    header_widths = [table._header.grid_bbox(i, 0)[2] for i in range(3)]
    row_widths = [table._row_frames[0].grid_bbox(i, 0)[2] for i in range(3)]
    assert header_widths == row_widths


def test_clear_removes_all_rows(table: WrappingTable):
    table.add_row(("a.pdf", "A", "1"))
    table.add_row(("b.pdf", "B", "2"))
    assert table.row_count == 2

    table.clear()

    assert table.row_count == 0
    assert table._row_frames == []


def test_get_row_returns_values_by_column_key(table: WrappingTable):
    table.add_row(("a.pdf", "A：適合済み", "処理不要"))

    row = table.get_row(0)

    assert row == {"a": "a.pdf", "b": "A：適合済み", "c": "処理不要"}


def test_row_background_matches_tag_color(root: tk.Tk, table: WrappingTable):
    table.add_row(("a.pdf", "A", "OK"), tag="cls_a")
    root.update()

    assert table._row_frames[0].cget("bg") == "#e6f4ea"


def test_checkbox_disabled_by_default_returns_empty_states(table: WrappingTable):
    table.add_row(("a.pdf", "A", "1"))
    assert table.get_checked_states() == []


@pytest.fixture
def checkable_table(root: tk.Tk):
    t = WrappingTable(root, _COLUMNS, with_checkbox=True)
    t.pack(fill="both", expand=True)
    yield t
    t.destroy()


def test_checkbox_defaults_to_checked(checkable_table: WrappingTable):
    checkable_table.add_row(("a.pdf", "A", "1"))
    checkable_table.add_row(("b.pdf", "B", "2"))

    assert checkable_table.get_checked_states() == [True, True]


def test_checkbox_can_start_unchecked(checkable_table: WrappingTable):
    checkable_table.add_row(("a.pdf", "A", "1"), checked=False)

    assert checkable_table.get_checked_states() == [False]


def test_checkbox_state_reflects_user_toggle(checkable_table: WrappingTable):
    checkable_table.add_row(("a.pdf", "A", "1"))
    checkable_table.add_row(("b.pdf", "B", "2"))

    checkable_table._check_vars[1].set(False)

    assert checkable_table.get_checked_states() == [True, False]


def test_non_checkable_row_shows_disabled_checkbox(root: tk.Tk, checkable_table: WrappingTable):
    # 書き出し対象になり得ない行（C・D分類等）は、チェックOFF・操作不可で表示する。
    checkable_table.add_row(("a.docx", "C", "要変換"), checked=False, checkable=False)
    root.update()

    checkbox = next(
        w for w in checkable_table._row_frames[0].winfo_children() if isinstance(w, tk.Checkbutton)
    )
    assert str(checkbox.cget("state")) == "disabled"
    assert checkable_table.get_checked_states() == [False]


def test_checkable_row_checkbox_stays_enabled(root: tk.Tk, checkable_table: WrappingTable):
    checkable_table.add_row(("a.pdf", "A", "OK"))
    root.update()

    checkbox = next(
        w for w in checkable_table._row_frames[0].winfo_children() if isinstance(w, tk.Checkbutton)
    )
    assert str(checkbox.cget("state")) == "normal"


def test_clear_resets_checked_states(checkable_table: WrappingTable):
    checkable_table.add_row(("a.pdf", "A", "1"))
    checkable_table.clear()

    assert checkable_table.get_checked_states() == []


def test_select_all_toggle_turns_all_rows_off_and_on(checkable_table: WrappingTable):
    checkable_table.add_row(("a.pdf", "A", "1"))
    checkable_table.add_row(("b.pdf", "B", "2"))

    checkable_table._select_all_var.set(False)
    checkable_table._on_select_all_toggle()
    assert checkable_table.get_checked_states() == [False, False]

    checkable_table._select_all_var.set(True)
    checkable_table._on_select_all_toggle()
    assert checkable_table.get_checked_states() == [True, True]


def test_select_all_does_not_affect_non_checkable_rows(checkable_table: WrappingTable):
    # 書き出し対象になり得ない行（C・D分類）は、全選択ONでもOFFのまま。
    checkable_table.add_row(("a.pdf", "A", "1"))
    checkable_table.add_row(("c.docx", "C", "要変換"), checked=False, checkable=False)

    checkable_table._select_all_var.set(True)
    checkable_table._on_select_all_toggle()

    assert checkable_table.get_checked_states() == [True, False]


def test_select_all_recovers_individually_unchecked_rows(checkable_table: WrappingTable):
    checkable_table.add_row(("a.pdf", "A", "1"))
    checkable_table.add_row(("b.pdf", "B", "2"))
    checkable_table._check_vars[0].set(False)  # 個別にOFFへ

    checkable_table._select_all_var.set(True)
    checkable_table._on_select_all_toggle()

    assert checkable_table.get_checked_states() == [True, True]


def test_clear_resets_select_all_to_on(checkable_table: WrappingTable):
    checkable_table.add_row(("a.pdf", "A", "1"))
    checkable_table._select_all_var.set(False)
    checkable_table._on_select_all_toggle()

    checkable_table.clear()

    assert checkable_table._select_all_var.get() is True


def test_checkbox_column_keeps_data_columns_aligned_with_header(
    root: tk.Tk, checkable_table: WrappingTable
):
    checkable_table.add_row(("a.pdf", "A", "テスト"))
    root.update()
    root.update_idletasks()

    # チェックボックス列（0）を除いた、データ列（1〜3）の幅がヘッダーと揃うこと。
    header_widths = [checkable_table._header.grid_bbox(i, 0)[2] for i in range(1, 4)]
    row_widths = [checkable_table._row_frames[0].grid_bbox(i, 0)[2] for i in range(1, 4)]
    assert header_widths == row_widths
