# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — FFmpeg Studio 桌面版

入口是 desktop.py（pywebview 原生窗口），不是 server/main.py。
后端仍在同一进程内跑，但只绑 127.0.0.1 的随机端口，不再经过浏览器。
"""
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

_HERE = Path(os.path.abspath(SPECPATH))

datas = [(str(_HERE / "frontend"), "frontend")]
binaries = []
hiddenimports = ["aiofiles", "pydantic"]

# uvicorn / pywebview / pythonnet 都有靠反射或数据文件加载的部分，
# 静态分析看不到，必须 collect_all 收进来
for _pkg in ("uvicorn", "webview", "pythonnet", "clr_loader"):
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h

# pywebview 的 Windows 后端是 WinForms + WebView2，运行时按平台动态导入
hiddenimports += [
    "webview.platforms.winforms",
    "webview.platforms.edgechromium",
]


a = Analysis(
    [str(_HERE / "desktop.py")],
    pathex=[str(_HERE)],
    binaries=binaries,
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
    name="FFmpegStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # 桌面应用：不弹控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
