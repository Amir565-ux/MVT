"""Playwright bootstrap helpers.

Keeps the "install browsers automatically" requirement in one place so both
the GUI button and the engine start-up path can use it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Iterable, List, Optional, Tuple

# Browser binaries required per engine choice.
ENGINE_BROWSERS = {
    "chrome": ("chromium",),
    "firefox": ("firefox",),
    "headless": ("chromium",),
}


def playwright_available() -> bool:
    """True when the ``playwright`` Python package is importable."""
    try:
        import playwright  # noqa: F401
    except Exception:
        return False
    return True


def browser_cache_marker() -> str:
    """Path of a file that only exists after ``playwright install``."""
    try:
        from playwright._impl._driver import compute_driver_executable
    except Exception:
        return ""
    return compute_driver_executable()


def is_installed(engine: str = "chrome") -> bool:
    """Check whether the browser binaries for ``engine`` are present.

    For the Chromium based engines a locally installed Chrome / Edge counts as
    installed too, because Playwright can drive it through the ``channel``
    option when the bundled binary is unavailable.
    """
    if not playwright_available():
        return False

    browsers = ENGINE_BROWSERS.get(engine, ("chromium",))
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return False

    try:
        with sync_playwright() as pw:
            for name in browsers:
                browser_type = getattr(pw, name)
                path = browser_type.executable_path
                if not path or not os.path.exists(path):
                    if name == "chromium" and system_chrome_available():
                        continue
                    return False
        return True
    except Exception:
        return False


def install_browsers(
    engine: str = "chrome",
    on_output: Optional[callable] = None,
) -> Tuple[bool, str]:
    """Run ``python -m playwright install`` for the browsers of ``engine``.

    Returns ``(ok, message)``.  The subprocess output is streamed to
    ``on_output`` when provided so the GUI can show progress.
    """
    if not playwright_available():
        return False, "The 'playwright' package is not installed. Run: pip install playwright"

    browsers: List[str] = list(ENGINE_BROWSERS.get(engine, ("chromium",)))
    cmd = [sys.executable, "-m", "playwright", "install", *browsers]
    env = dict(os.environ)

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
    except Exception as exc:
        return False, f"Could not start installer: {exc}"

    lines: List[str] = []
    assert process.stdout is not None
    for line in process.stdout:
        line = line.rstrip()
        if not line:
            continue
        lines.append(line)
        if on_output:
            on_output(line)
    code = process.wait()
    if code == 0:
        return True, "Installed: " + ", ".join(browsers)
    tail = "\n".join(lines[-6:])
    return False, f"playwright install failed (exit {code})\n{tail}"


def chromium_stealth_args() -> List[str]:
    """Launch flags that reduce the obvious automation fingerprints."""
    return [
        "--disable-blink-features=AutomationControlled",
        "--disable-features=IsolateOrigins,site-per-process",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--disable-popup-blocking",
        "--password-store=basic",
        "--use-mock-keychain",
    ]


# --------------------------------------------------------------------------- #
# System browser fallback
# --------------------------------------------------------------------------- #
# When `playwright install` is unavailable (locked-down network, corporate
# proxy, ...) the bundled binaries cannot be downloaded.  Playwright can still
# drive a Chrome / Edge that is already installed on the machine.
SYSTEM_CHROME_PATHS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


def system_chrome_path() -> str:
    """Return the path of an installed Chrome/Edge, or an empty string."""
    for path in SYSTEM_CHROME_PATHS:
        if os.path.exists(path):
            return path
    return ""


def system_chrome_available() -> bool:
    """True when a Chrome/Edge binary is present on this machine."""
    return bool(system_chrome_path())


def available_channels() -> List[str]:
    """Playwright channels that can be used as a fallback."""
    channels: List[str] = []
    for path in SYSTEM_CHROME_PATHS:
        if os.path.exists(path):
            if "Edge" in path:
                channels.append("msedge")
            else:
                channels.append("chrome")
                break
    return channels
