"""配布用リリースzip（dist/VA_mints_VerNNN.zip）を組み立てる。

Ver.100は手作業でzip化されており再現手順が残っていなかったため、Ver.110
以降はこのスクリプトで再現できるようにする（samples・アイコン・マニュアルと
同じ「ソース＋生成スクリプトのみ履歴管理し、生成物は都度作る」方針）。

前提（このスクリプトはビルドそのものは行わない。事前に済ませておくこと）：
    1. .venv\\Scripts\\pyinstaller.exe mints_prep.spec --noconfirm
       （dist\\VA_mints.exe を最新コードで生成）
    2. dist\\config.yaml をリポジトリ直下のconfig.yamlと同期
       （cp config.yaml dist\\config.yaml）
    3. .venv\\Scripts\\python.exe scripts\\generate_manual.py
       （docs\\manual\\VA_mints_操作マニュアル_VerNNN.pdf を生成。
       事前にdocs/manual/*.mdのフロントマターのversionを更新しておくこと）

実行方法：
    .venv\\Scripts\\python.exe scripts\\build_release.py Ver110

生成物：
    dist/VA_mints_Ver110.zip
    （配布用zipの置き場所はVA FAX〔dist/VA_FAX_v100.zip〕に合わせてdist/と
    する。2026-07-30変更。それ以前はrelease_VerNNN/直下に置いていた）
    release_Ver110/VA_mints_Ver110/
    （zip化前のステージングフォルダ。内容確認用に残す。release_*/・dist/は
    いずれもビルド生成物としてgitignore対象）
"""

from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# source/ に含めるソース一式。samplesの実測データ・primary-sources（一次資料
# 現物。著作権の観点から再配布しない）・__pycache__・.pyc等は含めない
# （Ver.100の構成を踏襲）。
_SOURCE_FILES = ["CLAUDE.md", "LICENSE", "main.py", "mints_prep.spec", "pytest.ini", "requirements.txt"]
_SOURCE_DIR_SPECS: list[tuple[str, str, tuple[str, ...]]] = [
    # (相対パス, コピー先の相対パス, 追加で除外するディレクトリ名のタプル)
    # __pycache__・.pyc・.pytest_cacheはどのディレクトリでも常に除外する
    # （_ignore内で一律処理するため、ここには追加除外分のみ書く）。
    ("assets", "assets", ()),
    ("docs", "docs", ("primary-sources",)),  # 一次資料現物は著作権のため除外
    ("scripts", "scripts", ()),
    ("src", "src", ()),
    ("tests", "tests", ()),
]

_ALWAYS_EXCLUDE_DIRS = ("__pycache__", ".pytest_cache")


def _copy_tree_excluding(src: Path, dst: Path, exclude_names: tuple[str, ...]) -> None:
    def _ignore(dir_path: str, names: list[str]) -> set[str]:
        ignored = {
            n for n in names if n in exclude_names or n in _ALWAYS_EXCLUDE_DIRS or n.endswith(".pyc")
        }
        # docs/直下の生成PDF（マニュアル）はトップレベルの「マニュアル/」フォルダに
        # 別途配置するため、source/docs/manual/配下のPDFはソース重複を避けて除外する。
        if dir_path.replace("\\", "/").endswith("docs/manual"):
            ignored |= {n for n in names if n.endswith(".pdf")}
        return ignored

    shutil.copytree(src, dst, ignore=_ignore)


def build(version: str) -> Path:
    """version例："Ver110"（config.yaml等の表記に合わせ、ドットなし表記）。"""
    dist_exe = ROOT / "dist" / "VA_mints.exe"
    dist_config = ROOT / "dist" / "config.yaml"
    manual_pdf = ROOT / "docs" / "manual" / f"VA_mints_操作マニュアル_{version}.pdf"
    license_file = ROOT / "LICENSE"

    missing = [p for p in (dist_exe, dist_config, manual_pdf, license_file) if not p.exists()]
    if missing:
        joined = "\n  ".join(str(p) for p in missing)
        raise FileNotFoundError(
            f"リリースに必要なファイルが見つかりません（先に準備すること）：\n  {joined}"
        )

    release_dir = ROOT / f"release_{version}"
    package_dir = release_dir / f"VA_mints_{version}"
    if release_dir.exists():
        shutil.rmtree(release_dir)
    package_dir.mkdir(parents=True)

    shutil.copy2(dist_exe, package_dir / "VA_mints.exe")
    shutil.copy2(dist_config, package_dir / "config.yaml")
    shutil.copy2(license_file, package_dir / "LICENSE")

    manual_dir = package_dir / "マニュアル"
    manual_dir.mkdir()
    shutil.copy2(manual_pdf, manual_dir / manual_pdf.name)

    source_dir = package_dir / "source"
    source_dir.mkdir()
    for name in _SOURCE_FILES:
        shutil.copy2(ROOT / name, source_dir / name)
    for rel_src, rel_dst, exclude in _SOURCE_DIR_SPECS:
        _copy_tree_excluding(ROOT / rel_src, source_dir / rel_dst, exclude)

    zip_path = ROOT / "dist" / f"VA_mints_{version}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(package_dir.rglob("*")):
            zf.write(path, path.relative_to(release_dir))

    return zip_path


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("使い方：python scripts\\build_release.py VerNNN（例：Ver110）", file=sys.stderr)
        raise SystemExit(1)
    result = build(sys.argv[1])
    print(f"生成完了：{result}")
