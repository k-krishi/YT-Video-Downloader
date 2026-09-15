#!/usr/bin/env python3
"""
Cross-platform build script for YT-Downloader GUI using PyInstaller.

Usage:
    python build.py

Output:
    - macOS: dist/YT-Downloader.app and dist/YT-Downloader
    - Windows: dist/YT-Downloader.exe
    - Linux: dist/YT-Downloader (standalone ELF binary)
"""

from __future__ import annotations

import os
import sys
import shutil
import platform
from pathlib import Path

ROOT_DIR = Path(__file__).parent.resolve()
MAIN_SCRIPT = ROOT_DIR / "main.py"
APP_NAME = "YT-Downloader"


def build() -> None:
    current_os = platform.system()
    print(f"=== Building {APP_NAME} for {current_os} ({platform.machine()}) ===")
    
    try:
        import PyInstaller.__main__
    except ImportError:
        print("Error: PyInstaller is not installed. Run 'pip install -r requirements.txt'")
        sys.exit(1)

    dist_dir = ROOT_DIR / "dist"
    build_dir = ROOT_DIR / "build"

    # Base PyInstaller arguments
    pyi_args = [
        str(MAIN_SCRIPT),
        "--name", APP_NAME,
        "--windowed",  # GUI app (no background terminal console window)
        "--noconfirm",
        "--clean",
        "--collect-all", "yt_dlp",
        "--collect-data", "certifi",
        "--distpath", str(dist_dir),
        "--workpath", str(build_dir),
        "--specpath", str(ROOT_DIR),
    ]

    # Use --onedir on macOS to create a native macOS .app bundle properly, --onefile on Windows/Linux
    if current_os == "Darwin":
        pyi_args.append("--onedir")
    else:
        pyi_args.append("--onefile")

    print(f"Running PyInstaller with arguments: {' '.join(pyi_args)}")
    PyInstaller.__main__.run(pyi_args)

    print("\n=== Build Complete ===")
    print(f"Artifacts located in: {dist_dir}")
    if dist_dir.exists():
        for item in dist_dir.iterdir():
            if item.name.startswith("."):
                continue
            size_str = "Directory/App" if item.is_dir() else f"{item.stat().st_size / 1024 / 1024:.2f} MB"
            print(f"  - {item.name} ({size_str})")


if __name__ == "__main__":
    build()
