"""診断情報（［ヘルプ］→［診断情報をコピー］。VA Case シリーズの common/diagnostics.py にならい、
VA FAX の F-24〔例外を、型の名前と発生した場所だけで記録する〕の考え方を土台にする。2026-11-10 班長決定）。

不具合を AI や開発者に相談するときに渡す文を作る。中身（証拠・書面のファイル名、ファイルやフォルダの場所、
利用者・PC の名前、例外のメッセージの本文）は、一切入れない。入れてよいのは、版・環境・設定の数値・件数・
誤りの型と起きた場所（ファイル名・行・関数名）だけとする。
例外のメッセージの本文を入れないのは、ファイルの読み書きの誤り（OSError など）のメッセージに、ファイルの
場所や名前（＝事件名・依頼者名が入ることがある）が入るため。
"""

from __future__ import annotations

import json
import os
import platform
import sys
import traceback
from datetime import datetime
from pathlib import Path

KEEP_ERRORS = 30     # 覚えておく誤りの数（古いものから消す）
SHOW_ERRORS = 10     # 診断情報に載せる誤りの数（新しいものから）
MAX_FRAMES = 12      # 1つの誤りに載せる場所の数（深い方から）


def exception_record(exc: BaseException, stage: str = "") -> dict:
    """例外を、型の名前と起きた場所だけの記録にする（メッセージの本文は入れない）。"""
    frames = traceback.extract_tb(exc.__traceback__)[-MAX_FRAMES:]
    record = {
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "stage": stage,
        "type": type(exc).__name__,
        "where": [f"{Path(f.filename).name}:{f.lineno}:{f.name}" for f in frames],
    }
    # OSError の番号（何の誤りかの手がかりになり、場所や名前は含まない）
    for name in ("errno", "winerror"):
        value = getattr(exc, name, None)
        if isinstance(value, int):
            record[name] = value
    cause = exc.__cause__ or exc.__context__
    if cause is not None:
        record["cause"] = type(cause).__name__
    return record


def errors_path() -> Path:
    """誤りの記録の置き場所。exe の隣に書くとフォルダによっては書けないことがあるため、
    利用者ごとのアプリデータの場所（%APPDATA%\\VA mints）に置く。"""
    base = os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / ".va_mints"
    return root / "VA mints" / "errors.json"


class ErrorLog:
    """起きた誤りを覚える。閉じた後や、落ちた後でも渡せるよう、ファイルにも書く。

    書けなくても、画面の動きは止めない（診断のための記録なので）。
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else errors_path()
        try:
            records = json.loads(self.path.read_text(encoding="utf-8"))
            self.records = [r for r in records if isinstance(r, dict)][-KEEP_ERRORS:]
        except (OSError, ValueError, TypeError):
            self.records = []

    def record(self, exc: BaseException, stage: str = "") -> None:
        try:
            self.records.append(exception_record(exc, stage))
            self.records = self.records[-KEEP_ERRORS:]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.records, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
        except Exception:
            pass

    def clear(self) -> None:
        self.records = []
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass


def format_record(r: dict) -> list[str]:
    head = f"{r.get('at', '')}　{r.get('type', '')}"
    extra = [f"{k}={r[k]}" for k in ("errno", "winerror") if k in r]
    if r.get("cause"):
        extra.append(f"原因 {r['cause']}")
    if extra:
        head += "（" + "・".join(extra) + "）"
    lines = [head]
    if r.get("stage"):
        lines.append(f"    処理：{r['stage']}")
    for w in r.get("where", []):
        lines.append(f"    {w}")
    return lines


def environment_lines(root, title: str, version: str) -> list[str]:
    frozen = getattr(sys, "frozen", False)
    lines = [f"ツール：{title}  {version}（{'exe' if frozen else 'ソースから起動'}）",
             f"OS：{platform.platform()}",
             f"Python：{platform.python_version()}　Tk：{_tk_patchlevel(root)}"]
    try:
        scale = root.winfo_fpixels("1i") / 96.0
        lines.append(f"画面：{root.winfo_screenwidth()}×{root.winfo_screenheight()}・拡大率 {scale:.0%}・"
                     f"ウィンドウ {root.winfo_width()}×{root.winfo_height()}")
    except Exception:
        pass
    return lines


def _tk_patchlevel(root) -> str:
    try:
        return root.tk.call("info", "patchlevel")
    except Exception:
        return "不明"
