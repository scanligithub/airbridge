# -*- mode: python ; coding: utf-8 -*-
# AirBridge.spec
# 完整集成 protocol, eda_engine, fs_engine 单文件打包清单

block_cipher = None

a = Analysis(
    ['airbridge_app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('protocol.py', '.'),
        ('eda_engine.py', '.'),
        ('fs_engine.py', '.'),
    ],
    hiddenimports=[
        'serial',
        'serial.tools.list_ports',
        'PySide6.QtCore',
        'PySide6.QtWidgets',
        'PySide6.QtGui',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='AirBridge',
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
