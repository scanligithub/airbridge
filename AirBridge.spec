# -*- mode: python ; coding: utf-8 -*-
# AirBridge.spec
# Unified AirBridge.exe: GUI default + MCP STDIO subprocess mode.

from PyInstaller.utils.hooks import collect_all

mcp_datas, mcp_binaries, mcp_hiddenimports = collect_all('mcp')

block_cipher = None

a = Analysis(
    ['airbridge_entry.py'],
    pathex=['mcp_gateway'],
    binaries=mcp_binaries,
    datas=[
        ('protocol.py', '.'),
        ('eda_engine.py', '.'),
        ('fs_engine.py', '.'),
        ('shell_engine.py', '.'),
    ] + mcp_datas,
    hiddenimports=[
        'serial',
        'serial.tools.list_ports',
        'PySide6.QtCore',
        'PySide6.QtWidgets',
        'PySide6.QtGui',
        'airbridge_mcp_gateway.server',
    ] + mcp_hiddenimports,
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
    # Keep the desktop application windowed. The MCP entrypoint restores
    # inherited STARTF_USESTDHANDLES pipes before starting the STDIO SDK.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
