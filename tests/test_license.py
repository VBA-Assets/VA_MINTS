"""配るプログラム（src）が、MIT License と合わない部品を使っていないことを確かめる（2026-11-17）。

VA mints は MIT License。GPL・AGPL の部品を exe に入れると、exe 全体をその条件で配ることになる。
Ver201 まで mutagen（GPL-2.0-or-later）を使っていたので、外して自前にした（detectors/av_tags.py）。
"""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
# MIT License と合わない部品（import の名前）
_FORBIDDEN = {"mutagen": "GPL-2.0-or-later", "fitz": "AGPL-3.0（PyMuPDF）", "pymupdf": "AGPL-3.0（PyMuPDF）"}


def test_src_does_not_import_gpl_packages():
    found = []
    for path in SRC.rglob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*(?:from|import)\s+([A-Za-z0-9_]+)", line)
            if m and m.group(1).lower() in _FORBIDDEN:
                found.append(f"{path.relative_to(SRC)}：{line.strip()}（{_FORBIDDEN[m.group(1).lower()]}）")
    assert not found, "\n".join(found)


def test_requirements_do_not_list_gpl_packages():
    text = (SRC.parent / "requirements.txt").read_text(encoding="utf-8")
    listed = [line.split(">")[0].split("=")[0].strip().lower() for line in text.splitlines()
              if line.strip() and not line.lstrip().startswith("#")]
    assert not set(listed) & {"mutagen", "pymupdf"}
