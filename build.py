"""Build a local native desktop bundle on the current OS."""

import os
import platform
import subprocess
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
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

    system = platform.system()
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
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", "--onedir", "--windowed",
        "--name", "YT Slop DL", "--paths", str(root),
        "--icon", str(icon), "--add-data", f"{root / 'app' / 'assets'}{os.pathsep}app/assets",
        str(root / "app" / "main.py"),
    ], cwd=root, check=True)
    if system == "Linux":
        desktop = root / "dist" / "YT Slop DL.desktop"
        desktop.write_text(
            '[Desktop Entry]\nType=Application\nName=YT Slop DL\n'
            f'Exec="{root / "dist" / "YT Slop DL" / "YT Slop DL"}"\n'
            f'Icon={icon}\nTerminal=false\nCategories=AudioVideo;\n', encoding="utf-8")
        desktop.chmod(0o755)
    print("Built desktop application in", root / "dist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
