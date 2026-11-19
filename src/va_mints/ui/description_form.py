"""証拠説明書の画面（要件定義書 4.17）：右のペインの入力欄の組み立てと、欄を変えたときの保存。

ui/app.py の MainWindow にまぜて（多重継承で）使う。状態（self._workspace・self._options_frame など）は
MainWindow が持つ。保存の後の PDF の作り直しと描き直し・作成・削除・プレビューのクリックは
ui/description_sync.py。

- リボンの［作成］（「証拠説明書」の組）を押すと、証拠説明書の PDF とデータを作る。作った後は、右のペインの、
  ふだんの項目（処理の項目など）の下に、いつも証拠説明書の入力欄を出す（隠す機能は設けない）。左の一覧で
  選んだ証拠の行（枝番ごと）を出す。中央のペインは、ふだんどおり選んだ証拠のプレビュー（証拠を見ながら
  入力できるように）。いずれも 2026-11-18 班長指示。
- 左の一覧で証拠説明書の PDF を選ぶと、右のペインに、頭書の欄と、全部の行の入力欄を出す。
- 入力は、変えるたびに（少し待ってから）保存する（証拠のファイルを変えないため、確認は要らない）。標目・
  原本写しの別は、1行のファイルではファイル名の変更（下書き）になる。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from va_mints import APP_NAME, errors
from va_mints.processors.evidence_description import CATEGORIES, CATEGORY_LABELS, HEADER_FIELDS
from va_mints.ui import theme
from va_mints.ui.tooltip import Tooltip

# 欄の高さ（行の数）
_TEXT_HEIGHTS = {"author": 2, "purpose": 5, "remarks": 2}
_FIELD_LABELS = {
    "title": "標目",
    "category": "原本・写しの別",
    "date": "作成年月日",
    "author": "作成者",
    "purpose": "立証趣旨",
    "remarks": "備考",
}
_PARTY_CHOICES = ("原告", "被告")
_HEADER_HINTS = {
    "court": "例：東京地方裁判所",
    "case_number": "例：令和8年（ワ）第123号",
    "number": "例：2（「証拠説明書（2）」になります。PDFの名前も変わります）",
    "date": "例：令和8年11月18日",
    "name": "例：原告訴訟代理人弁護士　甲野太郎",
}
# 左の一覧で、このツールで作った証拠説明書の PDF を選んだときの、右のペインの文。
DESCRIPTION_FILE_NOTE = (
    "これは、このツールで作った証拠説明書のPDFです（中央のプレビューで見た目を確かめられます）。"
    "下の頭書と各行を直すと、少し待ってから、このPDFを作り直します。証拠を見ながら1行ずつ直すときは、"
    "左の一覧で証拠を選びます（ふだんの項目の下に、その証拠の入力欄があります）。ファイル名の変更（標目の直しを含む）は、"
    "［ファイルを更新］を押すまで下書きです。mintsに出すのは、［ファイルを更新］の後のこのPDFにしてください。"
    "このファイルは、ほかの更新（スタンプ・仕分けなど）の対象になりません。"
)


class DescriptionFormMixin:
    # ------------------------------------------------------------------ 右のペイン
    def _append_description_form(self) -> None:
        """右のペインの、ふだんの項目の下に、証拠説明書の入力欄を足す。"""
        ttk.Separator(self._options_frame, orient="horizontal").pack(side=tk.TOP, fill=tk.X, pady=(10, 6))
        self._build_description_form()

    def _build_description_form(self) -> None:
        """選んだ証拠の行（枝番ごと）の入力欄。説明の文・全体の行の数・1行のファイルの号証の見出しは出さない
        （2026-11-18 班長指示）。"""
        ws, record = self._workspace, self._current
        self._wrap_label("証拠説明書", font=("", 10, "bold")).pack(side=tk.TOP, anchor="w", pady=(0, 2))
        mine = [r for r in ws.description_rows() if record is not None and r.record.path == record.path]
        if record is None:
            self._wrap_label("左の一覧でファイルを選ぶと、その証拠の行を入力できます。").pack(side=tk.TOP, anchor="w")
        elif not mine:
            self._wrap_label(
                "このファイルは、名前の先頭に証拠番号（例：甲001）がないため、証拠説明書の行に"
                "なりません（名前は、左の一覧でダブルクリック又はF2キーで変えられます）。"
            ).pack(side=tk.TOP, anchor="w")
        for row in mine:
            # 枝番ごとの行は、どの枝番かを分けるために号証の見出しを出す。
            self._build_row_fields(row, heading=row.count > 1)
        self._bind_wheel(self._options_frame)

    def _description_summary(self) -> None:
        rows = self._workspace.description_rows()
        unchosen = sum(1 for r in rows if not r.category)
        summary = f"全体：{len(rows)} 行"
        if unchosen:
            summary += f"（原本・写しの別を選んでいない行：{unchosen} 行）"
        self._wrap_label(summary, foreground=theme.NOTE).pack(side=tk.TOP, anchor="w", pady=(0, 4))

    def _build_description_file_pane(self) -> None:
        """作った証拠説明書の PDF を選んだときの右のペイン：頭書の欄と、全部の行の入力欄（2026-11-18 班長指示）。"""
        ws = self._workspace
        frame = self._options_frame
        self._desc_row_anchors = {}
        # 番号を変えて PDF の名前が変わったときに、description_sync.py が名前を替える。
        self._desc_title_label = self._wrap_label(self._current.path.name, font=("", 10, "bold"))
        self._desc_title_label.pack(side=tk.TOP, anchor="w", pady=(0, 2))
        self._wrap_label(DESCRIPTION_FILE_NOTE, foreground=theme.NOTE).pack(side=tk.TOP, anchor="w", pady=(0, 6))
        if ws.description_error:
            self._wrap_label(
                f"証拠説明書のデータを読めませんでした（{ws.description_error}）。データを上書きしないよう、"
                "ここでは保存しません。"
            ).pack(side=tk.TOP, anchor="w")
            return

        ttk.Label(frame, text="頭書", font=("", 10, "bold")).pack(side=tk.TOP, anchor="w", pady=(0, 2))
        box = ttk.Frame(frame)
        box.pack(side=tk.TOP, fill=tk.X, pady=(0, 4))
        box.columnconfigure(1, weight=1)
        header = ws.description.header
        for row_index, (name, label) in enumerate(HEADER_FIELDS):
            ttk.Label(box, text=label).grid(row=row_index, column=0, sticky="w", padx=(0, 8), pady=1)
            var = tk.StringVar(value=getattr(header, name))
            if name == "party":
                # 一覧から選んだときも、var が変わるので、下の trace で保存する。
                widget = ttk.Combobox(box, textvariable=var, values=_PARTY_CHOICES)
                Tooltip(widget, "空のときは、証拠番号の符号から決めます"
                                f"（いまは「{ws.description_header().party or '空'}」）。")
            else:
                widget = ttk.Entry(box, textvariable=var)
                Tooltip(widget, _HEADER_HINTS[name])
            widget.grid(row=row_index, column=1, sticky="ew", pady=1)
            self._keep_var(widget, var)
            var.trace_add("write", lambda *_a, n=name, v=var: self._queue_description_input(
                f"header:{n}", lambda: self._save_header(n, v.get())))
        ttk.Separator(frame, orient="horizontal").pack(side=tk.TOP, fill=tk.X, pady=6)
        self._description_summary()
        rows = ws.description_rows()
        if not rows:
            self._wrap_label(
                "証拠番号（例：甲001）が先頭にあるファイルがないため、行はありません。"
            ).pack(side=tk.TOP, anchor="w")
        for row in rows:
            self._build_row_fields(row)
        self._bind_wheel(frame)

    def _build_row_fields(self, row, heading: bool = True) -> None:
        """1行分の入力欄。heading：号証の見出しを出すか（全部の行を並べたペインでは出し、プレビューの行を
        クリックしたときに、その見出しまで送る）。"""
        frame = self._options_frame
        if heading:
            title = ttk.Label(frame, text=row.label, font=("", 10, "bold"))
            title.pack(side=tk.TOP, anchor="w", pady=(6, 2))
            self._desc_row_anchors[row.label] = title
        box = ttk.Frame(frame)
        box.pack(side=tk.TOP, fill=tk.X)
        box.columnconfigure(1, weight=1)
        r = 0

        def label(text: str, row_index: int) -> None:
            ttk.Label(box, text=text).grid(row=row_index, column=0, sticky="nw", padx=(0, 8), pady=(3, 1))

        # 標目：ほかの欄と同じく、字を入れ終えて少し待つと確定する（2026-11-18）。入れている間は右のペインを
        # 組み直さない（Enter・欄を離れたときに組み直す）。1行のファイルでは、ファイル名も変わる。
        label(_FIELD_LABELS["title"], r)
        committed = {"value": row.title}  # 最後に確定した標目（同じものを二度確定しないため）
        key = f"{row.label}:title"
        if row.data.span:
            # 標目をまたがせる行は、長い標目（文献名など）を入れるので、欄を立証趣旨と同じ高さにする
            # （2026-11-18 班長指示）。標目はファイル名にもなるので、改行は入れない（貼り付けた改行は外す）。
            entry = self._text_field(box, row.title, _TEXT_HEIGHTS["purpose"])

            def get(w=entry) -> str:
                return w.get("1.0", "end-1c").replace("\r", "").replace("\n", "")

            def put(value: str, w=entry) -> None:
                w.delete("1.0", "end")
                w.insert("1.0", value)

            def commit_now(_e=None) -> str:
                self._commit_title(row, get, put, committed)
                return "break"  # 改行を入れない

            entry.bind("<Return>", commit_now)
            entry.bind("<KP_Enter>", commit_now)
            entry.bind("<FocusOut>", lambda _e: self._commit_title(row, get, put, committed))
            self._on_text_change(entry, key, lambda: self._commit_title(row, get, put, committed, quiet=True))
        else:
            title_var = tk.StringVar(value=row.title)
            entry = ttk.Entry(box, textvariable=title_var)
            get, put = title_var.get, title_var.set
            entry.bind("<Return>", lambda _e: self._commit_title(row, get, put, committed))
            entry.bind("<FocusOut>", lambda _e: self._commit_title(row, get, put, committed))
            self._keep_var(entry, title_var)
            title_var.trace_add("write", lambda *_a: self._queue_description_input(
                key, lambda: self._commit_title(row, get, put, committed, quiet=True)))
        entry.grid(row=r, column=1, sticky="ew", pady=1)
        Tooltip(entry,
            "標目を直すと、ファイル名の標目も変わります（更新すると、ファイル名が変わります）。"
            if row.linked else
            "この枝番の行の標目です。ファイル名は変わりません（既定は、ファイル名の標目です）。")
        r += 1

        # 原本・写しの別（準備の手引27頁の4つの類型）
        label(_FIELD_LABELS["category"], r)
        names = [CATEGORY_LABELS[c] for c in CATEGORIES]
        cat_var = tk.StringVar(value=CATEGORY_LABELS[row.category])
        combo = ttk.Combobox(box, textvariable=cat_var, values=names, state="readonly")
        combo.grid(row=r, column=1, sticky="ew", pady=1)
        self._keep_var(combo, cat_var)
        combo.bind("<<ComboboxSelected>>", lambda _e: self._commit_category(
            row, CATEGORIES[names.index(cat_var.get())]))
        Tooltip(combo,
            "原本：標目の2行目に「原本」（ファイル名の末尾にも付けます）。紙を電子化：備考に「紙を電子化」。"
            "写し：標目の2行目に「写し」（ファイル名の末尾にも）。もともとデータ：何も書きません"
            "（準備の手引27頁）。")
        r += 1

        # 作成年月日（1行）・作成者・立証趣旨・備考（複数行）
        for name in ("date", "author", "purpose", "remarks"):
            label(_FIELD_LABELS[name], r)
            value = getattr(row.data, name)
            field_key = f"{row.label}:{name}"
            if name == "date":
                var = tk.StringVar(value=value)
                widget = ttk.Entry(box, textvariable=var)
                self._keep_var(widget, var)
                var.trace_add("write", lambda *_a, n=name, k=field_key, v=var: self._queue_description_input(
                    k, lambda: self._save_row_field(row, n, v.get())))
                Tooltip(widget, "もとの資料の作成年月日（例：R5.10.1）。電子化した日ではありません（準備の手引27頁）。")
            else:
                widget = self._text_field(box, value, _TEXT_HEIGHTS[name])
                self._on_text_change(widget, field_key, lambda n=name, w=widget: self._save_row_field(
                    row, n, w.get("1.0", "end-1c")))
                if name == "author":
                    Tooltip(widget, "もとの資料の作成者。電子化した人ではありません（準備の手引27頁）。")
            widget.grid(row=r, column=1, sticky="ew", pady=1)
            r += 1

        span_var = tk.BooleanVar(value=row.data.span)
        cb = self._checkbutton(
            "標目を、作成年月日・作成者の欄までまたがせる（作成年月日と作成者がともに空の行だけ）",
            span_var, parent=box, indent=0,
        )

        def toggle_span() -> None:
            self._save_row_field(row, "span", span_var.get())
            self._after_description_change()  # 標目の欄の高さを変えるため、組み直す

        cb.configure(command=toggle_span)
        cb.grid(row=r, column=0, columnspan=2, sticky="w", pady=(2, 0))
        self._keep_var(cb, span_var)

    @staticmethod
    def _keep_var(widget, var) -> None:
        """欄の変数を、その欄の部品に持たせる（欄がある間は、変数も確かに残るように）。"""
        widget._var = var

    def _text_field(self, parent, value: str, height: int) -> tk.Text:
        """複数行の欄（ttk に複数行の欄がないため、classic の tk.Text。対象フォルダの欄と同じ見た目）。"""
        text = tk.Text(
            parent, height=height, width=20, wrap="char", undo=True,
            font="TkDefaultFont", relief="flat", borderwidth=0,
            background=theme.FIELD_BG, foreground=theme.TEXT, insertbackground=theme.TEXT,
            highlightthickness=1, highlightbackground=theme.BORDER, highlightcolor=theme.ACCENT,
            padx=3, pady=2,
        )
        text.insert("1.0", value)
        text.edit_modified(False)
        text.edit_reset()
        return text

    def _on_text_change(self, widget: tk.Text, key: str, action) -> None:
        """複数行の欄の中身が変わったら、少し待ってから action（保存）を行う。"""
        def modified(_e=None) -> None:
            if not widget.edit_modified():
                return
            widget.edit_modified(False)
            self._queue_description_input(key, lambda: action() if widget.winfo_exists() else None)

        widget.bind("<<Modified>>", modified)

    # ------------------------------------------------------------------ 欄を変えたときの保存
    def _save_header(self, name: str, value: str) -> None:
        try:
            self._workspace.set_description_header(name, value)
        except (OSError, ValueError) as exc:
            self._report_save_error(exc)

    def _save_row_field(self, row, name: str, value) -> None:
        try:
            self._workspace.set_row_field(row, name, value)
        except (OSError, ValueError) as exc:
            self._report_save_error(exc)

    def _commit_title(self, row, get, put, committed: dict | None = None, quiet: bool = False) -> None:
        """標目を確定する。get：欄の字を返す。put：欄の字を入れ替える（直せなかったとき、元に戻す）。
        committed：最後に確定した標目（{"value": …}。確定したら入れ替える）。quiet：字を入れている途中の
        確定（直せない字でも知らせの画面は出さず、画面の下の行に出す。右のペインは組み直さない。PDF の
        作り直しは、呼び出した _queue_description_input が頼む）。"""
        ws = self._workspace
        current = committed["value"] if committed is not None else row.title
        value = get()
        if ws is None or value.strip() == current:
            return
        try:
            ws.set_row_title(row, value)
        except ValueError as exc:
            if quiet:
                self._status_label.config(text=f"標目：{errors.describe(exc, detail=False)}")
                return
            put(current)
            self._tell(exc, "証拠説明書の入力", icon="error")
            return
        except OSError as exc:
            self._report_save_error(exc)
            return
        if committed is not None:
            committed["value"] = value.strip()
        if not quiet:
            self._after_description_change()

    def _commit_category(self, row, category: str) -> None:
        ws = self._workspace
        if ws is None or category == row.category:
            return
        try:
            ws.set_row_category(row, category)
        except ValueError as exc:
            self._tell(exc, "証拠説明書の入力", icon="error")
            self._rebuild_options_keep_scroll()
            return
        except OSError as exc:
            self._report_save_error(exc)
            return
        self._after_description_change()

    def _after_description_change(self) -> None:
        """標目・原本写しの別・標目をまたがせるかを変えた後：右のペインを組み直し（見ていた位置のまま。入力中の
        欄の後始末が済んでから）、一覧の名前（＊）を直し、PDF の作り直しを頼む。"""
        self._refresh_tree_marks()
        self.after_idle(self._rebuild_options_keep_scroll)
        self._request_description_sync()
