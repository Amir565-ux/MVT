"""The visit engine: one browser visit, driven with human-like behaviour.

A *visit* consists of

1. creating a fresh browser context (new cookies / storage / fingerprint),
2. navigating to the target URL with a spoofed Referer,
3. simulating human behaviour - random mouse movement, random scrolling,
   a random dwell time and optionally one click on an internal link,
4. closing the context again.

Browsers are reused per worker thread (launching Chromium is expensive), but
the *context* - and therefore the fingerprint and the proxy - is recreated for
every single visit.
"""

from __future__ import annotations

import json
import random
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from .browser_setup import chromium_stealth_args
from . import browser_setup
from .models import BrowserChoice, ProxyEntry, UserAgentProfile, VisitResult

try:
    from playwright.sync_api import (
        Browser,
        BrowserContext,
        Error as PlaywrightError,
        Page,
        Playwright,
        TimeoutError as PlaywrightTimeoutError,
        sync_playwright,
    )

    PLAYWRIGHT_OK = True
except Exception:  # pragma: no cover - playwright not installed
    PLAYWRIGHT_OK = False
    PlaywrightError = Exception  # type: ignore
    PlaywrightTimeoutError = Exception  # type: ignore


IP_ECHO_URL = "https://api.ipify.org?format=json"

# --------------------------------------------------------------------------- #
# Ad slot detection
# --------------------------------------------------------------------------- #
# Hosts used by the common ad networks.  An iframe whose src points at one of
# them is an ad creative and is a safe click target.
AD_HOST_MARKERS = (
    "doubleclick.net",
    "googlesyndication.com",
    "googleadservices.com",
    "amazon-adsystem.com",
    "adnxs.com",
    "rubiconproject.com",
    "pubmatic.com",
    "openx.net",
    "criteo.com",
    "criteo.net",
    "taboola.com",
    "outbrain.com",
    "adform.net",
    "sharethrough.com",
    "adroll.com",
    "media.net",
    "adsrvr.org",
    "bidr.io",
    "ad-delivery.net",
    "smartadserver.com",
    "teads.tv",
    "yieldmo.com",
    "casalemedia.com",
    "gumgum.com",
)

# Container selectors that usually wrap an ad slot.  Kept deliberately narrow
# so ordinary page elements are never mistaken for an advertisement.
AD_SELECTORS = (
    "[data-ad-client]",
    "[data-ad-slot]",
    "[id^='ad-slot']",
    "[id^='adslot']",
    "[id^='ad-unit']",
    "[id^='ad_unit']",
    "[id^='div-gpt-ad']",
    "[class^='ad-slot']",
    "[class^='adslot']",
    "[class^='ad-unit']",
    "[class^='ad__']",
    "[id^='google_ads_']",
    "[id^='banner-']",
    "[class^='sponsor-']",
    "ins.adsbygoogle",
)

# A clickable ad slot has to be at least this big to be an advertisement.
MIN_AD_WIDTH = 250
MIN_AD_HEIGHT = 40

# One single round-trip collects every ad slot on the page and tags it with a
# marker attribute, so the click can be aimed at the exact element.
AD_DISCOVERY_JS = r"""
() => {
  const MIN_W = __MIN_W__;
  const MIN_H = __MIN_H__;
  const HOSTS = __HOSTS__;
  const SLOT_SELECTORS = __SLOTS__;

  const results = [];
  const seen = new Set();

  const measure = (el) => {
    const r = el.getBoundingClientRect();
    if (!r || r.width < MIN_W || r.height < MIN_H) return null;
    if (r.width <= 0 || r.height <= 0) return null;
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') return null;
    return r;
  };

  const add = (el, kind, label) => {
    const r = measure(el);
    if (!r) return;
    const key = Math.round(r.x / 8) + ':' + Math.round(r.y / 8) + ':' + Math.round(r.width / 8);
    if (seen.has(key)) return;
    seen.add(key);
    const index = results.length;
    el.setAttribute('data-mvt-ad', String(index));
    results.push({
      index: index,
      kind: kind,
      label: String(label || kind).slice(0, 70),
      area: Math.round(r.width * r.height),
      x: r.x,
      y: r.y,
      width: r.width,
      height: r.height
    });
  };

  // 1. Ad slot containers.
  for (const selector of SLOT_SELECTORS) {
    let nodes;
    try { nodes = document.querySelectorAll(selector); } catch (e) { continue; }
    for (const el of nodes) {
      let label = '';
      if (typeof el.className === 'string' && el.className) label = el.className;
      else if (el.id) label = el.id;
      else label = 'ad-slot';
      const slotId = el.getAttribute('data-ad-slot') || el.getAttribute('data-ad-client') || '';
      if (slotId) label += ' (' + slotId + ')';
      add(el, 'slot', label);
    }
  }

  // 2. Iframes that belong to an ad network.
  for (const el of document.querySelectorAll('iframe')) {
    const src = (el.getAttribute('src') || '').toLowerCase();
    const name = (el.getAttribute('name') || '').toLowerCase();
    const eid = (el.getAttribute('id') || '').toLowerCase();
    const isNetwork = HOSTS.some(h => src.indexOf(h) !== -1);
    const isNamed = name.indexOf('ad') === 0 || eid.indexOf('ad') === 0;
    if (isNetwork || isNamed) {
      add(el, 'iframe', src || name || eid);
    }
  }

  return results;
}
"""


