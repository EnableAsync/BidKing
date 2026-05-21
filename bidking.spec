# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# 项目自身的静态数据（地图、英雄 JSON）
datas = collect_data_files('bidking', includes=['data/**/*.json'])
# RapidOCR 的 ONNX 模型 + config.yaml 必须随包打入，否则首次运行时找不到
datas += collect_data_files('rapidocr_onnxruntime')

# 显式声明 hidden imports，避免 PyInstaller 静态分析漏掉
hiddenimports = collect_submodules('rapidocr_onnxruntime') + [
    'onnxruntime',
    'onnxruntime.capi._pybind_state',
]

a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
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
    name='BidKing',
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
    icon=None,
)
