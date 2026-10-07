# PyInstaller build spec for the end-user statement generator.
#
# Build with:  scripts\build_exe.ps1      (or: pyinstaller statement.spec --noconfirm)
#
# Deliberately does NOT bundle .env or households.toml — those hold Easee
# credentials and tenant personal data, and anything inside a PyInstaller
# archive is extractable. They are read at runtime from the folder the .exe
# sits in; see ocpp_garage.statement_cli.app_dir().

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

datas = []
datas += collect_data_files("fpdf")      # core font metrics
datas += collect_data_files("certifi")   # CA bundle for the HTTPS call to Easee

# Anaconda keeps its native libraries in Library\bin rather than beside the
# .pyd files that link against them, so PyInstaller's dependency scan cannot
# find them and silently ships a build that dies on first use. _ssl is the
# critical one: without it the HTTPS call to Easee fails with
# "DLL load failed while importing _ssl".
_LIB_BIN = Path(sys.base_prefix) / "Library" / "bin"
_REQUIRED_DLLS = [
    "libssl-3-x64.dll",     # _ssl
    "libcrypto-3-x64.dll",  # _ssl, _hashlib
    "liblzma.dll",          # _lzma
    "libbz2.dll",           # _bz2
    "ffi.dll",              # _ctypes
]

binaries = []
for _name in _REQUIRED_DLLS:
    _path = _LIB_BIN / _name
    if not _path.exists():
        raise SystemExit(
            f"Required library {_name} not found in {_LIB_BIN}.\n"
            "The build would produce an executable that fails at runtime."
        )
    binaries.append((str(_path), "."))

# The base Anaconda environment carries a lot of packages this tool never
# touches. Excluding them keeps the executable small and avoids pulling in
# native libraries that Anaconda keeps in Library\bin rather than next to the
# .pyd files, which PyInstaller cannot resolve.
#
# setuptools/pkg_resources in particular must go: its runtime hook fails at
# startup looking for a vendored platformdirs, and nothing here imports it.
excludes = [
    # scientific stack
    "numpy", "pandas", "scipy", "matplotlib", "PIL", "IPython", "jupyter",
    "notebook", "zmq", "cloudpickle", "lz4",
    # packaging machinery — breaks the frozen bootstrap, unused at runtime
    "setuptools", "pkg_resources", "pip", "distutils",
    # project deps not reached by the statement path
    "sqlalchemy", "alembic", "asyncpg", "apscheduler", "ocpp",
    # native libs Anaconda stores outside the DLL search path
    "cryptography", "paramiko", "lxml", "gssapi", "psutil", "yaml", "bcrypt",
    "nacl", "pygments", "rich",
    # GUI toolkits
    "tkinter", "PyQt5", "PySide2", "pydoc_data",
]

# NOTE: unittest must NOT be excluded. fpdf.sign imports it at module load, so
# `import fpdf` dies with ModuleNotFoundError in the frozen build without it.
#
# Pillow is excluded above: these statements contain no images. fpdf2 warns
# about its absence on import, which statement_cli silences.

# NOTE: tzdata must NOT be excluded. Windows ships no system zone database,
# so zoneinfo.ZoneInfo("Europe/London") in billing/pdf.py depends on it.

a = Analysis(
    ["scripts/generate_statement.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=["pydantic_settings", "fpdf"],
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="EV-Statement",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,          # end user needs to see the prompt and the result
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