class VisitError(RuntimeError):
    """Raised when a visit cannot be completed."""


# --------------------------------------------------------------------------- #
# Per-thread browser resources
# --------------------------------------------------------------------------- #
class BrowserPool:
    """Owns one Playwright instance + browser per worker thread.

    Threads are plain ``ThreadPoolExecutor`` workers, so a ``threading.local``
    is exactly the right scope: the browser that a thread opens is closed by
    the same thread.
    """

    def __init__(self, engine: BrowserChoice):
        self.engine = engine
        self._local = threading.local()
        self._lock = threading.Lock()
        self._browsers: List[Browser] = []
        self._used_channel = ""

    # -- lifecycle -------------------------------------------------------- #
    def browser(self) -> Browser:
        browser = getattr(self._local, "browser", None)
        if browser is not None:
            try:
                if browser.is_connected():
                    return browser
            except Exception:
                pass

        if not PLAYWRIGHT_OK:
            raise VisitError(
                "Playwright is not installed. Run: pip install playwright && python -m playwright install"
            )

        playwright: Playwright = sync_playwright().start()
        self._local.playwright = playwright
        browser = self._launch(playwright)
        self._local.browser = browser
        with self._lock:
            self._browsers.append(browser)
        return browser

    def _launch(self, playwright: Playwright) -> Browser:
        headless = self.engine is BrowserChoice.HEADLESS
        if self.engine is BrowserChoice.FIREFOX:
            return playwright.firefox.launch(headless=headless, slow_mo=0)

        try:
            return playwright.chromium.launch(
                headless=headless,
                args=chromium_stealth_args(),
                slow_mo=0,
            )
        except Exception as exc:
            # The bundled Chromium is missing (playwright install not run or
            # blocked).  Fall back to a Chrome / Edge already installed here.
            channels = browser_setup.available_channels()
            if not channels:
                raise
            for channel in channels:
                try:
                    browser = playwright.chromium.launch(
                        channel=channel,
                        headless=headless,
                        args=chromium_stealth_args(),
                        slow_mo=0,
                    )
                    self._used_channel = channel
                    return browser
                except Exception:
                    continue
            raise exc

    def close(self) -> None:
        """Close the browser owned by the *current* thread."""
        browser = getattr(self._local, "browser", None)
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
            self._local.browser = None
        playwright = getattr(self._local, "playwright", None)
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass
            self._local.playwright = None

    def close_all(self) -> None:
        """Close every browser that was opened (best effort)."""
        with self._lock:
            browsers = list(self._browsers)
            self._browsers.clear()
        for browser in browsers:
            try:
                browser.close()
            except Exception:
                pass


# --------------------------------------------------------------------------- #
# Human-like behaviour
# --------------------------------------------------------------------------- #
def _random_point(viewport: Dict[str, int], rng: random.Random) -> Tuple[float, float]:
    width = max(viewport.get("width", 1280), 320)
    height = max(viewport.get("height", 720), 240)
    return rng.uniform(width * 0.08, width * 0.92), rng.uniform(height * 0.08, height * 0.92)


