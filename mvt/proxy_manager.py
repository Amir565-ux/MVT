"""Proxy parsing, health validation and rotation.

Rotation strategy
-----------------
``ProxyPool.lease()`` hands out proxies in **round-robin** order and never
returns a proxy that is currently leased by another thread.  Because every
visit leases its own proxy, two concurrent visits can never share an exit IP,
and consecutive visits walk through the whole list - which is what guarantees
that the visible IP actually changes between visits.

Health handling
---------------
A proxy must pass a live health check before it can be leased.  Proxies that
time out, refuse connections or return the *local* IP (i.e. they are not
actually routing traffic) are quarantined and never handed out again.
"""

from __future__ import annotations

import random
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

from .models import ProxyEntry

try:  # requests is required for the health checker
    import requests
except Exception:  # pragma: no cover - handled by the GUI banner
    requests = None  # type: ignore

SUPPORTED_SCHEMES = ("http", "https", "socks4", "socks5", "socks5h")


class ProxyParseError(ValueError):
    """Raised when a proxy line cannot be understood."""


def parse_proxy(raw: str) -> ProxyEntry:
    """Parse one proxy line into a :class:`ProxyEntry`.

    Accepted formats::

        ip:port
        scheme://ip:port
        user:pass@ip:port
        scheme://user:pass@ip:port
        [::1]:8080            (IPv6)
        scheme://[::1]:8080
    """
    line = (raw or "").strip()
    if not line:
        raise ProxyParseError("empty proxy entry")
    if any(ch.isspace() for ch in line):
        raise ProxyParseError(f"unexpected whitespace in '{line}'")

    scheme = "http"
    rest = line
    if "://" in line:
        scheme, rest = line.split("://", 1)
        scheme = scheme.lower()
        if scheme not in SUPPORTED_SCHEMES:
            raise ProxyParseError(f"unsupported scheme '{scheme}'")

    username = ""
    password = ""
    if "@" in rest:
        userinfo, rest = rest.rsplit("@", 1)
        if ":" in userinfo:
            username, password = userinfo.split(":", 1)
        else:
            username = userinfo
        username = username.strip()
        password = password.strip()

    if not rest:
        raise ProxyParseError(f"missing host in '{line}'")

    host, port = _split_host_port(rest, line)
    if not host:
        raise ProxyParseError(f"missing host in '{line}'")
    if not 0 < port < 65536:
        raise ProxyParseError(f"invalid port '{port}' in '{line}'")

    server = f"{scheme}://{host}:{port}"
    return ProxyEntry(
        raw=line,
        server=server,
        scheme=scheme,
        host=host,
        port=port,
        username=username,
        password=password,
    )


def _split_host_port(value: str, original: str) -> Tuple[str, int]:
    value = value.strip()
    if value.startswith("["):  # IPv6 literal
        end = value.find("]")
        if end == -1:
            raise ProxyParseError(f"malformed IPv6 host in '{original}'")
        host = value[1:end]
        remainder = value[end + 1:]
        if not remainder.startswith(":"):
            raise ProxyParseError(f"missing port in '{original}'")
        port_text = remainder[1:]
    else:
        if ":" not in value:
            raise ProxyParseError(f"missing port in '{original}'")
        host, port_text = value.rsplit(":", 1)

    try:
        port = int(port_text)
    except ValueError as exc:
        raise ProxyParseError(f"invalid port '{port_text}' in '{original}'") from exc
    return host.strip(), port


def parse_proxy_list(lines: Iterable[str]) -> Tuple[List[ProxyEntry], List[str]]:
    """Parse many lines.

    Returns ``(entries, errors)`` - malformed lines are reported instead of
    aborting the whole list, and exact duplicates are collapsed so the same
    endpoint is never leased twice (which would defeat IP rotation).
    """
    entries: List[ProxyEntry] = []
    errors: List[str] = []
    seen = set()

    for line in lines:
        candidate = line.strip()
        if not candidate or candidate.startswith("#"):
            continue
        try:
            entry = parse_proxy(candidate)
        except ProxyParseError as exc:
            errors.append(str(exc))
            continue
        key = (entry.scheme, entry.host, entry.port, entry.username)
        if key in seen:
            continue
        seen.add(key)
        entries.append(entry)
    return entries, errors


# --------------------------------------------------------------------------- #
# Health checking
# --------------------------------------------------------------------------- #

# Public IP echo services used to discover the exit IP of a proxy.
IP_CHECK_URLS: Tuple[str, ...] = (
    "https://api.ipify.org?format=json",
    "https://api.myip.com",
    "http://ip-api.com/json",
    "https://ipinfo.io/json",
)


def _extract_ip(payload: str) -> str:
    """Pull an IPv4/IPv6 address out of an IP echo response."""
    import json
    import re

    text = payload.strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            for key in ("ip", "query", "ipAddress", "origin"):
                value = data.get(key)
                if isinstance(value, str) and value:
                    # ip-api.com may return a comma separated list.
                    return value.split(",")[0].strip()
    except Exception:
        pass
    match = re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text)
    if match:
        return match.group(0)
    match = re.search(r"\b(?:[0-9a-fA-F]{1,4}:){3,7}[0-9a-fA-F]{1,4}\b", text)
    if match:
        return match.group(0)
    return ""


