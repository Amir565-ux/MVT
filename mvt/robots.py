"""robots.txt compliance check.

Before a run starts the target host is queried for ``/robots.txt`` and the
requested URL is tested against the rules.  When the target is disallowed for
our User-Agent the run is aborted with a clear message instead of silently
hammering a site that asked not to be crawled.
"""

from __future__ import annotations

from typing import Optional, Tuple
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

try:
    import requests
except Exception:  # pragma: no cover
    requests = None  # type: ignore

DEFAULT_USER_AGENT = "MassVisitorBot/1.0"


class RobotsVerdict:
    """Result of a robots.txt evaluation."""

    def __init__(self, allowed: bool, reason: str, url: str = "", fetched: bool = False):
        self.allowed = allowed
        self.reason = reason
        self.url = url
        self.fetched = fetched

    def __bool__(self) -> bool:  # pragma: no cover - convenience
        return self.allowed

    def describe(self) -> str:
        if not self.fetched:
            return f"robots.txt could not be fetched ({self.reason})"
        if self.allowed:
            return "robots.txt allows this URL"
        return f"robots.txt disallows this URL ({self.reason})"


def check_robots(
    url: str,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: float = 10.0,
) -> RobotsVerdict:
    """Check ``url`` against the target's robots.txt."""
    if requests is None:
        return RobotsVerdict(True, "requests is not installed", url=url, fetched=False)

    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return RobotsVerdict(True, "not a valid URL", url=url, fetched=False)

    robots_url = urljoin(f"{parsed.scheme}://{parsed.netloc}", "/robots.txt")
    parser = RobotFileParser()
    parser.set_url(robots_url)

    try:
        response = requests.get(
            robots_url,
            timeout=timeout,
            headers={"User-Agent": user_agent},
        )
    except Exception as exc:
        return RobotsVerdict(True, f"{type(exc).__name__}", url=robots_url, fetched=False)

    if response.status_code >= 400:
        # No robots.txt -> crawling is allowed by convention.
        return RobotsVerdict(
            True,
            f"robots.txt returned HTTP {response.status_code}",
            url=robots_url,
            fetched=True,
        )

    try:
        parser.parse(response.text.splitlines())
    except Exception as exc:
        return RobotsVerdict(True, f"unparsable robots.txt ({exc})", url=robots_url, fetched=True)

    allowed = parser.can_fetch(user_agent, url)
    reason = "disallow rule matched" if not allowed else "no disallow rule matched"
    return RobotsVerdict(allowed, reason, url=robots_url, fetched=True)