def _eased_move(
    page: Page,
    start: Tuple[float, float],
    end: Tuple[float, float],
    rng: random.Random,
    speed: float = 1.0,
) -> None:
    """Move the cursor to ``end`` along a slightly curved, human-like path.

    Playwright interpolates linearly, so the path is broken into a few
    segments that pass near - but not exactly through - the straight line.
    A real wrist never produces a perfectly straight sweep.
    """
    steps = max(6, int(rng.randint(10, 30) / max(speed, 0.2)))
    try:
        page.mouse.move(start[0], start[1], steps=max(2, steps // 3))
    except Exception:
        return

    # A control point offset from the straight line creates the curve.
    mid_x = (start[0] + end[0]) / 2 + rng.uniform(-0.08, 0.08) * abs(end[0] - start[0])
    mid_y = (start[1] + end[1]) / 2 + rng.uniform(-0.08, 0.08) * abs(end[1] - start[1])

    try:
        page.mouse.move(mid_x, mid_y, steps=max(2, steps // 2))
        page.mouse.move(end[0], end[1], steps=max(2, steps // 2))
    except Exception:
        pass


def _human_click(
    page: Page,
    x: float,
    y: float,
    rng: random.Random,
    speed: float = 1.0,
) -> bool:
    """Press and release the mouse like a human (not an instant click)."""
    try:
        page.mouse.move(x, y, steps=rng.randint(4, 12))
        press_time = rng.uniform(0.05, 0.16) / max(speed, 0.2)
        page.mouse.down()
        time.sleep(max(0.02, press_time))
        page.mouse.up()
        return True
    except Exception:
        return False


def _hover_text_targets(
    page: Page,
    viewport: Dict[str, int],
    rng: random.Random,
) -> bool:
    """Glance at a random piece of on-page text (links / paragraphs).

    Hovering real elements instead of random coordinates makes the pointer
    trace look like somebody reading rather than a script moving a cursor.
    """
    try:
        elements = page.query_selector_all("a, p, h1, h2, h3, span, li, button")
    except Exception:
        return False
    if not elements:
        return False

    candidates = [el for el in elements if rng.random() < 0.25]
    rng.shuffle(candidates)
    for element in candidates[:3]:
        try:
            box = element.bounding_box()
            if not box or box["width"] < 4 or box["height"] < 4:
                continue
            x = box["x"] + box["width"] * rng.uniform(0.2, 0.8)
            y = box["y"] + box["height"] * rng.uniform(0.3, 0.7)
            if x < 0 or y < 0 or x > viewport.get("width", 1280) or y > viewport.get("height", 720):
                continue
            page.mouse.move(x, y, steps=rng.randint(5, 14))
            return True
        except Exception:
            continue
    return False


def simulate_human_behavior(
    page: Page,
    profile: UserAgentProfile,
    stay_seconds: float,
    rng: random.Random,
    stop_event: Optional[threading.Event] = None,
    speed: float = 1.0,
) -> None:
    """Move the mouse, scroll around and wait out the dwell time.

    The loop is chopped into small slices so a STOP request is honoured
    within a fraction of a second instead of after the full dwell time.

    ``speed`` scales every pause: 2.0 behaves twice as brisk, 0.5 lingers.
    """
    speed = max(0.1, float(speed))
    viewport = profile.viewport or {"width": 1280, "height": 720}
    deadline = time.monotonic() + max(0.0, stay_seconds)

    # 1. Approach: a curved sweep onto the page.
    position = _random_point(viewport, rng)
    try:
        page.mouse.move(rng.uniform(0, viewport.get("width", 1280)),
                        rng.uniform(0, viewport.get("height", 720)), steps=2)
    except Exception:
        pass
    _eased_move(page, position, _random_point(viewport, rng), rng, speed)

    scroll_direction = rng.choice([1, -1])
    while time.monotonic() < deadline:
        if stop_event is not None and stop_event.is_set():
            return

        # 2. Human-like scrolling: a burst of small eased wheel steps.
        try:
            if rng.random() < 0.75:
                total = rng.randint(120, 520) * scroll_direction
                chunk = max(40, int(total / rng.randint(2, 4)))
                for _ in range(rng.randint(2, 4)):
                    page.mouse.wheel(0, chunk)
                    time.sleep(rng.uniform(0.03, 0.12) / speed)
            else:
                scroll_direction = -scroll_direction
        except Exception:
            try:
                page.evaluate(
                    "([x, y]) => window.scrollBy(0, y)", [0, rng.randint(150, 400)]
                )
            except Exception:
                pass

        # 3. Wander over real content, not just random coordinates.
        if rng.random() < 0.55:
            _hover_text_targets(page, viewport, rng)
        else:
            target = _random_point(viewport, rng)
            _eased_move(page, position, target, rng, speed)
            position = target

        # 4. Idle like a reader glancing at the page.
        slice_seconds = min(rng.uniform(0.6, 2.2), max(0.05, deadline - time.monotonic()))
        slice_seconds /= speed
        if stop_event is not None:
            slice_seconds = min(slice_seconds, 0.5) if stop_event.is_set() else slice_seconds
        time.sleep(max(0.05, slice_seconds))

    # Final gentle scroll towards the bottom/top to look natural.
    try:
        page.mouse.wheel(0, rng.randint(-300, 300))
    except Exception:
        pass


def _ad_discovery_script() -> str:
    """Build the ad-discovery JavaScript with the real thresholds inlined."""
    return (
        AD_DISCOVERY_JS.replace("__MIN_W__", str(MIN_AD_WIDTH))
        .replace("__MIN_H__", str(MIN_AD_HEIGHT))
        .replace("__HOSTS__", json.dumps(list(AD_HOST_MARKERS)))
        .replace("__SLOTS__", json.dumps(list(AD_SELECTORS)))
    )


def find_ad_targets(page: Page, rng: random.Random) -> List[Dict[str, Any]]:
    """Locate advertisement slots on the current page.

    Everything is collected in **one** browser round-trip and each candidate
    is tagged with a marker attribute, so the caller can pick one and get a
    precise handle for it afterwards.  Only elements that are visible and big
    enough to be an advertisement are returned.
    """
    try:
        candidates = page.evaluate(_ad_discovery_script())
    except Exception:
        return []
    if not candidates:
        return []
    return list(candidates)


def pick_ad_target(page: Page, rng: random.Random) -> Optional[Dict[str, Any]]:
    """Choose one ad slot, scroll it into view and return its click position.

    Returns ``{"kind", "label", "x", "y"}`` or ``None``.
    """
    candidates = find_ad_targets(page, rng)
    if not candidates:
        return None

    # Bigger slots are the real ad units, small ones are usually trackers.
    weighted = sorted(candidates, key=lambda item: item.get("area", 0), reverse=True)
    pool = weighted[: max(1, min(len(weighted), 5))]
    chosen = rng.choice(pool)
    index = chosen.get("index", -1)
    if index < 0:
        return None

    selector = f'[data-mvt-ad="{index}"]'
    try:
        element = page.query_selector(selector)
        if element is None:
            return None
        element.scroll_into_view_if_needed(timeout=5000)
    except Exception:
        return None

    # Give the browser a moment to settle after the scroll.
    time.sleep(rng.uniform(0.15, 0.45))

    try:
        box = element.bounding_box()
    except Exception:
        box = None
    if not box or box.get("width", 0) < MIN_AD_WIDTH or box.get("height", 0) < MIN_AD_HEIGHT:
        return None

    # Aim slightly off-centre - nobody clicks the exact pixel.
    x = box["x"] + box["width"] * rng.uniform(0.35, 0.65)
    y = box["y"] + box["height"] * rng.uniform(0.35, 0.65)
    return {"kind": chosen.get("kind", "slot"), "label": chosen.get("label", "ad"), "x": x, "y": y}


def click_ad_slot(
    page: Page,
    rng: random.Random,
    stop_event: Optional[threading.Event] = None,
    speed: float = 1.0,
) -> Optional[Dict[str, Any]]:
    """Click one advertisement slot the way a visitor would.

    Used to verify that ad creatives render and that their landing pages
    work.  The click is performed with a real press/release on the slot's
    centre, and a landing page that opens in a new tab is visited briefly
    before it is closed again.

    Returns a description of what was clicked, or ``None``.
    """
    target = pick_ad_target(page, rng)
    if not target:
        return None

    x = target["x"]
    y = target["y"]

    # Capture a landing page that opens in a new tab.
    popups: List[Any] = []

    def on_popup(popup: Any) -> None:
        popups.append(popup)

    try:
        page.on("popup", on_popup)
    except Exception:
        pass

    original_url = page.url
    try:
        if not _human_click(page, x, y, rng, speed):
            return None
    except Exception:
        return None
    finally:
        try:
            page.remove_listener("popup", on_popup)
        except Exception:
            pass

    # Give the ad network a moment to open the landing page.
    deadline = time.monotonic() + 2.5
    while not popups and time.monotonic() < deadline:
        if stop_event is not None and stop_event.is_set():
            break
        time.sleep(0.1)

    landing_url = ""
    if popups:
        popup = popups[0]
        try:
            popup.wait_for_load_state("domcontentloaded", timeout=8000)
            landing_url = popup.url
        except Exception:
            landing_url = getattr(popup, "url", "")
        # Read the landing page like a visitor would, then close it.
        time.sleep(rng.uniform(1.0, 2.5) / max(speed, 0.2))
        try:
            popup.close()
        except Exception:
            pass

    # Some ads navigate the main tab instead of opening a new one.
    if page.url != original_url:
        try:
            page.go_back(wait_until="domcontentloaded", timeout=10000)
        except Exception:
            pass

    return {
        "kind": target["kind"],
        "label": target["label"],
        "landing_url": landing_url,
    }


def click_random_internal_link(
    page: Page,
    rng: random.Random,
    stop_event: Optional[threading.Event] = None,
) -> bool:
    """Click one random same-origin link on the page.

    Returns ``True`` when a link was actually followed.
    """
    try:
        hrefs = page.eval_on_selector_all(
            "a[href]",
            "els => els.map(e => e.getAttribute('href')).filter(h => h && h.length > 1)",
        )
    except Exception:
        return False
    if not hrefs:
        return False

    candidates = [
        h
        for h in hrefs
        if not h.lower().startswith(("javascript:", "mailto:", "tel:", "#"))
        and not any(marker in h.lower() for marker in ("logout", "signout", "delete", "unsubscribe"))
    ]
    if not candidates:
        return False

    href = rng.choice(candidates)
    if stop_event is not None and stop_event.is_set():
        return False
    try:
        page.evaluate(
            """(href) => {
                const link = [...document.querySelectorAll('a[href]')]
                    .find(a => a.getAttribute('href') === href);
                if (link) { link.scrollIntoView({block: 'center'}); link.click(); }
            }""",
            href,
        )
    except Exception:
        return False
    return True


# --------------------------------------------------------------------------- #
# The visitor
# --------------------------------------------------------------------------- #
class Visitor:
    """Executes single visits against a target URL."""

    def __init__(self, engine: BrowserChoice, browsers: BrowserPool):
        self.engine = engine
        self.browsers = browsers

    # -- helpers ---------------------------------------------------------- #
    def _context_kwargs(
        self,
        profile: UserAgentProfile,
        proxy: Optional[ProxyEntry],
    ) -> Dict[str, Any]:
        kwargs = profile.context_kwargs(self.engine)
        if proxy is not None:
            kwargs["proxy"] = proxy.playwright_proxy()
        return kwargs

    def _new_context(
        self,
        browser: Browser,
        profile: UserAgentProfile,
        proxy: Optional[ProxyEntry],
    ) -> BrowserContext:
        """Create a context, degrading gracefully on older Playwright builds.

        ``screen``, ``is_mobile`` and ``has_touch`` are not accepted by every
        Playwright / browser combination, so unsupported options are dropped
        one by one instead of failing the whole visit.
        """
        kwargs = self._context_kwargs(profile, proxy)
        optional = ("screen", "is_mobile", "has_touch", "device_scale_factor", "locale",
                    "timezone_id")
        attempts = 0
        while True:
            try:
                return browser.new_context(**kwargs)
            except (TypeError, ValueError) as exc:
                if attempts >= len(optional):
                    raise
                attempts += 1
                message = str(exc)
                removed = False
                for key in optional:
                    if key in kwargs and (key in message or attempts == 1):
                        kwargs.pop(key, None)
                        removed = True
                        break
                if not removed:
                    raise

    def _verify_exit_ip(self, context: BrowserContext, proxy: Optional[ProxyEntry]) -> str:
        """Ask the IP echo service *through the same context*.

        ``context.request`` shares the context's proxy configuration, so the
        answer is the exit IP the browser is actually using.
        """
        if proxy is None:
            return ""
        try:
            response = context.request.get(IP_ECHO_URL, timeout=15_000)
            if response.ok:
                payload = response.json()
                ip = payload.get("ip") if isinstance(payload, dict) else ""
                return str(ip or "")
        except Exception:
            pass
        return ""

    # -- main entry point -------------------------------------------------- #
    def visit(
        self,
        url: str,
        profile: UserAgentProfile,
        proxy: Optional[ProxyEntry],
        referrer: Optional[str] = None,
        min_stay: float = 5.0,
        max_stay: float = 15.0,
        rng: Optional[random.Random] = None,
        stop_event: Optional[threading.Event] = None,
        verify_exit_ip: bool = True,
        navigation_timeout: float = 30.0,
        click_internal_links: bool = False,
        max_internal_navs: int = 1,
        thread_id: int = 0,
        index: int = 0,
        on_status: Optional[Any] = None,
        click_ads: bool = False,
        max_ad_clicks: int = 1,
        ad_click_delay: float = 1.5,
        speed: float = 1.0,
    ) -> VisitResult:
        """Perform one visit and return the result (never raises)."""
        rng = rng or random.Random()
        started = time.monotonic()
        result = VisitResult(
            index=index,
            thread_id=thread_id,
            success=False,
            url=url,
            proxy=proxy.display if proxy else "",
            user_agent=profile.ua,
            referrer=referrer or "",
        )

        context: Optional[BrowserContext] = None
        try:
            browser = self.browsers.browser()
            context = self._new_context(browser, profile, proxy)
            context.set_default_timeout(navigation_timeout * 1000)
            context.set_default_navigation_timeout(navigation_timeout * 1000)

            # Remove the most obvious automation hints.
            try:
                context.add_init_script(
                    """
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
                    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                    window.chrome = window.chrome || {runtime: {}};
                    """
                )
            except Exception:
                pass

            page = context.new_page()
            page.set_default_timeout(navigation_timeout * 1000)

            if referrer:
                try:
                    response = page.goto(
                        url,
                        referer=referrer,
                        wait_until="domcontentloaded",
                        timeout=navigation_timeout * 1000,
                    )
                except TypeError:
                    # Older Playwright versions do not accept referer=.
                    response = page.goto(
                        url, wait_until="domcontentloaded", timeout=navigation_timeout * 1000
                    )
            else:
                response = page.goto(
                    url, wait_until="domcontentloaded", timeout=navigation_timeout * 1000
                )

            if response is not None and response.status >= 400:
                result.status = f"HTTP {response.status}"
            else:
                result.status = f"HTTP {response.status}" if response is not None else "OK"

            # Verify the exit IP through the same context/proxy.
            if verify_exit_ip:
                exit_ip = self._verify_exit_ip(context, proxy)
                if exit_ip:
                    result.exit_ip = exit_ip
                else:
                    result.exit_ip_error = (
                        "the IP echo service could not be reached through this proxy"
                    )

            stay = rng.uniform(min_stay, max_stay) if max_stay > min_stay else min_stay
            simulate_human_behavior(page, profile, stay, rng, stop_event, speed)

            # --- ad interaction (ad QA / landing page verification) -------- #
            if click_ads and max_ad_clicks > 0:
                for _ in range(max_ad_clicks):
                    if stop_event is not None and stop_event.is_set():
                        break
                    clicked = click_ad_slot(page, rng, stop_event, speed)
                    if not clicked:
                        break
                    result.ad_clicks += 1
                    result.ad_targets.append(clicked["label"])
                    if clicked.get("landing_url"):
                        result.ad_landing_urls.append(clicked["landing_url"])
                    time.sleep(max(0.1, ad_click_delay) / max(speed, 0.2))

            # Optional: follow one internal link, then behave again.
            if click_internal_links and max_internal_navs > 0:
                for nav_index in range(max_internal_navs):
                    if stop_event is not None and stop_event.is_set():
                        break
                    if not click_random_internal_link(page, rng, stop_event):
                        break
                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=10_000)
                    except Exception:
                        break
                    result.internal_clicks += 1
                    simulate_human_behavior(
                        page, profile, rng.uniform(2.0, 6.0), rng, stop_event, speed
                    )

            result.success = True
            result.duration = time.monotonic() - started
            return result

        except PlaywrightTimeoutError as exc:
            result.error = f"navigation timeout ({exc})".split("\n")[0]
        except PlaywrightError as exc:
            result.error = str(exc).split("\n")[0][:240]
        except VisitError as exc:
            result.error = str(exc)
        except Exception as exc:  # pragma: no cover - defensive
            result.error = f"{type(exc).__name__}: {exc}"[:240]
        finally:
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
            if not result.success:
                result.duration = time.monotonic() - started
        return result
