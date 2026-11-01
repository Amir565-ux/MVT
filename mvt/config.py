"""Persistence of the GUI configuration (JSON).

Everything the user typed - target URL, counters, referrer / user-agent /
proxy lists and all options - is stored in a single JSON file inside the
user's home directory so the tool can be closed and re-opened without losing
the setup.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from .defaults import DEFAULT_REFERRERS, DEFAULT_USER_AGENTS
from .models import BrowserChoice, RunConfig

CONFIG_DIR = Path.home() / ".mass_visitor_tool"
CONFIG_FILE = CONFIG_DIR / "config.json"


def default_payload() -> Dict[str, Any]:
    """The configuration a fresh installation starts with."""
    return {
        "url": "",
        "visits": 50,
        "threads": 10,
        "engine": BrowserChoice.HEADLESS.value,
        "use_referrers": True,
        "use_user_agents": True,
        "use_proxies": True,
        "referrers": list(DEFAULT_REFERRERS),
        "user_agents": list(DEFAULT_USER_AGENTS),
        "proxies": [],
        "min_stay": 5.0,
        "max_stay": 15.0,
        "navigation_timeout": 30.0,
        "proxy_timeout": 12.0,
        "verify_exit_ip": True,
        "respect_robots": True,
        "click_internal_links": False,
        "max_internal_navs": 1,
        "click_ads": False,
        "max_ad_clicks": 1,
        "ad_click_delay": 1.5,
        "fast_mode": False,
        "speed": 1.0,
    }


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load the saved configuration, falling back to the defaults."""
    target = Path(path) if path else CONFIG_FILE
    payload = default_payload()
    try:
        if target.exists():
            with open(target, "r", encoding="utf-8") as handle:
                stored = json.load(handle)
            if isinstance(stored, dict):
                payload.update({k: v for k, v in stored.items() if k in payload})
    except Exception:
        # A broken config must never prevent the tool from starting.
        pass
    return payload


def save_config(payload: Dict[str, Any], path: Optional[Path] = None) -> bool:
    """Write the configuration to disk. Returns ``True`` on success."""
    target = Path(path) if path else CONFIG_FILE
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        merged = default_payload()
        merged.update({k: v for k, v in payload.items() if k in merged})
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(merged, handle, indent=2, ensure_ascii=False)
        return True
    except Exception:
        return False


def config_to_run_config(payload: Dict[str, Any]) -> RunConfig:
    """Convert a stored payload into a :class:`RunConfig`."""
    try:
        engine = BrowserChoice(payload.get("engine", BrowserChoice.HEADLESS.value))
    except ValueError:
        engine = BrowserChoice.HEADLESS

    return RunConfig(
        url=str(payload.get("url", "")),
        visits=int(payload.get("visits", 50) or 1),
        threads=int(payload.get("threads", 10) or 1),
        engine=engine,
        use_referrers=bool(payload.get("use_referrers", True)),
        use_user_agents=bool(payload.get("use_user_agents", True)),
        use_proxies=bool(payload.get("use_proxies", True)),
        referrers=[str(r) for r in payload.get("referrers", [])],
        user_agents=[str(u) for u in payload.get("user_agents", [])],
        proxies=[str(p) for p in payload.get("proxies", [])],
        min_stay=float(payload.get("min_stay", 5.0)),
        max_stay=float(payload.get("max_stay", 15.0)),
        navigation_timeout=float(payload.get("navigation_timeout", 30.0)),
        proxy_timeout=float(payload.get("proxy_timeout", 12.0)),
        verify_exit_ip=bool(payload.get("verify_exit_ip", True)),
        respect_robots=bool(payload.get("respect_robots", True)),
        click_internal_links=bool(payload.get("click_internal_links", False)),
        max_internal_navs=int(payload.get("max_internal_navs", 1)),
        click_ads=bool(payload.get("click_ads", False)),
        max_ad_clicks=int(payload.get("max_ad_clicks", 1)),
        ad_click_delay=float(payload.get("ad_click_delay", 1.5)),
        fast_mode=bool(payload.get("fast_mode", False)),
        speed=float(payload.get("speed", 1.0)),
    )


def read_lines(text: str) -> List[str]:
    """Split a multi-line text area into non-empty, stripped lines."""
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def read_list_file(path: str) -> List[str]:
    """Read a proxy / UA / referrer list from a text file."""
    lines: List[str] = []
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            lines.append(line)
    return lines
