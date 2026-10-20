"""Application and frozen-worker entry point."""

import sys


def main() -> int:
    if "--worker" in sys.argv:
        from app.worker import main as worker_main
        return worker_main()

    import argparse
    from pathlib import Path
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication
    from app.window import Window

    parser = argparse.ArgumentParser(description="YT Slop DL")
    parser.add_argument("--language", choices=("ru", "uk", "en"), help="Override saved/system UI language for this launch")
    args = parser.parse_args()
    application = QApplication([sys.argv[0]])
    application.setApplicationName("YT Slop DL")
    application.setApplicationVersion("0.1.0")
    application.setOrganizationName("YT Slop DL")
    application.setWindowIcon(QIcon(str(Path(__file__).parent / "assets" / "icon.png")))
    window = Window(args.language)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
