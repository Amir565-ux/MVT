#!/usr/bin/env python3
"""Entry point for the Mass Website Visitor traffic generator.

Usage::

    python main.py                 # start the desktop GUI
    python main.py --check         # verify the installation and exit
    python main.py --install-browsers

The GUI requires PyQt6; the engine itself only needs Playwright.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow "python main.py" from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mvt import browser_setup  # noqa: E402


def _check() -> int:
    """Print an installation report."""
    print("Mass Website Visitor - installation check")
    print("-" * 46)

    ok = True

    try:
        import PyQt6  # noqa: F401

        print("[OK]   PyQt6 GUI toolkit")
    except Exception:
        print("[MISS] PyQt6 GUI toolkit  -> pip install PyQt6")
        ok = False

    if browser_setup.playwright_available():
        print("[OK]   playwright package")
    else:
        print("[MISS] playwright package -> pip install playwright")
        ok = False

    for engine in ("chrome", "firefox", "headless"):
        if browser_setup.is_installed(engine):
            print(f"[OK]   Playwright browsers for '{engine}'")
        else:
            print(f"[MISS] Playwright browsers for '{engine}' -> playwright install")

    try:
        import requests  # noqa: F401

        print("[OK]   requests (proxy health checks)")
    except Exception:
        print("[MISS] requests -> pip install requests")

    try:
        import socks  # noqa: F401

        print("[OK]   PySocks (SOCKS proxy health checks)")
    except Exception:
        print("[WARN] PySocks missing -> pip install PySocks (needed for socks4/socks5 checks)")

    print("-" * 46)
    print("Ready." if ok else "Some components are missing - see the lines above.")
    return 0 if ok else 1


def _install_browsers(engine: str) -> int:
    ok, message = browser_setup.install_browsers(engine)
    print(message)
    return 0 if ok else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Mass Website Visitor - multi-threaded web traffic generator"
    )
    parser.add_argument(
        "--check", action="store_true", help="verify the installation and exit"
    )
    parser.add_argument(
        "--install-browsers",
        action="store_true",
        help="download the Playwright browser binaries and exit",
    )
    parser.add_argument(
        "--browser",
        default="chrome",
        choices=("chrome", "firefox", "headless"),
        help="which browser to install (default: chrome)",
    )
    args = parser.parse_args(argv)

    if args.check:
        return _check()
    if args.install_browsers:
        return _install_browsers(args.browser)

    try:
        from PyQt6.QtWidgets import QApplication
    except Exception:
        print(
            "PyQt6 is required for the GUI.\n"
            "Install it with:  pip install PyQt6\n"
            "Then run:        python main.py",
            file=sys.stderr,
        )
        return 1

    from mvt.gui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Mass Website Visitor")
    app.setApplicationVersion("1.0.0")

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