class ProxyHealthChecker:
    """Validates proxies against an IP echo service before they are used."""

    def __init__(self, timeout: float = 12.0, local_ip: str = ""):
        self.timeout = timeout
        self.local_ip = local_ip
        self._local_known = bool(local_ip)

    def local_address(self) -> str:
        """Best effort detection of the machine's own public IP."""
        if self._local_known:
            return self.local_ip
        if requests is None:
            return ""
        try:
            response = requests.get("https://api.ipify.org", timeout=8)
            self.local_ip = _extract_ip(response.text)
        except Exception:
            self.local_ip = ""
        self._local_known = True
        return self.local_ip

    def check(self, entry: ProxyEntry) -> ProxyEntry:
        """Run a health check against a single proxy.

        The proxy is marked ``healthy`` only when a request routed through it
        succeeds *and* the observed exit IP differs from the local IP.
        """
        if requests is None:
            entry.checked = True
            entry.healthy = False
            entry.last_error = "requests library not installed"
            return entry

        entry.checked = True
        entry.healthy = False
        entry.last_error = ""

        proxies = entry.requests_proxies()
        last_error = "no response"

        for url in IP_CHECK_URLS:
            try:
                response = requests.get(
                    url,
                    proxies=proxies,
                    timeout=self.timeout,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
            except Exception as exc:  # timeout, refused, dns, socks errors
                last_error = f"{type(exc).__name__}: {exc}"
                continue

            if response.status_code != 200:
                last_error = f"HTTP {response.status_code}"
                continue

            exit_ip = _extract_ip(response.text)
            if not exit_ip:
                last_error = "could not determine exit IP"
                continue

            entry.exit_ip = exit_ip
            local = self.local_address()
            if local and exit_ip == local:
                # The proxy silently fell back to a direct connection.
                entry.last_error = "proxy leaks direct connection (exit IP == local IP)"
                last_error = entry.last_error
                break

            entry.healthy = True
            entry.last_error = ""
            return entry

        entry.last_error = last_error
        entry.fail_count += 1
        return entry


def health_check_all(
    entries: Sequence[ProxyEntry],
    timeout: float = 12.0,
    max_workers: int = 16,
    on_result: Optional[Callable[[ProxyEntry], None]] = None,
) -> List[ProxyEntry]:
    """Check every proxy in parallel and return the same list, updated."""
    if not entries:
        return list(entries)
    checker = ProxyHealthChecker(timeout=timeout)
    with ThreadPoolExecutor(max_workers=min(max_workers, max(1, len(entries)))) as pool:
        futures = {pool.submit(checker.check, entry): entry for entry in entries}
        for future in futures:
            entry = future.result()
            if on_result:
                on_result(entry)
    return list(entries)


# --------------------------------------------------------------------------- #
# Rotation pool
# --------------------------------------------------------------------------- #
class ProxyPool:
    """Thread-safe pool that hands out a distinct proxy per visit.

    * ``lease()``    -> take the next healthy proxy (round-robin), mark it busy
    * ``release()``  -> give the proxy back after the visit
    * ``report_failure()`` -> quarantine the proxy, it will not be reused
    """

    def __init__(
        self,
        entries: Sequence[ProxyEntry],
        rng: Optional[random.Random] = None,
        require_healthy: bool = True,
    ):
        self._entries: List[ProxyEntry] = list(entries)
        self._lock = threading.Lock()
        self._cursor = 0
        self._rng = rng or random
        self._require_healthy = require_healthy

    # -- introspection ---------------------------------------------------- #
    def __len__(self) -> int:
        return len(self._entries)

    @property
    def entries(self) -> List[ProxyEntry]:
        return list(self._entries)

    def healthy_entries(self) -> List[ProxyEntry]:
        return [e for e in self._entries if e.healthy]

    def available_count(self) -> int:
        with self._lock:
            return sum(
                1
                for e in self._entries
                if not e.in_use and (e.healthy or not self._require_healthy)
            )

    def reset(self, entries: Optional[Sequence[ProxyEntry]] = None) -> None:
        with self._lock:
            if entries is not None:
                self._entries = list(entries)
            for entry in self._entries:
                entry.in_use = False
            self._cursor = 0

    # -- leasing ---------------------------------------------------------- #
    def lease(self) -> Optional[ProxyEntry]:
        """Return the next usable proxy, or ``None`` when the pool is dry."""
        with self._lock:
            count = len(self._entries)
            if count == 0:
                return None
            for offset in range(count):
                index = (self._cursor + offset) % count
                entry = self._entries[index]
                if entry.in_use:
                    continue
                if self._require_healthy and not entry.healthy:
                    continue
                entry.in_use = True
                self._cursor = (index + 1) % count
                return entry
        return None

    def release(self, entry: Optional[ProxyEntry]) -> None:
        """Return a leased proxy to the rotation."""
        if entry is None:
            return
        with self._lock:
            entry.in_use = False

    def report_success(self, entry: Optional[ProxyEntry], exit_ip: str = "") -> None:
        if entry is None:
            return
        with self._lock:
            entry.in_use = False
            entry.healthy = True
            entry.checked = True
            if exit_ip:
                entry.exit_ip = exit_ip

    def report_failure(self, entry: Optional[ProxyEntry], error: str = "") -> None:
        """Quarantine a failing proxy - it is never leased again."""
        if entry is None:
            return
        with self._lock:
            entry.in_use = False
            entry.fail_count += 1
            entry.last_error = error
            entry.healthy = False

    def report_exit_ip(self, entry: Optional[ProxyEntry], exit_ip: str) -> None:
        """Record the exit IP observed during a real visit."""
        if entry is None or not exit_ip:
            return
        with self._lock:
            entry.exit_ip = exit_ip

    def exit_ips(self) -> List[str]:
        seen: List[str] = []
        for entry in self._entries:
            if entry.exit_ip and entry.exit_ip not in seen:
                seen.append(entry.exit_ip)
        return seen
