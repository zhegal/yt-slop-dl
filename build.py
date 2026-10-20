"""Build a local native desktop bundle on the current OS."""

import argparse
import json
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path


# Preserve the identifier used by existing installations and privacy records.
MACOS_BUNDLE_ID = "YT Slop DL"
SIGNING_CONFIG = "config.local.signing.json"


def macos_metadata(bundle: Path, version: str, identity: str | None):
    """Advertise the actual minimum OS required by all bundled native code."""
    from macholib.MachO import MachO
    from macholib.mach_o import LC_BUILD_VERSION, LC_VERSION_MIN_MACOSX
    minimum = 0
    magics = {b'\xfe\xed\xfa\xce', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf',
              b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca',
              b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca'}
    for path in bundle.rglob('*'):
        if path.is_symlink() or not path.is_file():
            continue
        with path.open('rb') as stream:
            if stream.read(4) not in magics:
                continue
        for header in MachO(str(path)).headers:
            for load, command, _ in header.commands:
                if load.cmd == LC_BUILD_VERSION:
                    minimum = max(minimum, command.minos)
                elif load.cmd == LC_VERSION_MIN_MACOSX:
                    minimum = max(minimum, command.version)
    if not minimum:
        raise RuntimeError('No macOS deployment target found in the bundle.')
    minimum_version = f'{minimum >> 16}.{(minimum >> 8) & 255}.{minimum & 255}'
    path = bundle / 'Contents' / 'Info.plist'
    info = plistlib.loads(path.read_bytes())
    info.update(CFBundleShortVersionString=version, CFBundleVersion=version,
                LSMinimumSystemVersion=minimum_version)
    path.write_bytes(plistlib.dumps(info))
    command = ['codesign', '--force', '--sign', identity or '-']
    if identity:
        command += ['--options=runtime', '--timestamp']
    subprocess.run([*command, str(bundle)], check=True)
    return minimum_version


def make_dmg(root: Path, bundle: Path, version: str, architecture: str, minimum: str):
    image = root / 'dist' / f'YT-Slop-DL-{version}-macOS-{architecture}.dmg'
    with tempfile.TemporaryDirectory(prefix='dmg-', dir=root / 'build') as temporary:
        stage = Path(temporary)
        subprocess.run(['ditto', str(bundle), str(stage / bundle.name)], check=True)
        (stage / 'Applications').symlink_to('/Applications')
        instructions = (root / 'packaging' / 'Install.txt').read_text(encoding='utf-8')
        (stage / 'Install.txt').write_text(instructions.format(version=version, architecture=architecture,
                                                             minimum=minimum), encoding='utf-8')
        subprocess.run(['hdiutil', 'create', '-volname', f'YT Slop DL {version}', '-srcfolder', str(stage),
                        '-format', 'UDZO', '-fs', 'HFS+', '-ov', str(image)], check=True)
    subprocess.run(['hdiutil', 'verify', str(image)], check=True)
    print('Shareable disk image:', image)


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
    parser.add_argument("--target-arch", choices=("arm64", "x86_64", "universal2"), help="macOS target architecture")
    parser.add_argument("--dmg", action="store_true", help="Package a shareable macOS disk image")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    system = platform.system()
    if system != "Darwin" and (args.dmg or args.target_arch):
        parser.error('--dmg and --target-arch require macOS')
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
    version = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
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
        if args.target_arch:
            signing_args += ["--target-architecture", args.target_arch]
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", "--onedir", "--windowed",
        "--name", "YT Slop DL", "--paths", str(root),
        "--icon", str(icon), "--add-data", f"{root / 'app' / 'assets'}{os.pathsep}app/assets",
        *signing_args, str(root / "app" / "main.py"),
    ], cwd=root, check=True)
    if system == "Darwin":
        bundle = root / "dist" / "YT Slop DL.app"
        shutil.copytree(root / 'packaging' / 'licenses', bundle / 'Contents' / 'Resources' / 'licenses', dirs_exist_ok=True)
        minimum = macos_metadata(bundle, version, identity)
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(bundle)], check=True)
        if identity:
            config = root / SIGNING_CONFIG
            temporary = config.with_suffix(".tmp")
            temporary.write_text(json.dumps({"identity": identity}) + "\n", encoding="utf-8")
            temporary.replace(config)
        if args.dmg:
            make_dmg(root, bundle, version, args.target_arch or platform.machine(), minimum)
    if system == "Linux":
        desktop = root / "dist" / "YT Slop DL.desktop"
        desktop.write_text(
            '[Desktop Entry]\nType=Application\nName=YT Slop DL\n'
            f'Exec="{root / "dist" / "YT Slop DL" / "YT Slop DL"}"\n'
            f'Icon={icon}\nTerminal=false\nCategories=AudioVideo;\n', encoding="utf-8")
        desktop.chmod(0o755)
    print("Built desktop application in", root / "dist")
    if args.installer:
        subprocess.run([compiler, f"/DAppVersion={version}", str(root / "packaging" / "windows.iss")],
                       cwd=root, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
