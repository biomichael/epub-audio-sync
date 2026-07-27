# -*- mode: python -*-
import os
from pathlib import Path

datas = [
    ("aeneas/res/*",            "aeneas/res"),
    ("aeneas/tools/res/*",      "aeneas/tools/res"),
    ("aeneas/extra/*.py",       "aeneas/extra"),
    ("aeneas/extra/.gitignore", "output"),
]

# Try to locate espeak_sapi.dll for the cew C extension
_espeak_dll = None
_candidates = [
    r"C:\Program Files (x86)\eSpeak\command_line\espeak_sapi.dll",
    r"C:\Program Files\eSpeak\command_line\espeak_sapi.dll",
    r"C:\Program Files (x86)\eSpeak NG\espeak-ng.dll",
    r"C:\Program Files\eSpeak NG\espeak-ng.dll",
]
for _c in _candidates:
    if Path(_c).is_file():
        _espeak_dll = _c
        break
_binaries = [(_espeak_dll, ".")] if _espeak_dll else []

block_cipher = None

a = Analysis(
    ["pyinstaller-epub-sync-desktop.py"],
    pathex=[],
    binaries=_binaries,
    datas=datas,
    hiddenimports=[
        # Dynamic engine dispatch
        "aeneas.epubsync.alignment",
        "aeneas.epubsync.webapp",
        "aeneas.epubsync.desktop_api",
        # FastAPI / uvicorn
        "fastapi",
        "uvicorn",
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.middleware",
        "uvicorn.middleware.wsgi",
        # lxml C extensions
        "lxml._elementpath",
        "lxml.etree",
        "lxml.html",
        # NumPy
        "numpy",
        "numpy.core._multiarray_umath",
        "numpy.core._multiarray_tests",
        "numpy.core.multiarray",
        "numpy.fft",
        "numpy.fft.fftpack_lite",
        "numpy.linalg",
        "numpy.linalg._umath_linalg",
        "numpy.random",
        # BeautifulSoup / soupsieve
        "bs4",
        "bs4.builder._lxml",
        "soupsieve",
        # CFFI (used by various packages)
        "cffi",
        "cffi.api",
        "cffi.cparser",
        "cffi.backend_ctypes",
        # Concurrency (uvicorn uses these)
        "multiprocessing",
        "multiprocessing.spawn",
        "concurrent",
        "concurrent.futures",
        # lxml used in pipeline
        "lxml.isoschematron",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "PIL",
        "PyQt5",
        "PySide2",
        "PySide6",
        "test",
        "unittest",
        "distutils",
        "setuptools",
        "pip",
        "wheel",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="desktop_api",
    debug=False,
    strip=False,
    upx=True,
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    name="desktop_api",
    strip=False,
    upx=True,
)
