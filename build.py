"""Build a local native desktop bundle on the current OS."""

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import shutil
import tomllib
from pathlib import Path


# Preserve the identifier used by existing installations and privacy records.
MACOS_BUNDLE_ID = "YT Slop DL"
SIGNING_CONFIG = "config.local.signing.json"


def signing_identity(root: Path, requested: str | None = None) -> str:
    """Pin a real Keychain identity; never silently downgrade a signed build."""
    config = root / SIGNING_CONFIG
    if not requested and config.exists():
        try:
            requested = json.loads(config.read_text(encoding="utf-8"))["identity"]
            if not isinstance(requested, str) or not requested:
                raise ValueError()
        except (ValueError, KeyError, TypeError) as error:
            raise RuntimeError(f"Invalid {SIGNING_CONFIG}; choose --codesign-identity explicitly.") from error
    result = subprocess.run(["security", "find-identity", "-v", "-p", "codesigning"],
                            check=True, capture_output=True, text=True)
    identities = re.findall(r'^\s*\d+\) ([0-9A-Fa-f]{40}) "([^"]+)"', result.stdout, re.MULTILINE)
    matches = [digest for digest, name in identities
               if not requested or requested.upper() == digest.upper() or requested == name]
    if len(matches) != 1:
        raise RuntimeError("The selected signing identity is unavailable or ambiguous. "
                           "Use --codesign-identity with a valid Keychain certificate SHA-1. "
                           "--ad-hoc is only for disposable builds; privacy permissions will not survive updates.")
    return matches[0].upper()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", action="store_true", help="Also build a Windows installer (requires Inno Setup 6)")
    parser.add_argument("--iscc", help="Path to the Inno Setup compiler")
    signing = parser.add_mutually_exclusive_group()
    signing.add_argument("--codesign-identity", help="macOS Keychain certificate name/SHA-1; remembered locally")
    signing.add_argument("--ad-hoc", action="store_true", help="Disposable macOS build without persistent privacy identity")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    system = platform.system()
    compiler = None
    if args.installer:
        if system != "Windows" or platform.machine().lower() not in ("amd64", "x86_64"):
            parser.error("The installer currently targets Windows x64 only.")
        candidates = [args.iscc, shutil.which("ISCC"),
                      str(root / ".tools" / "inno" / "ISCC.exe"),
                      str(Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Inno Setup 6" / "ISCC.exe")]
        compiler = next((path for path in candidates if path and Path(path).is_file()), None)
        if not compiler:
            parser.error("Install Inno Setup 6 or pass --iscc PATH.")
    identity = signing_identity(root, args.codesign_identity) if system == "Darwin" and not args.ad_hoc else None
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QApplication
    application = QApplication([])
    assets = root / "build" / "icons"
    assets.mkdir(parents=True, exist_ok=True)
    # Keep the background full bleed: macOS applies its own rounded icon mask.
    # Transparent padding would reveal a system plate around the artwork.
    source = QImage(str(root / "app" / "assets" / "icon.png"))
    if source.isNull() or source.width() != source.height():
        raise RuntimeError("The application icon must be a valid square PNG.")

    def render(size, target, format="PNG"):
        image = QImage(size, size, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(image.rect(), source)
        painter.end()
        if not image.save(str(target), format):
            raise RuntimeError(f"Cannot create icon: {target}")

    if system == "Darwin":
        iconset = assets / "YTSlopDL-Lemon-FullBleed.iconset"
        iconset.mkdir(exist_ok=True)
        for size in (16, 32, 128, 256, 512):
            render(size, iconset / f"icon_{size}x{size}.png")
            render(size * 2, iconset / f"icon_{size}x{size}@2x.png")
        icon = assets / "YTSlopDL-Lemon-FullBleed.icns"
        subprocess.run(["iconutil", "-c", "icns", "-o", str(icon), str(iconset)], check=True)
    elif system == "Windows":
        icon = assets / "YTSlopDL-Lemon-FullBleed.ico"
        render(256, icon, "ICO")
    else:
        icon = assets / "YTSlopDL-Lemon-FullBleed.png"
        render(256, icon)
    signing_args = []
    if system == "Darwin":
        signing_args = ["--osx-bundle-identifier", MACOS_BUNDLE_ID]
        if identity:
            signing_args += ["--codesign-identity", identity]
        else:
            print("Warning: ad-hoc build; macOS privacy permissions may reset after rebuilding.", flush=True)
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", "--onedir", "--windowed",
        "--name", "YT Slop DL", "--paths", str(root),
        "--icon", str(icon), "--add-data", f"{root / 'app' / 'assets'}{os.pathsep}app/assets",
        *signing_args, str(root / "app" / "main.py"),
    ], cwd=root, check=True)
    if system == "Darwin":
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(root / "dist" / "YT Slop DL.app")], check=True)
        if identity:
            config = root / SIGNING_CONFIG
            temporary = config.with_suffix(".tmp")
            temporary.write_text(json.dumps({"identity": identity}) + "\n", encoding="utf-8")
            temporary.replace(config)
    if system == "Linux":
        desktop = root / "dist" / "YT Slop DL.desktop"
        desktop.write_text(
            '[Desktop Entry]\nType=Application\nName=YT Slop DL\n'
            f'Exec="{root / "dist" / "YT Slop DL" / "YT Slop DL"}"\n'
            f'Icon={icon}\nTerminal=false\nCategories=AudioVideo;\n', encoding="utf-8")
        desktop.chmod(0o755)
    print("Built desktop application in", root / "dist")
    if args.installer:
        version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        subprocess.run([compiler, f"/DAppVersion={version}", str(root / "packaging" / "windows.iss")],
                       cwd=root, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
