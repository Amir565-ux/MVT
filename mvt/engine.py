"""The threaded traffic engine.

``TrafficEngine`` owns the whole run:

* validates the configuration,
* optionally checks robots.txt,
* health-checks every proxy (failed proxies are quarantined and never used),
* spins up ``threads`` workers from a ``ThreadPoolExecutor``,
* rotates the proxy pool, the User-Agent list and the referrer list per visit,
* reports every result back to the GUI through callbacks.

Each worker pulls the next visit index from a queue, so a single thread can
perform many visits while the others are still busy.
"""

from __future__ import annotations

import random
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

from .fingerprint import FingerprintRotator, ReferrerRotator, default_profile
from .logbus import LogBus
from .models import BrowserChoice, ProxyEntry, RunConfig, RunStats, VisitResult
from .proxy_manager import (
    ProxyPool,
    health_check_all,
    parse_proxy_list,
)
from .robots import check_robots
from .visitor import BrowserPool, Visitor


class TrafficEngine:
    """Runs a traffic campaign with a pool of browser workers."""

    def __init__(
        self,
        config: RunConfig,
        log: Optional[LogBus] = None,
        on_result: Optional[Callable[[VisitResult], None]] = None,
        on_progress: Optional[Callable[[RunStats], None]] = None,
        on_finished: Optional[Callable[[RunStats], None]] = None,
    ):
        self.config = config
        self.log = log or LogBus()
        self.on_result = on_result
        self.on_progress = on_progress
        self.on_finished = on_finished

        self.stop_event = threading.Event()
        self.stats = RunStats(total=config.visits)
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._browsers = BrowserPool(config.engine)
        self._pool: Optional[ProxyPool] = None
        self._rng = random.Random()
        self._started_at = 0.0

    # -- public API -------------------------------------------------------- #
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        """Start the run in a background thread. Returns ``False`` on refusal."""
        if self.running:
            return False

        problems = self.config.validate()
        if problems:
            for problem in problems:
                self.log.error(problem)
            return False

        self.stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="traffic-engine", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        """Ask the workers to stop as soon as possible."""
        if self.running:
            self.log.warn("Stop requested - finishing current visits...")
        self.stop_event.set()

    def wait(self, timeout: Optional[float] = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    # -- internals --------------------------------------------------------- #
    def _run(self) -> None:
        self._started_at = time.monotonic()
        config = self.config
        self.stats = RunStats(total=config.visits)

        try:
            if not self._prepare_proxies():
                return
            if not self._check_robots():
                return
            self._execute()
        except Exception as exc:  # pragma: no cover - defensive
            self.log.error(f"Engine crashed: {type(exc).__name__}: {exc}")
        finally:
            self._browsers.close_all()
            elapsed = time.monotonic() - self._started_at
            self.log.info(
                f"Run finished in {elapsed:.1f}s - "
                f"success: {self.stats.success}, failed: {self.stats.failed}"
            )
            if self.on_finished:
                try:
                    self.on_finished(self.stats)
                except Exception:
                    pass

    # -- preparation ------------------------------------------------------- #
    def _prepare_proxies(self) -> bool:
        """Parse and health-check the proxy list. Returns False when unusable."""
        config = self.config
        self._pool = None

        if not config.use_proxies:
            self.log.info("Proxy mode disabled - using direct connections.")
            return True

        entries, errors = parse_proxy_list(config.proxies)
        for error in errors:
            self.log.warn(f"Skipped invalid proxy line: {error}")

        if not entries:
            self.log.error("No usable proxies found in the proxy list.")
            return False

        self.log.info(f"Checking {len(entries)} proxy/proxies for health...")
        checked: List[ProxyEntry] = health_check_all(
            entries,
            timeout=config.proxy_timeout,
            on_result=lambda entry: self._log_proxy_check(entry),
        )

        healthy = [entry for entry in checked if entry.healthy]
        broken = [entry for entry in checked if not entry.healthy]
        for entry in broken:
            self.log.warn(f"Proxy [{entry.display}] failed health check - {entry.last_error}")

        if not healthy:
            self.log.error(
                "None of the proxies passed the health check. "
                "Fix the list or disable proxy mode."
            )
            return False

        self.log.info(
            f"{len(healthy)} of {len(checked)} proxies are healthy "
            f"(exit IPs: {', '.join(sorted({e.exit_ip for e in healthy if e.exit_ip})[:6])})"
        )
        self._warn_about_socks(healthy)
        self._pool = ProxyPool(healthy, rng=self._rng, require_healthy=True)
        return True

    def _warn_about_socks(self, entries: List[ProxyEntry]) -> None:
        """Chromium cannot authenticate against SOCKS proxies."""
        if self.config.engine is BrowserChoice.FIREFOX:
            return
        authenticated = [e for e in entries if e.is_socks and e.has_credentials]
        if authenticated:
            self.log.warn(
                f"{len(authenticated)} SOCKS proxy/proxies carry credentials, but Chromium "
                "cannot authenticate SOCKS proxies - switch to Firefox mode for those.",
            )

    def _log_proxy_check(self, entry: ProxyEntry) -> None:
        if entry.healthy:
            self.log.info(f"Proxy [{entry.display}] OK - exit IP {entry.exit_ip}")
        else:
            self.log.warn(f"Proxy [{entry.display}] failed - {entry.last_error}")

    def _check_robots(self) -> bool:
        if not self.config.respect_robots:
            return True
        verdict = check_robots(self.config.url)
        if verdict.allowed:
            self.log.info(f"robots.txt check: {verdict.describe()}")
            return True
        self.log.error(
            f"Aborting: {verdict.describe()}. "
            "Disable 'Respect robots.txt' only if you own the target site."
        )
        return False

    # -- execution --------------------------------------------------------- #
    def _effective_settings(self) -> Dict[str, Any]:
        """Apply the fast-mode preset on top of the user's settings.

        Fast mode shortens the dwell time, drops the extra exit-IP request
        and doubles the pace of the simulated behaviour.  Explicit user
        values are never overwritten, only capped.
        """
        config = self.config
        settings: Dict[str, Any] = {
            "min_stay": config.min_stay,
            "max_stay": config.max_stay,
            "speed": max(config.speed, 0.1),
            "verify_exit_ip": config.verify_exit_ip,
        }
        if config.fast_mode:
            settings["min_stay"] = min(config.min_stay, 1.0)
            settings["max_stay"] = min(config.max_stay, 3.0)
            settings["speed"] = max(config.speed, 2.0)
            settings["verify_exit_ip"] = False
        return settings

    def _execute(self) -> None:
        config = self.config
        work: "queue.Queue[int]" = queue.Queue()
        for index in range(config.visits):
            work.put(index)

        ua_rotator = FingerprintRotator(
            config.user_agents if config.use_user_agents else [], rng=self._rng
        )
        ref_rotator = ReferrerRotator(
            config.referrers if config.use_referrers else [], rng=self._rng
        )

        self.log.info(
            f"Starting {config.visits} visits across {config.threads} thread(s) "
            f"[{config.engine.label}]"
        )

        workers = max(1, min(config.threads, 50, config.visits))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="visit") as pool:
            futures = [
                pool.submit(self._worker, worker_id, work, ua_rotator, ref_rotator)
                for worker_id in range(1, workers + 1)
            ]
            for future in futures:
                try:
                    future.result()
                except Exception as exc:  # pragma: no cover - defensive
                    self.log.error(f"Worker crashed: {type(exc).__name__}: {exc}")

    def _worker(
        self,
        worker_id: int,
        work: "queue.Queue[int]",
        ua_rotator: FingerprintRotator,
        ref_rotator: ReferrerRotator,
    ) -> None:
        """Worker loop: lease a proxy, build a fingerprint, perform the visit."""
        config = self.config
        visitor = Visitor(config.engine, self._browsers)
        rng = random.Random()
        settings = self._effective_settings()

        while not self.stop_event.is_set():
            try:
                index = work.get_nowait()
            except queue.Empty:
                break

            try:
                profile = ua_rotator.assign() or default_profile()
                referrer = ref_rotator.assign()

                proxy: Optional[ProxyEntry] = None
                if self._pool is not None:
                    proxy = self._pool.lease()
                    if proxy is None:
                        self._record(
                            VisitResult(
                                index=index,
                                thread_id=worker_id,
                                success=False,
                                url=config.url,
                                error="no healthy proxy available",
                            )
                        )
                        continue

                if proxy is not None:
                    self.log.info(
                        f"Thread #{worker_id}: Using Proxy [{proxy.display}]",
                        thread_id=worker_id,
                    )

                result = visitor.visit(
                    url=config.url,
                    profile=profile,
                    proxy=proxy,
                    referrer=referrer,
                    min_stay=settings["min_stay"],
                    max_stay=settings["max_stay"],
                    rng=rng,
                    stop_event=self.stop_event,
                    verify_exit_ip=settings["verify_exit_ip"] and proxy is not None,
                    navigation_timeout=config.navigation_timeout,
                    click_internal_links=config.click_internal_links,
                    max_internal_navs=config.max_internal_navs,
                    thread_id=worker_id,
                    index=index,
                    click_ads=config.click_ads,
                    max_ad_clicks=config.max_ad_clicks,
                    ad_click_delay=config.ad_click_delay,
                    speed=settings["speed"],
                )

                if proxy is not None:
                    if result.success:
                        self._pool.report_success(proxy, result.exit_ip)
                    else:
                        self._pool.report_failure(proxy, result.error)
                        self.log.warn(
                            f"Thread #{worker_id}: Proxy Failed - Retrying ({result.error})",
                            thread_id=worker_id,
                        )

                if result.success:
                    suffix = f" - {result.status} in {result.duration:.1f}s"
                    if result.exit_ip:
                        suffix += f" [exit {result.exit_ip}]"
                    self.log.success(
                        f"Thread #{worker_id}: Using Proxy "
                        f"[{result.proxy or 'direct'}] - Success{suffix}",
                        thread_id=worker_id,
                    )
                    if result.exit_ip_error:
                        self.log.warn(
                            f"Thread #{worker_id}: Exit IP could not be verified - "
                            f"{result.exit_ip_error}",
                            thread_id=worker_id,
                        )
                    for ad_index, label in enumerate(result.ad_targets):
                        landing = (
                            result.ad_landing_urls[ad_index]
                            if ad_index < len(result.ad_landing_urls)
                            else ""
                        )
                        landing_part = f" -> {landing}" if landing else " (no landing page)"
                        self.log.info(
                            f"Thread #{worker_id}: Ad clicked [{label}]{landing_part}",
                            thread_id=worker_id,
                        )
                else:
                    self.log.error(
                        f"Thread #{worker_id}: Visit failed - {result.error}",
                        thread_id=worker_id,
                    )

                self._record(result)

            except Exception as exc:  # pragma: no cover - defensive
                self.log.error(
                    f"Thread #{worker_id}: Unexpected error - {type(exc).__name__}: {exc}",
                    thread_id=worker_id,
                )
            finally:
                work.task_done()

        self._browsers.close()

    def _record(self, result: VisitResult) -> None:
        with self._lock:
            self.stats.completed += 1
            if result.success:
                self.stats.success += 1
            else:
                self.stats.failed += 1
            if result.exit_ip:
                self.stats.ips.append(result.exit_ip)
        if self.on_result:
            try:
                self.on_result(result)
            except Exception:
                pass
        if self.on_progress:
            try:
                self.on_progress(self.stats)
            except Exception:
                pass
