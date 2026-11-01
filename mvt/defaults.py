"""Default payload lists: User-Agents and Referrers.

Both lists are plain strings so the user can freely edit them in the GUI text
areas and save them to the config file.
"""

from __future__ import annotations

from typing import List

# --------------------------------------------------------------------------- #
# User-Agents.  Mixed Windows / macOS / Linux desktops plus Android / iPhone
# handsets, so the fingerprint engine can also rotate device classes.
# --------------------------------------------------------------------------- #
DEFAULT_USER_AGENTS: List[str] = [
    # --- Desktop Chrome on Windows --------------------------------------- #
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.2478.67",
    "Mozilla/5.0 (Windows NT 6.1; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    # --- Desktop Chrome on macOS ------------------------------------------ #
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5_2) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    # --- Desktop Chrome on Linux ------------------------------------------ #
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    # --- Desktop Firefox --------------------------------------------------- #
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Vivaldi/6.5.3206.39",
    # --- Android ------------------------------------------------------------ #
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 12; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 11; moto g power (2021)) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Mobile Safari/537.36",
    # --- iOS ---------------------------------------------------------------- #
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPad; CPU OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
]

# --------------------------------------------------------------------------- #
# Referrers - search engines and social networks.  A Referer header is only
# accepted by browsers for http(s) origins, so bare domain names are ignored.
# --------------------------------------------------------------------------- #
DEFAULT_REFERRERS: List[str] = [
    "https://www.google.com/",
    "https://www.google.co.uk/",
    "https://www.google.ca/",
    "https://www.bing.com/",
    "https://duckduckgo.com/",
    "https://search.yahoo.com/",
    "https://search.brave.com/",
    "https://www.facebook.com/",
    "https://l.facebook.com/",
    "https://t.co/",
    "https://www.reddit.com/",
    "https://old.reddit.com/",
    "https://news.ycombinator.com/",
    "https://www.youtube.com/",
    "https://www.linkedin.com/",
    "https://news.ycombinator.com/item?id=1",
    "https://www.ecosia.org/",
    "https://yandex.com/",
    "https://www.startpage.com/",
    "https://mail.google.com/",
]

# --------------------------------------------------------------------------- #
# Localised locale / timezone pairs, picked to match the region of the UA.
# --------------------------------------------------------------------------- #
LOCALE_TIMEZONES = [
    ("en-US", "America/New_York"),
    ("en-GB", "Europe/London"),
    ("en-CA", "America/Toronto"),
    ("en-AU", "Australia/Sydney"),
    ("de-DE", "Europe/Berlin"),
    ("fr-FR", "Europe/Paris"),
    ("es-ES", "Europe/Madrid"),
    ("it-IT", "Europe/Rome"),
    ("pt-BR", "America/Sao_Paulo"),
    ("nl-NL", "Europe/Amsterdam"),
    ("ja-JP", "Asia/Tokyo"),
    ("ko-KR", "Asia/Seoul"),
    ("zh-CN", "Asia/Shanghai"),
    ("ru-RU", "Europe/Moscow"),
    ("hi-IN", "Asia/Kolkata"),
]
