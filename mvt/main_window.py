"""PyQt6 user interface for the Mass Website Visitor tool.

Layout (top to bottom):

1. Target bar    - URL, visit count, thread count
2. Options row   - browser choice, feature toggles, behaviour settings
3. Payload panels- Referrers | User Agents | Proxies (side by side)
4. Activity log  - live, colour coded engine messages
5. Footer        - START / STOP, progress bar, success/failure counters
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict, Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import browser_setup
from ..config import (
    CONFIG_FILE,
    config_to_run_config,
    load_config,
    read_lines,
    read_list_file,
    save_config,
)
from ..defaults import DEFAULT_REFERRERS, DEFAULT_USER_AGENTS
from ..engine import TrafficEngine
from ..logbus import LogBus
from ..models import BrowserChoice, RunConfig, RunStats

LOG_COLORS = {
    "INFO": QColor("#9aa4b2"),
    "SUCCESS": QColor("#3fb950"),
    "WARN": QColor("#d29922"),
    "ERROR": QColor("#f85149"),
    "SYSTEM": QColor("#58a6ff"),
}

DISCLAIMER = (
    "For authorized testing only - analytics validation, QA and load testing of sites "
    "you own or have written permission to test. Do not use to inflate ad revenue, "
    "click fraud, evade bans or violate a site's Terms of Service."
)


class EngineBridge(QObject):
    """Marshals engine callbacks from worker threads onto the Qt main thread."""

    log_signal = pyqtSignal(str, str)
    progress_signal = pyqtSignal(object)
    finished_signal = pyqtSignal(object)
    control_signal = pyqtSignal(str)


class MainWindow(QMainWindow):
    """The application window."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Mass Website Visitor - Traffic Generator")
        self.resize(1440, 940)

        self.log_bus = LogBus()
        self.bridge = EngineBridge()
        self.engine: Optional[TrafficEngine] = None
        self._loaded_config: Dict[str, Any] = {}

        self._build_ui()
        self._connect_signals()
        self._load_settings()
        self._check_environment()

    # ------------------------------------------------------------------ UI #
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        root.addWidget(self._build_target_bar())
        root.addWidget(self._build_options_group())
        root.addWidget(self._build_payload_panels(), 1)
        root.addWidget(self._build_log_box())
        root.addLayout(self._build_footer())

    # -- target bar ------------------------------------------------------- #
    def _build_target_bar(self) -> QGroupBox:
        box = QGroupBox("Target")
        layout = QHBoxLayout(box)

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://example.com  (target website)")
        self.url_edit.setClearButtonEnabled(True)
        layout.addWidget(QLabel("Target URL:"))
        layout.addWidget(self.url_edit, 1)

        layout.addWidget(QLabel("Visits:"))
        self.visits_spin = QSpinBox()
        self.visits_spin.setRange(1, 1_000_000)
        self.visits_spin.setValue(50)
        self.visits_spin.setToolTip("Total number of page hits to perform")
        layout.addWidget(self.visits_spin)

        layout.addWidget(QLabel("Threads:"))
        self.threads_spin = QSpinBox()
        self.threads_spin.setRange(1, 50)
        self.threads_spin.setValue(10)
        self.threads_spin.setToolTip("Concurrent browsers (1-50)")
        layout.addWidget(self.threads_spin)
        return box

    # -- options ---------------------------------------------------------- #
    def _build_options_group(self) -> QGroupBox:
        box = QGroupBox("Options")
        grid = QVBoxLayout(box)

        # Row 1: browser + payload toggles
        top = QHBoxLayout()
        top.addWidget(QLabel("Browser:"))

        self.chrome_radio = QRadioButton("Chrome")
        self.firefox_radio = QRadioButton("Firefox")
        self.headless_radio = QRadioButton("Headless")
        self.headless_radio.setToolTip("Chromium without a visible window")
        self.browser_group = QButtonGroup(self)
        for button in (self.chrome_radio, self.firefox_radio, self.headless_radio):
            self.browser_group.addButton(button)
            top.addWidget(button)
        self.headless_radio.setChecked(True)

        top.addSpacing(24)
        self.use_ref_cb = QCheckBox("Referrers")
        self.use_ua_cb = QCheckBox("User-Agents")
        self.use_proxy_cb = QCheckBox("Proxies")
        for toggle in (self.use_ref_cb, self.use_ua_cb, self.use_proxy_cb):
            top.addWidget(toggle)

        top.addStretch(1)
        self.install_btn = QPushButton("Install Browsers")
        self.install_btn.setToolTip("Run 'python -m playwright install' for the selected browser")
        top.addWidget(self.install_btn)
        self.save_cfg_btn = QPushButton("Save Config")
        top.addWidget(self.save_cfg_btn)
        self.load_cfg_btn = QPushButton("Load Config")
        top.addWidget(self.load_cfg_btn)
        grid.addLayout(top)

        # Row 2: behaviour
        bottom = QHBoxLayout()
        bottom.addWidget(QLabel("Stay on page:"))
        self.min_stay_spin = QDoubleSpinBox()
        self.min_stay_spin.setRange(0.0, 600.0)
        self.min_stay_spin.setDecimals(1)
        self.min_stay_spin.setValue(5.0)
        self.min_stay_spin.setSuffix(" s")
        bottom.addWidget(self.min_stay_spin)

        self.max_stay_spin = QDoubleSpinBox()
        self.max_stay_spin.setRange(0.0, 600.0)
        self.max_stay_spin.setDecimals(1)
        self.max_stay_spin.setValue(15.0)
        self.max_stay_spin.setSuffix(" s")
        bottom.addWidget(self.max_stay_spin)

        self.verify_ip_cb = QCheckBox("Verify exit IP")
        self.verify_ip_cb.setToolTip("Query an IP echo service through the proxy and log it")
        self.robots_cb = QCheckBox("Respect robots.txt")
        self.robots_cb.setToolTip("Abort the run when robots.txt disallows the target URL")
        self.internal_cb = QCheckBox("Click internal link")
        self.max_internal_spin = QSpinBox()
        self.max_internal_spin.setRange(1, 5)
        self.max_internal_spin.setValue(1)
        for widget in (self.verify_ip_cb, self.robots_cb, self.internal_cb, self.max_internal_spin):
            bottom.addWidget(widget)

        bottom.addWidget(QLabel("| Ads:"))
        self.click_ads_cb = QCheckBox("Click ads")
        self.click_ads_cb.setToolTip(
            "Click an advertisement slot to verify that ad creatives and their "
            "landing pages work (ad QA on sites you own or may test)."
        )
        self.max_ad_spin = QSpinBox()
        self.max_ad_spin.setRange(1, 5)
        self.max_ad_spin.setValue(1)
        self.max_ad_spin.setToolTip("Maximum number of ad slots clicked per visit")
        bottom.addWidget(self.click_ads_cb)
        bottom.addWidget(self.max_ad_spin)

        bottom.addWidget(QLabel("| Speed:"))
        self.fast_cb = QCheckBox("Fast mode")
        self.fast_cb.setToolTip(
            "Shorten the dwell time, skip the exit-IP check and move faster"
        )
        self.speed_spin = QDoubleSpinBox()
        self.speed_spin.setRange(0.2, 5.0)
        self.speed_spin.setDecimals(1)
        self.speed_spin.setValue(1.0)
        self.speed_spin.setSingleStep(0.5)
        self.speed_spin.setSuffix("x")
        self.speed_spin.setToolTip("Speed multiplier for the simulated behaviour")
        bottom.addWidget(self.fast_cb)
        bottom.addWidget(self.speed_spin)

        bottom.addStretch(1)
        self.env_label = QLabel("")
        self.env_label.setStyleSheet("color: #d29922;")
        bottom.addWidget(self.env_label)
        grid.addLayout(bottom)
        return box

    # -- payload panels ---------------------------------------------------- #
    def _build_payload_panels(self) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Referrers
        ref_box = QGroupBox("Referrers")
        ref_layout = QVBoxLayout(ref_box)
        self.ref_text = QPlainTextEdit()
        self.ref_text.setPlaceholderText(
            "https://www.google.com/\nhttps://www.bing.com/\nhttps://www.reddit.com/"
        )
        ref_layout.addWidget(self.ref_text)
        ref_buttons = QHBoxLayout()
        self.ref_default_btn = QPushButton("Defaults")
        self.ref_load_btn = QPushButton("Load File...")
        self.ref_clear_btn = QPushButton("Clear")
        for button in (self.ref_default_btn, self.ref_load_btn, self.ref_clear_btn):
            ref_buttons.addWidget(button)
        ref_layout.addLayout(ref_buttons)
        layout.addWidget(ref_box, 1)

        # User agents
        ua_box = QGroupBox("User Agents")
        ua_layout = QVBoxLayout(ua_box)
        self.ua_text = QPlainTextEdit()
        self.ua_text.setPlaceholderText("Mozilla/5.0 (Windows NT 10.0; Win64; x64) ...")
        ua_layout.addWidget(self.ua_text)
        ua_buttons = QHBoxLayout()
        self.ua_default_btn = QPushButton("Defaults")
        self.ua_load_btn = QPushButton("Load File...")
        self.ua_clear_btn = QPushButton("Clear")
        for button in (self.ua_default_btn, self.ua_load_btn, self.ua_clear_btn):
            ua_buttons.addWidget(button)
        ua_layout.addLayout(ua_buttons)
        layout.addWidget(ua_box, 1)

        # Proxies
        proxy_box = QGroupBox("Proxies")
        proxy_layout = QVBoxLayout(proxy_box)
        self.proxy_text = QPlainTextEdit()
        self.proxy_text.setPlaceholderText(
            "ip:port\nhttp://ip:port\nhttps://user:pass@ip:port\nsocks5://ip:port"
        )
        proxy_layout.addWidget(self.proxy_text)
        proxy_buttons = QHBoxLayout()
        self.proxy_load_btn = QPushButton("Load Proxy List")
        self.proxy_clear_btn = QPushButton("Clear")
        self.proxy_test_btn = QPushButton("Test Proxies")
        for button in (self.proxy_load_btn, self.proxy_clear_btn, self.proxy_test_btn):
            proxy_buttons.addWidget(button)
        proxy_layout.addLayout(proxy_buttons)
        layout.addWidget(proxy_box, 1)
        return container

    # -- log --------------------------------------------------------------- #
    def _build_log_box(self) -> QGroupBox:
        box = QGroupBox("Activity Log")
        layout = QVBoxLayout(box)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(3000)
        self.log_view.setFont(QFont("Consolas", 9))
        layout.addWidget(self.log_view)
        return box

    # -- footer ------------------------------------------------------------ #
    def _build_footer(self) -> QHBoxLayout:
        layout = QHBoxLayout()

        self.ack_cb = QCheckBox("I confirm this is authorized testing")
        self.ack_cb.setToolTip(DISCLAIMER)
        layout.addWidget(self.ack_cb)

        self.start_btn = QPushButton("START")
        self.start_btn.setObjectName("startButton")
        self.start_btn.setMinimumHeight(34)
        self.start_btn.setMinimumWidth(110)
        self.start_btn.setStyleSheet(
            "QPushButton#startButton {background:#238636;color:white;font-weight:bold;"
            "border-radius:4px;} QPushButton#startButton:disabled {background:#30363d;color:#6e7681;}"
        )
        self.start_btn.setEnabled(False)
        layout.addWidget(self.start_btn)

        self.stop_btn = QPushButton("STOP")
        self.stop_btn.setObjectName("stopButton")
        self.stop_btn.setMinimumHeight(34)
        self.stop_btn.setMinimumWidth(110)
        self.stop_btn.setStyleSheet(
            "QPushButton#stopButton {background:#8b2c2c;color:white;font-weight:bold;"
            "border-radius:4px;} QPushButton#stopButton:disabled {background:#30363d;color:#6e7681;}"
        )
        self.stop_btn.setEnabled(False)
        layout.addWidget(self.stop_btn)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("%v / %m")
        self.progress.setMinimumHeight(22)
        layout.addWidget(self.progress, 1)

        self.stats_label = QLabel("Ready - 0 / 0 visits, 0 ok, 0 failed")
        layout.addWidget(self.stats_label)

        self.ip_label = QLabel("Unique IPs: 0")
        self.ip_label.setToolTip("Distinct exit IPs observed during this run")
        layout.addWidget(self.ip_label)

        disclaimer = QLabel("Use responsibly - " + DISCLAIMER)
        disclaimer.setWordWrap(True)
        disclaimer.setStyleSheet("color:#6e7681;font-size:11px;")
        disclaimer.setMaximumHeight(34)
        layout.addWidget(disclaimer, 1)
        return layout

    # ------------------------------------------------------------ signals #
    def _connect_signals(self) -> None:
        self.start_btn.clicked.connect(self.start_run)
        self.stop_btn.clicked.connect(self.stop_run)
        self.ack_cb.toggled.connect(self._update_start_state)

        self.install_btn.clicked.connect(self.install_browsers)
        self.save_cfg_btn.clicked.connect(self.save_settings)
        self.load_cfg_btn.clicked.connect(self.load_settings_dialog)

        self.ref_default_btn.clicked.connect(
            lambda: self.ref_text.setPlainText("\n".join(DEFAULT_REFERRERS))
        )
        self.ref_clear_btn.clicked.connect(self.ref_text.clear)
        self.ref_load_btn.clicked.connect(lambda: self._load_into(self.ref_text))

        self.ua_default_btn.clicked.connect(
            lambda: self.ua_text.setPlainText("\n".join(DEFAULT_USER_AGENTS))
        )
        self.ua_clear_btn.clicked.connect(self.ua_text.clear)
        self.ua_load_btn.clicked.connect(lambda: self._load_into(self.ua_text))

        self.proxy_clear_btn.clicked.connect(self.proxy_text.clear)
        self.proxy_load_btn.clicked.connect(self.load_proxy_list)
        self.proxy_test_btn.clicked.connect(self.test_proxies)

        self.bridge.progress_signal.connect(self._on_progress)
        self.bridge.finished_signal.connect(self._on_finished)
        self.bridge.control_signal.connect(self._on_control)

        self._log_timer = QTimer(self)
        self._log_timer.timeout.connect(self._drain_log)
        self._log_timer.start(120)

    # ---------------------------------------------------------- settings #
    def _load_settings(self) -> None:
        payload = load_config()
        self._loaded_config = payload
        self.url_edit.setText(payload.get("url", ""))
        self.visits_spin.setValue(int(payload.get("visits", 50)))
        self.threads_spin.setValue(int(payload.get("threads", 10)))

        engine = payload.get("engine", BrowserChoice.HEADLESS.value)
        self.chrome_radio.setChecked(engine == BrowserChoice.CHROME.value)
        self.firefox_radio.setChecked(engine == BrowserChoice.FIREFOX.value)
        self.headless_radio.setChecked(engine == BrowserChoice.HEADLESS.value)

        self.use_ref_cb.setChecked(bool(payload.get("use_referrers", True)))
        self.use_ua_cb.setChecked(bool(payload.get("use_user_agents", True)))
        self.use_proxy_cb.setChecked(bool(payload.get("use_proxies", True)))

        self.ref_text.setPlainText("\n".join(payload.get("referrers", DEFAULT_REFERRERS)))
        self.ua_text.setPlainText("\n".join(payload.get("user_agents", DEFAULT_USER_AGENTS)))
        self.proxy_text.setPlainText("\n".join(payload.get("proxies", [])))

        self.min_stay_spin.setValue(float(payload.get("min_stay", 5.0)))
        self.max_stay_spin.setValue(float(payload.get("max_stay", 15.0)))
        self.verify_ip_cb.setChecked(bool(payload.get("verify_exit_ip", True)))
        self.robots_cb.setChecked(bool(payload.get("respect_robots", True)))
        self.internal_cb.setChecked(bool(payload.get("click_internal_links", False)))
        self.max_internal_spin.setValue(int(payload.get("max_internal_navs", 1)))
        self.click_ads_cb.setChecked(bool(payload.get("click_ads", False)))
        self.max_ad_spin.setValue(int(payload.get("max_ad_clicks", 1)))
        self.fast_cb.setChecked(bool(payload.get("fast_mode", False)))
        self.speed_spin.setValue(float(payload.get("speed", 1.0)))

    def collect_settings(self) -> Dict[str, Any]:
        engine = BrowserChoice.CHROME
        if self.firefox_radio.isChecked():
            engine = BrowserChoice.FIREFOX
        elif self.headless_radio.isChecked():
            engine = BrowserChoice.HEADLESS

        return {
            "url": self.url_edit.text().strip(),
            "visits": self.visits_spin.value(),
            "threads": self.threads_spin.value(),
            "engine": engine.value,
            "use_referrers": self.use_ref_cb.isChecked(),
            "use_user_agents": self.use_ua_cb.isChecked(),
            "use_proxies": self.use_proxy_cb.isChecked(),
            "referrers": read_lines(self.ref_text.toPlainText()),
            "user_agents": read_lines(self.ua_text.toPlainText()),
            "proxies": read_lines(self.proxy_text.toPlainText()),
            "min_stay": self.min_stay_spin.value(),
            "max_stay": self.max_stay_spin.value(),
            "verify_exit_ip": self.verify_ip_cb.isChecked(),
            "respect_robots": self.robots_cb.isChecked(),
            "click_internal_links": self.internal_cb.isChecked(),
            "max_internal_navs": self.max_internal_spin.value(),
            "click_ads": self.click_ads_cb.isChecked(),
            "max_ad_clicks": self.max_ad_spin.value(),
            "fast_mode": self.fast_cb.isChecked(),
            "speed": self.speed_spin.value(),
        }

    def save_settings(self, silent: bool = False) -> bool:
        payload = self.collect_settings()
        ok = save_config(payload)
        if not silent:
            if ok:
                self._append_log("SYSTEM", f"Configuration saved to {CONFIG_FILE}")
            else:
                QMessageBox.warning(self, "Save failed", f"Could not write {CONFIG_FILE}")
        return ok

    def load_settings_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load configuration", str(Path.home()), "JSON files (*.json);;All files (*)"
        )
        if not path:
            return
        payload = load_config(Path(path))
        self._loaded_config = payload
        self.url_edit.setText(payload.get("url", ""))
        self.visits_spin.setValue(int(payload.get("visits", 50)))
        self.threads_spin.setValue(int(payload.get("threads", 10)))
        self.ref_text.setPlainText("\n".join(payload.get("referrers", DEFAULT_REFERRERS)))
        self.ua_text.setPlainText("\n".join(payload.get("user_agents", DEFAULT_USER_AGENTS)))
        self.proxy_text.setPlainText("\n".join(payload.get("proxies", [])))
        self._append_log("SYSTEM", f"Configuration loaded from {path}")

    # ------------------------------------------------------- file helpers #
    def _load_into(self, target: QPlainTextEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load list", str(Path.home()), "Text files (*.txt);;All files (*)"
        )
        if not path:
            return
        try:
            lines = read_list_file(path)
        except Exception as exc:
            QMessageBox.warning(self, "Load failed", f"Could not read {path}:\n{exc}")
            return
        target.setPlainText("\n".join(lines))
        self._append_log("SYSTEM", f"Loaded {len(lines)} entries from {path}")

    def load_proxy_list(self) -> None:
        self._load_into(self.proxy_text)

    # ------------------------------------------------------- proxy testing #
    def test_proxies(self) -> None:
        from ..proxy_manager import health_check_all, parse_proxy_list

        entries, errors = parse_proxy_list(read_lines(self.proxy_text.toPlainText()))
        for error in errors:
            self.log_bus.warn(f"Skipped invalid proxy line: {error}")
        if not entries:
            self.log_bus.error("No valid proxies in the list.")
            return

        self.proxy_test_btn.setEnabled(False)
        self.proxy_test_btn.setText("Testing...")
        self.log_bus.info(f"Testing {len(entries)} proxies...")

        def worker() -> None:
            # Results are pushed through the log bus, the QTimer drains them
            # onto the GUI thread so no Qt call happens off-thread.
            health_check_all(
                entries,
                timeout=12.0,
                on_result=lambda entry: self.log_bus.log(
                    "INFO" if entry.healthy else "WARN",
                    f"Proxy [{entry.display}] - "
                    + (
                        f"OK, exit IP {entry.exit_ip}"
                        if entry.healthy
                        else f"FAILED: {entry.last_error}"
                    ),
                ),
            )
            healthy = [e for e in entries if e.healthy]
            self.log_bus.info(
                f"Proxy test finished - {len(healthy)}/{len(entries)} healthy."
            )
            self.bridge.control_signal.emit("proxy_test_done")

        threading.Thread(target=worker, name="proxy-test", daemon=True).start()

    # --------------------------------------------------------- environment #
    def _check_environment(self) -> None:
        if not browser_setup.playwright_available():
            self.env_label.setText("Playwright missing - pip install playwright")
            self._append_log(
                "ERROR",
                "The 'playwright' package is not installed. Run: pip install playwright",
            )
            return
        if not browser_setup.is_installed(self._selected_engine_value()):
            self.env_label.setText("Browser binaries missing - click 'Install Browsers'")
            self._append_log(
                "WARN",
                "Playwright browser binaries are missing. Click 'Install Browsers'.",
            )
        else:
            self.env_label.setText("")

    def _selected_engine_value(self) -> str:
        if self.firefox_radio.isChecked():
            return "firefox"
        if self.headless_radio.isChecked():
            return "headless"
        return "chrome"

    def install_browsers(self) -> None:
        engine = self._selected_engine_value()
        self.install_btn.setEnabled(False)
        self.log_bus.info(f"Installing Playwright browsers for {engine}...")

        def worker() -> None:
            ok, message = browser_setup.install_browsers(
                engine, on_output=lambda line: self.log_bus.info(line)
            )
            self.log_bus.log("INFO" if ok else "ERROR", message)
            self.bridge.control_signal.emit("install_done")

        threading.Thread(target=worker, name="browser-install", daemon=True).start()

    # ------------------------------------------------------------- running #
    def _update_start_state(self) -> None:
        running = self.engine is not None and self.engine.running
        self.start_btn.setEnabled(bool(self.ack_cb.isChecked()) and not running)
        self.stop_btn.setEnabled(running)

    def _build_config(self) -> RunConfig:
        payload = self.collect_settings()
        payload["proxy_timeout"] = 12.0
        payload["navigation_timeout"] = 30.0
        payload["ad_click_delay"] = 1.5
        return config_to_run_config(payload)

    def start_run(self) -> None:
        if self.engine is not None and self.engine.running:
            return

        config = self._build_config()
        problems = config.validate()
        if problems:
            QMessageBox.warning(self, "Cannot start", "\n".join(problems))
            return

        if not browser_setup.playwright_available():
            QMessageBox.critical(
                self,
                "Playwright missing",
                "Install it first:\n\npip install playwright\npython -m playwright install",
            )
            return

        self.log_bus.clear()
        self.log_view.clear()
        self.progress.setRange(0, config.visits)
        self.progress.setValue(0)
        self.stats_label.setText(f"Running - 0 / {config.visits} visits")
        self.ip_label.setText("Unique IPs: 0")

        self.engine = TrafficEngine(
            config,
            log=self.log_bus,
            on_progress=self._emit_progress,
            on_finished=self._emit_finished,
        )
        self._append_log(
            "SYSTEM",
            f"Target: {config.url} | Visits: {config.visits} | Threads: {config.threads} "
            f"| Browser: {config.engine.label}",
        )
        if config.use_proxies:
            self._append_log(
                "SYSTEM",
                f"Proxy rotation: round-robin over {len(config.proxies)} entries, "
                "one distinct proxy per visit.",
            )
        if config.use_user_agents:
            self._append_log(
                "SYSTEM",
                f"Rotating {len(config.user_agents)} User-Agents with matching viewports.",
            )
        if config.use_referrers:
            self._append_log(
                "SYSTEM", f"Rotating {len(config.referrers)} Referrers."
            )
        if config.click_ads:
            self._append_log(
                "SYSTEM",
                f"Ad click enabled - up to {config.max_ad_clicks} ad slot(s) per visit "
                "will be clicked to verify creatives and landing pages.",
            )
        if config.fast_mode:
            self._append_log(
                "SYSTEM",
                f"Fast mode - dwell capped at {min(config.max_stay, 3.0)}s, "
                "exit-IP check disabled.",
            )

        if not self.engine.start():
            self.engine = None
            QMessageBox.warning(self, "Cannot start", "The engine refused to start. See the log.")
            return

        self._update_start_state()
        self.stop_btn.setEnabled(True)
        self.save_settings(silent=True)

    def stop_run(self) -> None:
        if self.engine is not None:
            self.engine.stop()
            self._append_log("SYSTEM", "Stop requested.")
            self.stop_btn.setEnabled(False)

    # ------------------------------------------------------------ callbacks #
    def _emit_progress(self, stats: RunStats) -> None:
        self.bridge.progress_signal.emit(stats.as_dict())

    def _emit_finished(self, stats: RunStats) -> None:
        self.bridge.finished_signal.emit(stats.as_dict())

    def _on_control(self, command: str) -> None:
        """Handle control messages emitted by background workers."""
        if command == "proxy_test_done":
            self.proxy_test_btn.setEnabled(True)
            self.proxy_test_btn.setText("Test Proxies")
        elif command == "install_done":
            self.install_btn.setEnabled(True)
            self._check_environment()

    def _on_progress(self, payload: Dict[str, Any]) -> None:
        total = int(payload.get("total", 0))
        completed = int(payload.get("completed", 0))
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(completed)
        self.stats_label.setText(
            f"Running - {completed} / {total} visits, "
            f"{payload.get('success', 0)} ok, {payload.get('failed', 0)} failed"
        )
        self.ip_label.setText(f"Unique IPs: {payload.get('unique_ips', 0)}")

    def _on_finished(self, payload: Dict[str, Any]) -> None:
        total = int(payload.get("total", 0))
        completed = int(payload.get("completed", 0))
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(completed)
        self.stats_label.setText(
            f"Finished - {completed} / {total} visits, "
            f"{payload.get('success', 0)} ok, {payload.get('failed', 0)} failed"
        )
        self.ip_label.setText(f"Unique IPs: {payload.get('unique_ips', 0)}")
        self._update_start_state()

    # --------------------------------------------------------------- logs #
    def _append_log(self, level: str, text: str) -> None:
        """Append a message to the log view (safe to call from any thread)."""
        color = LOG_COLORS.get(level.upper(), LOG_COLORS["INFO"])
        safe = (
            text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
        self.log_view.appendHtml(
            f'<span style="color:{color.name()}">{safe}</span>'
        )

    def _drain_log(self) -> None:
        for message in self.log_bus.drain():
            self._append_log(message.level, message.text)

    # ------------------------------------------------------------ closing #
    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if self.engine is not None and self.engine.running:
            answer = QMessageBox.question(
                self,
                "Run in progress",
                "A run is still active. Stop it and exit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.engine.stop()
            self.engine.wait(timeout=10)
        self.save_settings(silent=True)
        event.accept()
