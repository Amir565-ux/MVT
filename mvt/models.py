"""Shared data models for the Mass Website Visitor tool.

These dataclasses are the contract between the GUI layer and the traffic
engine.  They are intentionally plain (no Qt / Playwright types) so the engine
can be unit-tested or driven from a headless script.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class BrowserChoice(str, Enum):
    """Which browser engine Playwright should drive."""

    CHROME = "chrome"
    FIREFOX = "firefox"
    HEADLESS = "headless"

    @property
    def label(self) -> str:
        return {
            BrowserChoice.CHROME: "Chrome",
            BrowserChoice.FIREFOX: "Firefox",
            BrowserChoice.HEADLESS: "Headless (Chromium)",
        }[self]

    @property
    def is_headless(self) -> bool:
        return self is BrowserChoice.HEADLESS


class DeviceType(str, Enum):
    """Device class derived from a User-Agent string."""

    DESKTOP = "desktop"
    MOBILE = "mobile"
    TABLET = "tablet"


@dataclass
class ProxyEntry:
    """A single parsed proxy from the user supplied proxy list.

    ``server`` is the value handed to Playwright (``scheme://host:port``) while
    ``username`` / ``password`` are passed separately, because Chromium cannot
    authenticate against SOCKS proxies.
    """

    raw: str
    server: str
    scheme: str = "http"
    host: str = ""
    port: int = 0
    username: str = ""
    password: str = ""
    exit_ip: str = ""
    healthy: bool = False
    checked: bool = False
    in_use: bool = False
    fail_count: int = 0
    last_error: str = ""

    @property
    def display(self) -> str:
        """Compact ``host:port`` label used in log lines."""
        return f"{self.host}:{self.port}" if self.port else self.host

    @property
    def is_socks(self) -> bool:
        return self.scheme.startswith("socks")

    @property
    def has_credentials(self) -> bool:
        return bool(self.username)

    def playwright_proxy(self) -> Dict[str, str]:
        """Proxy dict for ``browser.new_context(proxy=...)``."""
        proxy: Dict[str, str] = {"server": self.server}
        if self.username:
            proxy["username"] = self.username
        if self.password:
            proxy["password"] = self.password
        return proxy

    def requests_url(self) -> str:
        """Proxy URL for the ``requests`` based health checker."""
        if self.username:
            return f"{self.scheme}://{self.username}:{self.password}@{self.host}:{self.port}"
        return self.server

    def requests_proxies(self) -> Dict[str, str]:
        """``requests`` proxy mapping.

        SOCKS schemes are rewritten to ``socks5h`` so DNS resolution also goes
        through the proxy instead of leaking locally.
        """
        url = self.requests_url()
        if self.scheme == "socks5":
            url = url.replace("socks5://", "socks5h://", 1)
        return {"http": url, "https": url}


@dataclass
class UserAgentProfile:
    """A User-Agent plus the viewport / platform traits that must match it."""

    ua: str
    device: DeviceType = DeviceType.DESKTOP
    viewport: Dict[str, int] = field(default_factory=lambda: {"width": 1920, "height": 1080})
    screen: Dict[str, int] = field(default_factory=lambda: {"width": 1920, "height": 1080})
    device_scale_factor: float = 1.0
    is_mobile: bool = False
    has_touch: bool = False
    platform: str = "Win32"
    locale: str = "en-US"
    timezone_id: str = "America/New_York"

    def context_kwargs(self, engine: BrowserChoice) -> Dict[str, Any]:
        """Playwright ``new_context()`` keyword arguments.

        ``is_mobile`` / ``has_touch`` are Chromium-only in Playwright and raise
        on Firefox, so they are filtered out for the Firefox engine.
        """
        kwargs: Dict[str, Any] = {
            "user_agent": self.ua,
            "viewport": dict(self.viewport),
            "screen": dict(self.screen),
            "device_scale_factor": self.device_scale_factor,
            "locale": self.locale,
            "timezone_id": self.timezone_id,
            "java_script_enabled": True,
            "ignore_https_errors": True,
        }
        if engine is not BrowserChoice.FIREFOX:
            kwargs["is_mobile"] = self.is_mobile
            kwargs["has_touch"] = self.has_touch
        return kwargs


@dataclass
class VisitResult:
    """Outcome of a single visit attempt."""

    index: int
    thread_id: int
    success: bool
    url: str = ""
    proxy: str = ""
    exit_ip: str = ""
    user_agent: str = ""
    referrer: str = ""
    status: str = ""
    duration: float = 0.0
    error: str = ""
    internal_clicks: int = 0
    exit_ip_error: str = ""
    ad_clicks: int = 0
    ad_targets: List[str] = field(default_factory=list)
    ad_landing_urls: List[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.success:
            proxy_part = f"Using Proxy [{self.proxy}]" if self.proxy else "Direct Connection"
            ip_part = f" -> {self.exit_ip}" if self.exit_ip else ""
            return f"{proxy_part}{ip_part} - Success"
        return f"Proxy Failed - {self.error}" if self.proxy else f"Failed - {self.error}"


@dataclass
class RunConfig:
    """Everything the engine needs for one run."""

    url: str = ""
    visits: int = 10
    threads: int = 10
    engine: BrowserChoice = BrowserChoice.HEADLESS

    use_referrers: bool = True
    use_user_agents: bool = True
    use_proxies: bool = True

    referrers: List[str] = field(default_factory=list)
    user_agents: List[str] = field(default_factory=list)
    proxies: List[str] = field(default_factory=list)

    min_stay: float = 5.0
    max_stay: float = 15.0
    navigation_timeout: float = 30.0
    proxy_timeout: float = 12.0

    verify_exit_ip: bool = True
    respect_robots: bool = True
    click_internal_links: bool = False
    max_internal_navs: int = 1

    # --- ad interaction (ad QA / landing-page verification) --------------- #
    click_ads: bool = False
    max_ad_clicks: int = 1
    ad_click_delay: float = 1.5

    # --- pacing ----------------------------------------------------------- #
    fast_mode: bool = False
    speed: float = 1.0

    def validate(self) -> List[str]:
        """Return a list of human readable problems (empty == valid)."""
        problems: List[str] = []
        if not self.url or not self.url.strip():
            problems.append("Target URL is empty.")
        elif not self.url.lower().startswith(("http://", "https://")):
            problems.append("Target URL must start with http:// or https://")
        if self.visits < 1:
            problems.append("Visits must be at least 1.")
        if self.threads < 1:
            problems.append("Threads must be at least 1.")
        if self.threads > 50:
            problems.append("Threads must be 50 or fewer.")
        if self.max_stay < self.min_stay:
            problems.append("Max stay must be greater than or equal to min stay.")
        if self.use_proxies and not self.proxies:
            problems.append("Proxy mode is enabled but the proxy list is empty.")
        if self.max_ad_clicks < 1:
            problems.append("Max ad clicks must be at least 1.")
        if self.speed <= 0:
            problems.append("Speed must be greater than 0.")
        return problems


@dataclass
class RunStats:
    """Mutable counters shared by the engine threads."""

    total: int = 0
    completed: int = 0
    success: int = 0
    failed: int = 0
    ips: List[str] = field(default_factory=list)

    @property
    def unique_ips(self) -> List[str]:
        return sorted({ip for ip in self.ips if ip})

    def as_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total,
            "completed": self.completed,
            "success": self.success,
            "failed": self.failed,
            "unique_ips": len(self.unique_ips),
        }
