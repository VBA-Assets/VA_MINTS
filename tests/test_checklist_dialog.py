import pytest

pytest.importorskip("tkinter")

import tkinter as tk  # noqa: E402

from mints_prep.ui.checklist_dialog import ChecklistDialog, ChecklistItem  # noqa: E402


@pytest.fixture(scope="module")
def root():
    r = tk.Tk()
    r.withdraw()
    yield r
    r.destroy()


def _items() -> list[ChecklistItem]:
    return [
        ChecklistItem(key="g1", before_text="a.pdf、b.pdf", after_text="ab.pdf", default_checked=True),
        ChecklistItem(key="g2", before_text="c.pdf、d.pdf", after_text="cd.pdf", default_checked=False),
    ]


def test_checkboxes_start_at_default_checked_state(root: tk.Tk):
    dialog = ChecklistDialog(root, "タイトル", "説明文", _items())
    root.update()

    assert dialog._vars["g1"].get() is True
    assert dialog._vars["g2"].get() is False

    dialog.destroy()


def test_execute_returns_current_checkbox_states(root: tk.Tk):
    dialog = ChecklistDialog(root, "タイトル", "説明文", _items())
    root.update()
    dialog._vars["g2"].set(True)  # ユーザーが個別にチェックを入れる操作を模す

    dialog._on_execute()

    assert dialog.result == {"g1": True, "g2": True}
    assert not dialog.winfo_exists()


def test_cancel_returns_none(root: tk.Tk):
    dialog = ChecklistDialog(root, "タイトル", "説明文", _items())
    root.update()

    dialog._on_cancel()

    assert dialog.result is None
    assert not dialog.winfo_exists()


def test_count_label_shows_item_count(root: tk.Tk):
    dialog = ChecklistDialog(root, "タイトル", "説明文", _items())
    root.update()

    assert dialog.count_label.cget("text") == "対象：2 件"

    dialog.destroy()


def test_empty_items_shows_zero_count(root: tk.Tk):
    dialog = ChecklistDialog(root, "タイトル", "説明文", [])
    root.update()

    assert dialog.count_label.cget("text") == "対象：0 件"
    assert dialog._vars == {}

    dialog.destroy()
