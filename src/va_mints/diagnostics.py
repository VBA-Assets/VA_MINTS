"""診断情報（［ヘルプ］→［診断情報をコピー］。標準 02「診断情報」。VA Document・VA FAX・VA mints・VA Case と同じ形）。

不具合を AI や作成者に相談するときに渡す文を作る。中身（文書の字、ファイルやフォルダの名前と場所、利用者・PC の名前、
例外のメッセージの本文）は、一切入れない。入れてよいのは、版・環境・設定の値（選択肢・数値）・件数・誤りの型と
起きた場所（ファイル名・行・関数名）だけとする。
例外のメッセージの本文を入れないのは、ファイルの読み書きの誤り（OSError など）のメッセージに、フォルダの場所
（依頼者の名前が入ることがある）が入るため。

誤りの記録は、この PC の設定と同じフォルダ（errors.json）に書く（閉じた後・落ちた後でも渡せるように）。
"""
import json
import os
import platform
import sys
import traceback
from datetime import datetime
from pathlib import Path

from .user_settings import config_dir

KEEP_ERRORS = 30     # 覚えておく誤りの数（古いものから消す）
SHOW_ERRORS = 10     # 診断情報に載せる誤りの数（新しいものから）
MAX_FRAMES = 12      # 1つの誤りに載せる場所の数（深い方から）


def exception_record(exc, stage=""):
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


def errors_path():
    """誤りの記録の置き場所（この PC の設定と同じフォルダ）。"""
    return config_dir() / "errors.json"


def _same(a, b):
    return all(a.get(k) == b.get(k) for k in ("stage", "type", "where", "errno", "winerror", "cause"))


class ErrorLog:
    """起きた誤りを覚える（型と場所だけ）。閉じた後や、落ちた後でも渡せるよう、ファイルにも書く。

    書けなくても、画面の動きは止めない（診断のための記録なので）。同じ誤りが続けて起きたときは、1件にまとめて
    回数を数える（ほかの誤りが押し出されないように）。
    """

    def __init__(self, path=None):
        self.path = Path(path) if path else errors_path()
        try:
            records = json.loads(self.path.read_text(encoding="utf-8"))
            self.records = [r for r in records if isinstance(r, dict)][-KEEP_ERRORS:]
        except (OSError, ValueError, TypeError):
            self.records = []

    def record(self, exc, stage=""):
        try:
            new = exception_record(exc, stage)
            if self.records and _same(self.records[-1], new):
                last = self.records[-1]
                last["at"] = new["at"]
                last["count"] = int(last.get("count", 1)) + 1
            else:
                self.records.append(new)
                self.records = self.records[-KEEP_ERRORS:]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            # 改行を変えないよう、バイトで書く（Windows の write_text は \n を \r\n にする）
            tmp.write_bytes(json.dumps(self.records, ensure_ascii=False, indent=1).encode("utf-8"))
            os.replace(tmp, self.path)
        except Exception:
            pass

    def clear(self):
        self.records = []
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass


def format_record(record):
    """1件の誤りを、診断情報に載せる行にする。"""
    head = f"{record.get('at', '')}　{record.get('type', '')}"
    extra = [f"{key}={record[key]}" for key in ("errno", "winerror") if key in record]
    if record.get("cause"):
        extra.append(f"原因 {record['cause']}")
    if int(record.get("count", 1)) > 1:
        extra.append(f"続けて {record['count']} 回")
    if extra:
        head += "（" + "・".join(extra) + "）"
    lines = [head]
    if record.get("stage"):
        lines.append(f"    処理：{record['stage']}")
    for where in record.get("where", []):
        lines.append(f"    {where}")
    return lines


def environment_lines(root, title, version):
    """環境（版・OS・Python/Tk・画面）の行。利用者名・PC 名・フォルダの場所は入れない。"""
    frozen = getattr(sys, "frozen", False)
    lines = [
        f"ツール：{title}  {version}（{'exe' if frozen else 'ソースから起動'}）",
        f"OS：{platform.platform()}",
        f"Python：{platform.python_version()}　Tk：{_tk_patchlevel(root)}",
    ]
    try:
        scale = root.winfo_fpixels("1i") / 96.0
        lines.append(f"画面：{root.winfo_screenwidth()}×{root.winfo_screenheight()}・拡大率 {scale:.0%}・"
                     f"ウィンドウ {root.winfo_width()}×{root.winfo_height()}")
    except Exception:       # 画面の情報が取れなくても、診断情報は作る
        pass
    return lines


def diagnostic_text(root, title, version, setting_lines, state_lines, errors):
    """診断情報の文を組み立てる。setting_lines・state_lines は、値・場所・名前を入れない行（呼ぶ側で作る）。"""
    lines = [f"{title} 診断情報（{datetime.now():%Y-%m-%d %H:%M}）",
             "※ 文書の中身、ファイルやフォルダの場所・名前、利用者と PC の名前は入っていません。", ""]
    lines += ["■ 環境"] + environment_lines(root, title, version) + [""]
    lines += ["■ この PC の設定"] + list(setting_lines) + [""]
    lines += ["■ 状態"] + list(state_lines) + [""]
    records = errors.records
    lines.append(f"■ 起きた誤り（新しいものから {min(len(records), SHOW_ERRORS)} 件。覚えているのは全 {len(records)} 件）")
    if not records:
        lines.append("なし")
    for r in reversed(records[-SHOW_ERRORS:]):
        lines += format_record(r)
    return "\n".join(lines) + "\n"


def _tk_patchlevel(root):
    try:
        return root.tk.call("info", "patchlevel")
    except Exception:
        return "不明"
