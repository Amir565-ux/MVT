# Mass Website Visitor

A modular, multi-threaded **web traffic generator** with a PyQt6 desktop GUI. It
simulates human-like visits against a target URL, routes every request through a
**distinct proxy** so the visible IP rotates, and rotates **browser fingerprints**
(User-Agent, viewport, Referer) per visit.

> **For authorized testing only.** This tool exists to validate analytics
> pipelines, QA a site under load, or rehearse a marketing landing page. Do not
> use it to inflate ad revenue, commit click fraud, evade IP bans, or breach a
> site's Terms of Service. The tool ships with a robots.txt compliance check and
> an acknowledgement checkbox precisely because running it against someone
> else's site without permission is both unethical and frequently illegal.

---

## 1. Install

```bash
cd mass_visitor_tool

# 1) Python dependencies
pip install -r requirements.txt

# 2) Browser binaries (Chromium for Chrome/Headless mode, Firefox for Firefox mode)
python -m playwright install chromium firefox

# 3) Verify the installation
python main.py --check
```

Python 3.10 or newer is required. The GUI needs **PyQt6**, the engine needs
**Playwright**, and proxy health checks use **requests** (+ **PySocks** for
`socks4://` / `socks5://`).

> **No browser binaries?** If `playwright install` is blocked by a corporate
> proxy or firewall, the tool automatically falls back to the Chrome or Edge
> already installed on the machine (Playwright's `channel` option). The
> Chrome/Headless modes keep working; only Firefox mode needs the bundled
> build.

## 2. Run

```bash
python main.py
```

Other entry points:

| Command | Purpose |
|---|---|
| `python main.py --check` | Installation report |
| `python main.py --install-browsers --browser firefox` | Download browser binaries only |

The GUI also has an **Install Browsers** button that runs the Playwright
installer for you, so you never have to touch the terminal again.

---

## 3. Using the GUI

```
┌────────────────────────────────────────────────────────────────────────┐
│ Target    Target URL: [____________________]  Visits:[50] Threads:[10]  │
├────────────────────────────────────────────────────────────────────────┤
│ Options   Browser: (•)Chrome ( )Firefox ( )Headless                     │
│           ☑ Referrers  ☑ User-Agents  ☑ Proxies                        │
│           Stay on page: [5.0] - [15.0] s  ☑ Verify exit IP             │
│           ☑ Respect robots.txt  ☑ Click internal link [1]               │
├───────────────────┬───────────────────┬────────────────────────────────┤
│ Referrers         │ User Agents       │ Proxies                        │
│ (one URL / line)  │ (one UA / line)   │ (one proxy / line)             │
│                   │                   │ [Load Proxy List][Clear][Test] │
├───────────────────┴───────────────────┴────────────────────────────────┤
│ Activity Log                                                            │
│ Thread #1: Using Proxy [1.2.3.4:8080] - Success (HTTP 200 in 8.2s)      │
├────────────────────────────────────────────────────────────────────────┤
│ ☑ authorized  [START] [STOP]  ▓▓▓▓▓░░░  32/50   30 ok, 2 failed       │
└────────────────────────────────────────────────────────────────────────┘
```

### Step by step

1. **Target URL** – enter the site you want to test, e.g. `https://example.com`.
2. **Visits** – total number of page hits. **Threads** – how many browsers run
   at the same time (1–50).
3. **Browser** – `Chrome` (visible window), `Firefox` (visible window) or
   `Headless` (Chromium, no window).
4. **Toggles** – switch off *Referrers*, *User-Agents* or *Proxies* to disable
   that feature.
5. **Payload panels** – paste your lists, one entry per line:
   * **Referrers**: `https://www.google.com/`, `https://www.reddit.com/` …
   * **User Agents**: any real UA string. Optional prefix `[mobile]`,
     `[tablet]` or `[desktop]` forces the device class.
   * **Proxies**: `ip:port`, `http://ip:port`, `https://user:pass@ip:port`,
     `socks5://ip:port`, `socks4://ip:port`, IPv6 `[::1]:8080`.
     Use **Load Proxy List** to import a `.txt` file and **Test Proxies** to
     validate it before a run.
6. Tick **"I confirm this is authorized testing"**, then press **START**.
   **STOP** finishes the in-flight visits and shuts everything down cleanly.

### Proxy list format

| Format | Example |
|---|---|
| Host and port | `1.2.3.4:8080` |
| Explicit scheme | `http://1.2.3.4:8080` |
| Authenticated | `http://user:pass@1.2.3.4:8080` |
| SOCKS5 | `socks5://1.2.3.4:1080` |
| SOCKS4 | `socks4://1.2.3.4:1080` |
| IPv6 | `[2001:db8::1]:8080` |

Lines starting with `#` are treated as comments and duplicates are removed
automatically (the same endpoint twice would defeat IP rotation).

---

## 4. How the rotation works

### Proxy rotation — one distinct IP per visit

`mvt/proxy_manager.py` owns the `ProxyPool`:

1. Every line is parsed into a `ProxyEntry` (scheme, host, port, credentials).
2. Each proxy is **health-checked** against an IP echo service
   (`api.ipify.org`, `api.myip.com`, `ip-api.com`, `ipinfo.io`). A proxy that
   times out, refuses the connection, or returns the machine's *own* IP
   (meaning it silently fell back to a direct connection) is quarantined and
   **never used**.
3. `ProxyPool.lease()` hands out healthy proxies in **round-robin** order and
   marks the proxy as *in use* for the duration of the visit. Because a proxy
   that is in use is skipped, two concurrent visits can never share an exit IP.
4. When a visit fails, `report_failure()` quarantines that proxy permanently
   for the run. `report_success()` returns it to the rotation and records the
   observed exit IP.
5. **Exit-IP verification** (optional, on by default) queries the IP echo
   service again *through the browser context itself*
   (`context.request` inherits the context proxy) and logs the real exit IP, so
   the log proves the IP actually changed.

The GUI footer shows the number of **unique exit IPs** observed during the run.

### User-Agent & fingerprint rotation

`mvt/fingerprint.py` parses every UA string and derives a matching profile:

| Device class | Detected by | Viewport pool |
|---|---|---|
| Mobile | `iPhone`, `iPod`, `Mobile`, `Android … Mobile` | 360×640, 390×844, 412×915 … |
| Tablet | `iPad`, `Android` without `Mobile` | 768×1024, 820×1180 … |
| Desktop | everything else | 1920×1080, 1536×864, 1366×768 … |

Each profile also carries a matching `device_scale_factor`, touch support,
`navigator.platform`, locale and timezone. Firefox contexts omit
`is_mobile` / `has_touch` because Playwright only supports them on Chromium.
The rotator never returns the same UA twice in a row.

### Referrer rotation

One URL is drawn per visit from the referrer list and passed to
`page.goto(..., referer=...)`, so the target sees a `Referer` header pointing at
Google, Bing, Facebook, Reddit … exactly like a visitor arriving from search.

### Human-like behaviour

After each page load (`mvt/visitor.py`):

* the cursor moves along a smooth arc onto the page,
* the page is scrolled in short bursts, up **and** down, with the direction
  changing at random,
* the cursor keeps wandering while the visitor "reads",
* the browser stays open for a random **5–15 s** (configurable) dwell time,
* optionally one **random internal link** is clicked and the new page is read
  for a few seconds,
* the context is closed afterwards so every visit starts with clean cookies.

---

## 5. Project layout

```
mass_visitor_tool/
├── main.py                    # entry point (--check / --install-browsers)
├── requirements.txt
├── README.md
├── mvt/
│   ├── __init__.py
│   ├── models.py              # RunConfig, ProxyEntry, VisitResult, …
│   ├── defaults.py            # built-in UA + referrer lists
│   ├── fingerprint.py         # UA parsing, viewport matching, rotation
│   ├── proxy_manager.py       # parsing, health checks, ProxyPool rotation
│   ├── visitor.py             # the Playwright visit + human-like behaviour
│   ├── engine.py              # ThreadPoolExecutor orchestration
│   ├── logbus.py              # thread-safe log plumbing
│   ├── robots.py              # robots.txt compliance check
│   ├── config.py              # JSON persistence (~/.mass_visitor_tool/config.json)
│   ├── browser_setup.py       # automatic `playwright install` handling
│   └── gui/
│       ├── __init__.py
│       └── main_window.py     # PyQt6 main window
└── tests/
    └── test_core.py           # offline self-tests (no browser required)
```

### Module responsibilities

| Module | Responsibility |
|---|---|
| `models` | Pure data structures shared by the GUI and the engine |
| `fingerprint` | UA → device class → viewport / locale / timezone, rotation |
| `proxy_manager` | Proxy parsing, health validation, round-robin pool |
| `visitor` | One visit: context creation, navigation, behaviour simulation |
| `engine` | Worker pool, statistics, stop handling, logging |
| `robots` | robots.txt lookup and verdict |
| `config` | Save/load the whole GUI setup as JSON |
| `browser_setup` | Detect and install the Playwright browser binaries |
| `logbus` | Thread-safe queue that bridges worker threads and the GUI |

---

## 6. Safety & guardrails

* **robots.txt is checked before every run.** If the target disallows our
  User-Agent the run is aborted with a message. Disable the toggle only for
  sites you own.
* **Authorisation checkbox.** START stays disabled until you confirm the run is
  authorised.
* **Permanent disclaimer** in the footer and in this README.
* **No credential storage.** Proxy credentials live in the config file only if
  you paste them; nothing is uploaded anywhere.
* **No abusive features by default.** There is no click-fraud, no
  CAPTCHA-solving and no auto-posting – the tool performs page loads and
  navigation only.

---

## 7. Tests

```bash
python tests\test_core.py
```

43 offline assertions covering proxy parsing (all supported formats, IPv6,
malformed input, duplicate collapsing), the round-robin pool (deterministic
order, quarantine of failed proxies, no double-leasing under 8 concurrent
threads), fingerprint generation (device classification, viewport matching,
Firefox flag filtering, rotation coverage) and configuration validation.

---

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| `Playwright is not installed` | `pip install playwright` |
| Browser binaries missing | Click **Install Browsers**, or `python -m playwright install chromium firefox` |
| `None of the proxies passed the health check` | The proxies are unreachable or need credentials – test them with **Test Proxies** |
| SOCKS proxy with a password fails in Chrome mode | Chromium cannot authenticate SOCKS proxies; switch to **Firefox** |
| "Proxy mode is enabled but the proxy list is empty" | Add proxies or untick **Proxies** |
| Visits fail instantly with a timeout | Raise **Stay on page**? No – raise the navigation timeout in `config.py` (`navigation_timeout`) |
| A browser window opens in Headless mode | You are running the **Chrome** radio button – pick **Headless** |

---

## 9. License

Provided for authorized QA, analytics validation and load testing of systems
you own or are explicitly permitted to test. The authors accept no liability
for misuse performed with this tool.

## 10. Application

You will get exe in the MVT\dist\main Folder and this is open-source
