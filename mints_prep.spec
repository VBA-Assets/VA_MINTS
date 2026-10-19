# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=['src'],
    binaries=[],
    # ウィンドウ（タイトルバー・タスクバー）用アイコン。実行ファイル自体の
    # アイコンはEXE(icon=...)で埋め込むが、Tkウィンドウ側は実行時にファイル
    # として読む必要があるため、onefileの展開先（sys._MEIPASS）へ同梱する。
    datas=[('assets/VA_mints.ico', 'assets')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='VA_mints',
    icon='assets/VA_mints.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
