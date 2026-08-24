# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir build for CodexQuota.exe."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


PACKAGING_DIR = Path(SPECPATH).resolve()
ROOT = PACKAGING_DIR.parents[1]
CLOUDFLARED = ROOT / "vendor" / "bin" / "cloudflared.exe"
ICON = ROOT / "assets" / "codex-quota.ico"

if not CLOUDFLARED.is_file():
    raise SystemExit(
        f"Missing {CLOUDFLARED}; run install.ps1 or build-installer.ps1 first"
    )
if not ICON.is_file():
    raise SystemExit(f"Missing application icon: {ICON}")

hiddenimports = (
    collect_submodules("codex_quota.providers")
    + collect_submodules("codex_quota.ui")
)

a = Analysis(
    [str(PACKAGING_DIR / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=[(str(CLOUDFLARED), "vendor/bin")],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "tests"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CodexQuota",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=str(ICON),
    version=str(PACKAGING_DIR / "version_info.txt"),
    uac_admin=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="CodexQuota",
)
