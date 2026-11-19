"""配布用のリリース（release_VerNNN\\VA_mints_VerNNN\\ と、dist\\ の zip）を組み立てる（標準 02「ビルドと配布物」）。

前提（このスクリプトはビルドそのものはしない。先に済ませておく）：
    1. python -m PyInstaller va_mints.spec --noconfirm   （dist\\VA_mints.exe を最新のコードで作る）
    2. python scripts\\generate_manual.py                 （docs\\manual\\VA_mints_操作マニュアル_VerNNN.pdf。
       先にマニュアルのフロントマターの version を上げておく）
    3. python scripts\\make_notices.py                    （部品を足したとき・版を上げたとき）

実行のしかた：
    python scripts\\build_release.py Ver311
    python scripts\\build_release.py Ver311 dist_next
        （dist\\VA_mints.exe を誰かが開いていて上書きできないとき。PyInstaller を --distpath dist_next で
         動かして作った exe を使う）

できるもの：
    release_Ver311\\VA_mints_Ver311\\  （exe・config.yaml・LICENSE・THIRD_PARTY_NOTICES.txt・README.md・マニュアル\\・source\\）
    dist\\VA_mints_Ver311.zip          （上のフォルダを zip にしたもの）
統合版のインストーラー（VA_Documentシリーズ\\共通\\統合版）は、release_VerNNN\\VA_mints_VerNNN\\ を参照する。
release_*\\・dist\\ は、ビルドの生成物として git に入れない。
作ったら、VAプロジェクト\\標準\\点検\\check_tools.py release で、内部資料と個人の情報が入っていないことを確かめる。

VA mints だけのこと：config.yaml（mints の上限などの数値。利用者がテキストエディタで直せる）を、exe の隣に置く。
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from va_mints import FILE_STEM, VERSION_LABEL  # noqa: E402

EXE = f"{FILE_STEM}.exe"

# source\\ に入れないもの（git の管理下でも、公開しない内部の資料）。公開する要件定義書は docs/03_requirements.md だけ
# （CLAUDE.md の冒頭）。docs/primary-sources/ は裁判所の資料（著作権があるので、配らない）。
_EXCLUDE = [re.compile(p) for p in (
    r"(^|/)CLAUDE\.md$",
    r"(^|/)検討_",
    r"^試作/",
    r"^articles/",
    r"^\.claude/",
    r"^docs/01_project-brief\.md$",
    r"^docs/02_constraints-report\.md$",
    r"^docs/primary-sources/",
)]


def _source_files() -> list[str]:
    out = subprocess.run(["git", "-c", "core.quotepath=false", "ls-files", "-z"], cwd=ROOT, capture_output=True,
                         check=True).stdout
    files = [f for f in out.decode("utf-8").split("\0") if f]
    return [f for f in files if not any(p.search(f) for p in _EXCLUDE)]


def _clear_readonly(func, path, exc):
    """OneDrive はフォルダに読み取り専用の属性を付け、Windows は読み取り専用のフォルダを消せない。属性を外して、もう一度消す。"""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def build(version: str, dist: Path | None = None) -> Path:
    if not re.fullmatch(r"Ver[0-9A-Z]{3}", version):
        raise SystemExit("版は「Ver311」の形で指定してください。")
    if VERSION_LABEL != version:
        raise SystemExit(f"src の __init__.py の版（{VERSION_LABEL}）が {version} と合いません。")
    exe = (dist or ROOT / "dist") / EXE
    manual = ROOT / "docs" / "manual" / f"{FILE_STEM}_操作マニュアル_{version}.pdf"
    required = [exe, manual, ROOT / "config.yaml", ROOT / "LICENSE", ROOT / "THIRD_PARTY_NOTICES.txt",
                ROOT / "README.md"]
    missing = [str(p.relative_to(ROOT)) for p in required if not p.exists()]
    if missing:
        raise SystemExit("足りないファイルがあります：" + "、".join(missing))
    # exe が、いまのソースより古くないかを確かめる（直したのに作り直し忘れるのを防ぐ）
    newest = max(p.stat().st_mtime for p in (ROOT / "src").rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    if exe.stat().st_mtime < newest:
        raise SystemExit("exe がソースより古いです。先に PyInstaller で作り直してください。")

    stage = ROOT / f"release_{version}" / f"{FILE_STEM}_{version}"
    if stage.exists():
        shutil.rmtree(stage, onexc=_clear_readonly)
    (stage / "マニュアル").mkdir(parents=True)
    shutil.copy2(exe, stage / EXE)
    shutil.copy2(manual, stage / "マニュアル" / manual.name)
    for name in ("config.yaml", "LICENSE", "THIRD_PARTY_NOTICES.txt", "README.md"):
        shutil.copy2(ROOT / name, stage / name)
    for f in _source_files():
        dest = stage / "source" / f
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / f, dest)

    zip_path = ROOT / "dist" / f"{FILE_STEM}_{version}.zip"
    zip_path.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(stage.rglob("*")):
            if p.is_file():
                z.write(p, Path(stage.name) / p.relative_to(stage))
    return zip_path


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        raise SystemExit("使い方：python scripts\\build_release.py Ver311 [exe のあるフォルダ]")
    path = build(sys.argv[1], Path(sys.argv[2]).resolve() if len(sys.argv) == 3 else None)
    print(f"作りました：{path}（{path.stat().st_size:,} バイト）")
