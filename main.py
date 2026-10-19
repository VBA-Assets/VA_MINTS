"""VA mints 起動スクリプト。

使い方（Windows）：
    .venv\\Scripts\\python.exe main.py

src/ 以下をパッケージとしてインストールしていない環境でも起動できるよう、
sys.path に src を追加してからGUIを起動する。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from mints_prep.ui.app import main  # noqa: E402

if __name__ == "__main__":
    main()
