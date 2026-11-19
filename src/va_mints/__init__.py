"""VA mints：mints（民事裁判書類電子提出システム）「記録」に出すファイルを確かめ、提出用の PDF を準備するツール。"""

# 版はここの1か所に持つ（標準 02「版」）。9 の次は A（"2.A.0"）。表示は VERSION_LABEL（Ver311）を使う。
# spec・build_release.py・診断情報・「について」は、ここから読む。マニュアルのフロントマターの version も合わせる。
__version__ = "3.1.1"
VERSION_LABEL = "Ver" + __version__.replace(".", "")

APP_NAME = "VA mints"                        # ツールの名前（題名・メッセージ・「について」はここから作る）
FILE_STEM = "VA_mints"                       # exe・zip・マニュアルの名前に使う形
ABOUT_LINE = "mints 提出前のチェックと提出用PDFの準備"   # 「について」の1行の説明（統合版・記事の説明と同じにする）
APP_USER_MODEL_ID = "VBAAssets.VAmints.Source"
