"""User-Agent parsing and fingerprint generation.

The fingerprint engine turns a raw User-Agent string into a
:class:`~mvt.models.UserAgentProfile` whose viewport, device scale factor,
platform, locale and timezone all *match* the device class of the UA.  A
mobile UA therefore gets a 360x640 style viewport instead of 1920x1080, which
is one of the cheapest ways to look like a real device to fingerprinting
scripts.

An optional prefix can be used to force a device class explicitly::

    [mobile] Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) ...
"""

from __future__ import annotations

import random
import re
from typing import Dict, List, Optional, Sequence, Tuple

from .defaults import LOCALE_TIMEZONES
from .models import DeviceType, UserAgentProfile

# --------------------------------------------------------------------------- #
# Viewport pools per device class.
# --------------------------------------------------------------------------- #
MOBILE_VIEWPORTS: List[Tuple[int, int]] = [
    (360, 640),
    (360, 740),
    (375, 667),
    (390, 844),
    (393, 852),
    (412, 915),
    (414, 896),
    (360, 780),
]

TABLET_VIEWPORTS: List[Tuple[int, int]] = [
    (768, 1024),
    (800, 1280),
    (820, 1180),
    (834, 1194),
    (1024, 1366),
    (1180, 820),
]

DESKTOP_VIEWPORTS: List[Tuple[int, int]] = [
    (1920, 1080),
    (1536, 864),
    (1600, 900),
    (1366, 768),
    (1440, 900),
    (1280, 720),
    (2560, 1440),
    (1680, 1050),
]

# Rough platform hints, keyed by a substring of the UA.
_PLATFORM_HINTS: Sequence[Tuple[str, str]] = (
    ("Windows", "Win32"),
    ("Android", "Linux armv8l"),
    ("iPhone", "iPhone"),
    ("iPad", "iPad"),
    ("Macintosh", "MacIntel"),
    ("CrOS", "Linux x86_64"),
    ("Linux", "Linux x86_64"),
)

# Locale hints per platform/region keyword.
_LOCALE_HINTS: Sequence[Tuple[str, str, str]] = (
    ("Windows", "en-US", "America/New_York"),
    ("Macintosh", "en-US", "America/New_York"),
    ("Android", "en-US", "America/New_York"),
    ("iPhone", "en-US", "America/New_York"),
    ("iPad", "en-US", "America/New_York"),
    ("Linux", "en-US", "America/New_York"),
)


def classify_device(ua: str) -> DeviceType:
    """Derive the device class from a User-Agent string.

    ``Android`` without the ``Mobile`` token denotes a tablet, ``iPad`` is a
    tablet, everything else with a ``Mobile``/``iPhone`` token is a phone.
    """
    low = ua.lower()
    if "ipad" in low or "tablet" in low:
        return DeviceType.TABLET
    if "android" in low:
        return DeviceType.MOBILE if "mobile" in low else DeviceType.TABLET
    if "iphone" in low or "ipod" in low or "windows phone" in low:
        return DeviceType.MOBILE
    if "opera mini" in low or "opera mobi" in low:
        return DeviceType.MOBILE
    return DeviceType.DESKTOP


def strip_device_prefix(ua: str) -> Tuple[str, Optional[DeviceType]]:
    """Split an optional ``[mobile]`` / ``[tablet]`` / ``[desktop]`` prefix."""
    match = re.match(r"^\s*\[(mobile|tablet|desktop)\]\s*(.+)$", ua, re.IGNORECASE)
    if not match:
        return ua.strip(), None
    forced = {
        "mobile": DeviceType.MOBILE,
        "tablet": DeviceType.TABLET,
        "desktop": DeviceType.DESKTOP,
    }[match.group(1).lower()]
    return match.group(2).strip(), forced


def _viewport_for(device: DeviceType, rng: random.Random) -> Tuple[int, int]:
    pool = {
        DeviceType.MOBILE: MOBILE_VIEWPORTS,
        DeviceType.TABLET: TABLET_VIEWPORTS,
        DeviceType.DESKTOP: DESKTOP_VIEWPORTS,
    }[device]
    return rng.choice(pool)


def _scale_factor_for(device: DeviceType, rng: random.Random) -> float:
    if device is DeviceType.MOBILE:
        return rng.choice([2.0, 2.75, 3.0])
    if device is DeviceType.TABLET:
        return rng.choice([2.0, 2.5])
    return 1.0


def _platform_for(ua: str) -> str:
    for token, platform in _PLATFORM_HINTS:
        if token.lower() in ua.lower():
            return platform
    return "Win32"


def _locale_for(ua: str, rng: random.Random) -> Tuple[str, str]:
    """Pick a locale + timezone, biased towards the UA's own region."""
    for token, locale, tz in _LOCALE_HINTS:
        if token.lower() in ua.lower():
            return locale, tz
    locale, tz = rng.choice(LOCALE_TIMEZONES)
    return locale, tz


def build_profile(ua: str, rng: Optional[random.Random] = None) -> UserAgentProfile:
    """Build a full fingerprint profile for one User-Agent string."""
    rng = rng or random
    clean_ua, forced = strip_device_prefix(ua)
    device = forced or classify_device(clean_ua)

    width, height = _viewport_for(device, rng)
    locale, timezone = _locale_for(clean_ua, rng)

    return UserAgentProfile(
        ua=clean_ua,
        device=device,
        viewport={"width": width, "height": height},
        # A real browser reports a screen slightly larger than the viewport.
        screen={"width": width, "height": height},
        device_scale_factor=_scale_factor_for(device, rng),
        is_mobile=device is DeviceType.MOBILE,
        has_touch=device in (DeviceType.MOBILE, DeviceType.TABLET),
        platform=_platform_for(clean_ua),
        locale=locale,
        timezone_id=timezone,
    )


class FingerprintRotator:
    """Round-robin / random rotation over a list of User-Agent strings.

    ``assign()`` never returns the same UA twice in a row for the same
    rotator, which keeps the traffic pattern from looking like a single
    hard-coded client.
    """

    def __init__(self, user_agents: Sequence[str], rng: Optional[random.Random] = None):
        self._uas: List[str] = [ua.strip() for ua in user_agents if ua and ua.strip()]
        self._rng = rng or random
        self._index = 0
        self._last = -1

    @property
    def count(self) -> int:
        return len(self._uas)

    def assign(self) -> Optional[UserAgentProfile]:
        if not self._uas:
            return None
        if len(self._uas) == 1:
            return build_profile(self._uas[0], self._rng)

        # Pick a random index different from the previous one.
        index = self._rng.randrange(len(self._uas))
        if index == self._last:
            index = (index + 1) % len(self._uas)
        self._last = index
        self._index = (self._index + 1) % len(self._uas)
        return build_profile(self._uas[index], self._rng)


class ReferrerRotator:
    """Rotation over a list of Referrer URLs."""

    def __init__(self, referrers: Sequence[str], rng: Optional[random.Random] = None):
        self._refs: List[str] = [r.strip() for r in referrers if r and r.strip()]
        self._rng = rng or random
        self._last = -1

    @property
    def count(self) -> int:
        return len(self._refs)

    def assign(self) -> Optional[str]:
        if not self._refs:
            return None
        index = self._rng.randrange(len(self._refs))
        if len(self._refs) > 1 and index == self._last:
            index = (index + 1) % len(self._refs)
        self._last = index
        return self._refs[index]


def default_profile() -> UserAgentProfile:
    """A plain desktop Chrome profile used when fingerprint rotation is off."""
    return build_profile(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        random,
    )
