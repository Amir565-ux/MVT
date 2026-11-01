"""Mass Website Visitor - a modular, multi-threaded web traffic generator.

The package is split into focused modules:

``models``          dataclasses shared by the engine and the GUI
``defaults``        built-in User-Agent / referrer lists
``fingerprint``     UA parsing, viewport matching, UA + referrer rotation
``proxy_manager``   proxy parsing, health validation, round-robin rotation
``visitor``         the Playwright visit with human-like behaviour
``engine``          the ThreadPoolExecutor orchestration
``logbus``          thread-safe log plumbing
``robots``          robots.txt compliance check
``config``          JSON persistence of the GUI settings
``browser_setup``   Playwright bootstrap / automatic browser installation
``gui``             the PyQt6 desktop interface
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
